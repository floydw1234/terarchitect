"""Project-scoped AgentHub DAG aggregation for the frontend."""

from __future__ import annotations

import os
from typing import Any

import requests

from models.db import Project, Ticket, TicketAttempt

from .channel_service import parse_event_post, project_channel, ticket_channel, wave_channel
from .merge_service import compute_waves
from .project_service import project_to_json

DEFAULT_COMMIT_PAGE_SIZE = 200
DEFAULT_COMMIT_PAGES = 4
DEFAULT_POST_LIMIT = 10
DEFAULT_CHANNEL_LIMIT = 12
REQUEST_TIMEOUT = 12


def _truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def agenthub_read_auth_disabled() -> bool:
    return _truthy_env(os.environ.get("AGENTHUB_AUTH_DISABLED"))


def agenthub_auth_headers(api_key: str | None) -> dict[str, str]:
    key = (api_key or "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def agenthub_connection_from_env() -> tuple[str, str]:
    return (
        (os.environ.get("AGENTHUB_URL") or "").strip().rstrip("/"),
        (os.environ.get("AGENTHUB_API_KEY") or "").strip(),
    )


class AgenthubGraphServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, guidance: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.guidance = guidance


def _agenthub_get(
    session: requests.Session,
    base_url: str,
    api_key: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> Any:
    response = session.get(
        f"{base_url}{path}",
        params=params or None,
        headers=agenthub_auth_headers(api_key) or None,
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code in (401, 403):
        raise AgenthubGraphServiceError(
            "agenthub_auth_required",
            "Backend could not read AgentHub. Set AGENTHUB_API_KEY in backend .env or enable dev auth bypass with AGENTHUB_AUTH_DISABLED=1 on AgentHub.",
            guidance="Set AGENTHUB_API_KEY in backend .env or enable dev auth bypass with AGENTHUB_AUTH_DISABLED=1 on AgentHub.",
        )
    if response.status_code >= 400:
        message = response.text.strip()[:300] or f"AgentHub returned HTTP {response.status_code}."
        raise AgenthubGraphServiceError("agenthub_http_error", message)
    return response.json()


def _sorted_unique(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _project_attempt_hashes(project_id) -> list[str]:
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project_id)
        .order_by(TicketAttempt.created_at.desc(), TicketAttempt.attempt_num.desc())
        .all()
    )
    return [attempt.agenthub_commit_hash for attempt in attempts if attempt.agenthub_commit_hash]


def _project_related_channels(project: Project) -> list[str]:
    tickets = Ticket.query.filter_by(project_id=project.id).all()
    channel_names = {project_channel(str(project.id))}

    if tickets:
        waves = compute_waves(tickets)
        wave_nums = sorted({waves.get(str(ticket.id), 0) for ticket in tickets})
        for ticket in tickets:
            channel_names.add(ticket_channel(str(ticket.id)))
        for wave_num in wave_nums:
            channel_names.add(wave_channel(project.name, wave_num))

    attempts = TicketAttempt.query.filter_by(project_id=project.id).all()
    for attempt in attempts:
        if attempt.wave_num is not None:
            channel_names.add(wave_channel(project.name, attempt.wave_num))

    return sorted(channel_names)


def _collect_anchor_hashes(project: Project) -> dict[str, list[str]]:
    frontier_hashes = _sorted_unique([
        getattr(project, "accepted_frontier_id", None) or "",
        getattr(project, "shipped_frontier", None) or "",
    ])
    root_hashes = _sorted_unique([
        getattr(project, "github_resolved_sha", None) or "",
    ])
    attempt_hashes = _sorted_unique(_project_attempt_hashes(project.id))
    anchor_hashes = _sorted_unique(frontier_hashes + root_hashes + attempt_hashes)
    return {
        "anchor_hashes": anchor_hashes,
        "frontier_hashes": frontier_hashes,
        "root_hashes": root_hashes,
        "attempt_hashes": attempt_hashes,
    }


def _commit_index(commits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(commit.get("hash") or ""): commit
        for commit in commits
        if isinstance(commit, dict) and commit.get("hash")
    }


def _collect_recent_commits(
    session: requests.Session,
    base_url: str,
    api_key: str,
) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(DEFAULT_COMMIT_PAGES):
        batch = _agenthub_get(
            session,
            base_url,
            api_key,
            "/api/git/commits",
            params={"limit": DEFAULT_COMMIT_PAGE_SIZE, "offset": page * DEFAULT_COMMIT_PAGE_SIZE},
        )
        if not isinstance(batch, list) or not batch:
            break
        for commit in batch:
            commit_hash = str((commit or {}).get("hash") or "")
            if commit_hash and commit_hash not in seen:
                seen.add(commit_hash)
                commits.append(commit)
        if len(batch) < DEFAULT_COMMIT_PAGE_SIZE:
            break
    return commits


def _merge_commit(target: dict[str, dict[str, Any]], commit: dict[str, Any]) -> None:
    commit_hash = str((commit or {}).get("hash") or "")
    if not commit_hash:
        return
    existing = target.get(commit_hash) or {}
    merged = dict(existing)
    merged.update(commit)
    target[commit_hash] = merged


def _collect_anchor_lineage(
    session: requests.Session,
    base_url: str,
    api_key: str,
    anchor_hashes: list[str],
    commit_map: dict[str, dict[str, Any]],
) -> None:
    for anchor_hash in anchor_hashes:
        try:
            lineage = _agenthub_get(session, base_url, api_key, f"/api/git/commits/{anchor_hash}/lineage")
        except AgenthubGraphServiceError as exc:
            if exc.code == "agenthub_http_error":
                continue
            raise
        if not isinstance(lineage, list):
            continue
        for commit in lineage:
            if isinstance(commit, dict):
                _merge_commit(commit_map, commit)


def _filter_project_commits(commits: list[dict[str, Any]], anchor_hashes: list[str]) -> list[dict[str, Any]]:
    if not anchor_hashes:
        return []
    commit_map = _commit_index(commits)
    included: set[str] = set()
    frontier = list(anchor_hashes)
    while frontier:
        current_hash = frontier.pop()
        if current_hash in included:
            continue
        commit = commit_map.get(current_hash)
        if not commit:
            continue
        included.add(current_hash)
        parent_hash = str(commit.get("parent_hash") or "")
        if parent_hash and parent_hash not in included:
            frontier.append(parent_hash)
    filtered = [commit for commit in commits if str(commit.get("hash") or "") in included]
    filtered.sort(key=lambda commit: str(commit.get("created_at") or ""), reverse=True)
    return filtered


def _project_leaves(
    commits: list[dict[str, Any]],
    global_leaves: list[dict[str, Any]],
    anchor_hashes: list[str],
) -> list[dict[str, Any]]:
    if not commits:
        return []
    commit_map = _commit_index(commits)
    parent_hashes = {
        str(commit.get("parent_hash") or "")
        for commit in commits
        if commit.get("parent_hash")
    }
    leaves_by_hash = _commit_index(global_leaves)
    scoped: list[dict[str, Any]] = []
    seen: set[str] = set()

    for anchor_hash in anchor_hashes:
        if anchor_hash in commit_map and anchor_hash not in parent_hashes and anchor_hash not in seen:
            scoped.append(commit_map[anchor_hash])
            seen.add(anchor_hash)

    for commit_hash, commit in commit_map.items():
        if commit_hash in leaves_by_hash and commit_hash not in seen:
            scoped.append(commit)
            seen.add(commit_hash)

    scoped.sort(key=lambda commit: str(commit.get("created_at") or ""), reverse=True)
    return scoped


def _root_hashes(commits: list[dict[str, Any]]) -> list[str]:
    commit_hashes = {str(commit.get("hash") or "") for commit in commits}
    return [
        str(commit.get("hash") or "")
        for commit in commits
        if not commit.get("parent_hash") or str(commit.get("parent_hash") or "") not in commit_hashes
    ]


def _related_channels_and_posts(
    session: requests.Session,
    base_url: str,
    api_key: str,
    project: Project,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    related_names = set(_project_related_channels(project))
    if not related_names:
        return [], []

    all_channels = _agenthub_get(session, base_url, api_key, "/api/channels")
    if not isinstance(all_channels, list):
        return [], []

    channels = [
        channel for channel in all_channels
        if isinstance(channel, dict) and str(channel.get("name") or "") in related_names
    ]
    channels.sort(key=lambda channel: str(channel.get("name") or ""))
    channels = channels[:DEFAULT_CHANNEL_LIMIT]

    posts: list[dict[str, Any]] = []
    for channel in channels:
        channel_name = str(channel.get("name") or "")
        if not channel_name:
            continue
        try:
            channel_posts = _agenthub_get(
                session,
                base_url,
                api_key,
                f"/api/channels/{channel_name}/posts",
                params={"limit": DEFAULT_POST_LIMIT},
            )
        except AgenthubGraphServiceError as exc:
            if exc.code == "agenthub_http_error":
                continue
            raise
        if not isinstance(channel_posts, list):
            continue
        for post in channel_posts:
            if not isinstance(post, dict):
                continue
            normalized = parse_event_post(post)
            normalized["channel_name"] = channel_name
            posts.append(normalized)

    posts.sort(key=lambda post: str(post.get("created_at") or ""), reverse=True)
    return channels, posts


def build_project_agenthub_graph(project: Project) -> dict[str, Any]:
    base_url, api_key = agenthub_connection_from_env()
    status = {
        "code": "ok",
        "online": True,
        "auth_configured": bool(api_key),
        "auth_mode": "backend_api_key" if api_key else "unauthenticated",
        "project_scoped": True,
        "message": None,
        "guidance": None,
    }
    scope = _collect_anchor_hashes(project)
    response = {
        "project": project_to_json(project),
        "status": status,
        "scope": {
            **scope,
            "channel_names": _project_related_channels(project),
        },
        "graph": {
            "commits": [],
            "nodes": [],
            "leaves": [],
            "channels": [],
            "posts": [],
            "root_hashes": [],
        },
    }

    if not scope["anchor_hashes"]:
        status.update({
            "code": "no_project_hashes",
            "message": "This project has no accepted frontier, shipped frontier, source SHA, or recorded attempt hashes yet.",
        })
        return response

    if not base_url:
        status.update({
            "code": "agenthub_not_configured",
            "online": False,
            "message": "AGENTHUB_URL is not configured in the backend runtime.",
            "guidance": "Set AGENTHUB_URL in backend .env/runtime so Terarchitect can proxy AgentHub data.",
        })
        return response

    try:
        with requests.Session() as session:
            commits = _collect_recent_commits(session, base_url, api_key)
            commit_map = _commit_index(commits)
            _collect_anchor_lineage(session, base_url, api_key, scope["anchor_hashes"], commit_map)
            all_commits = list(commit_map.values())
            filtered_commits = _filter_project_commits(all_commits, scope["anchor_hashes"])
            global_leaves = _agenthub_get(session, base_url, api_key, "/api/git/leaves")
            channels, posts = _related_channels_and_posts(session, base_url, api_key, project)
    except AgenthubGraphServiceError as exc:
        status.update({
            "code": exc.code,
            "online": exc.code != "agenthub_not_configured",
            "message": exc.message,
            "guidance": exc.guidance,
        })
        return response
    except requests.RequestException:
        status.update({
            "code": "agenthub_unreachable",
            "online": False,
            "message": f"AgentHub is not reachable at {base_url}.",
            "guidance": "Start AgentHub or correct AGENTHUB_URL in backend .env/runtime.",
        })
        return response

    leaves = _project_leaves(
        filtered_commits,
        global_leaves if isinstance(global_leaves, list) else [],
        scope["anchor_hashes"],
    )
    response["graph"] = {
        "commits": filtered_commits,
        "nodes": filtered_commits,
        "leaves": leaves,
        "channels": channels,
        "posts": posts,
        "root_hashes": _root_hashes(filtered_commits),
    }
    if not filtered_commits:
        status.update({
            "code": "no_project_commits_visible",
            "message": "Project anchor hashes are known, but no related commits are visible from AgentHub yet.",
        })
    elif not api_key and agenthub_read_auth_disabled():
        status["message"] = "Using AgentHub read-only dev bypass from the backend runtime."
    return response
