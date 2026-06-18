"""Publish integrated-winner AgentHub commits to downstream targets."""

from __future__ import annotations

import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import requests

from models.db import Project, TicketAttempt, db

try:
    from utils.app_settings import get_dashboard_git_env
except (ModuleNotFoundError, ImportError):
    from backend.utils.app_settings import get_dashboard_git_env

try:
    from .github_service import normalize_github_repo_url
except (ModuleNotFoundError, ImportError):
    from backend.api.services.github_service import normalize_github_repo_url

from .agenthub_import_service import agenthub_connection_from_env
from .attempt_service import attempt_is_integrated_winner


class PublishError(RuntimeError):
    """Raised when publish preconditions or execution fail."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 409,
        detail: str | None = None,
        phase: str | None = None,
        hint: str | None = None,
        next_commands: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.phase = phase
        self.hint = hint
        self.next_commands = list(next_commands or [])


@dataclass
class PublishSelection:
    commit_hash: str
    attempt: TicketAttempt | None
    source: str


class Publisher(Protocol):
    def publish(
        self,
        project: Project,
        *,
        selection: PublishSelection,
        branch: str | None,
        push: bool,
        force: bool,
    ) -> dict[str, Any]:
        ...


def _env_for_publish_git() -> dict[str, str]:
    return {**os.environ, **get_dashboard_git_env()}


def _serialize_result(
    result: subprocess.CompletedProcess[str],
    *,
    cmd: list[str],
    cwd: str,
) -> dict[str, Any]:
    return {
        "cmd": cmd,
        "cwd": cwd,
        "returncode": result.returncode,
        "stdout": (result.stdout or "").strip(),
        "stderr": (result.stderr or "").strip(),
    }


def _run_git(
    commands: list[dict[str, Any]],
    repo_path: str,
    args: list[str],
    *,
    check: bool = True,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_env_for_publish_git(),
    )
    commands.append(_serialize_result(result, cmd=["git", *args], cwd=repo_path))
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or "git command failed"
        raise PublishError(
            "Publish git command failed",
            status_code=502,
            detail=detail[:1000],
            phase="git",
        )
    return result


def _resolve_branch(project: Project, repo_path: str, commands: list[dict[str, Any]], override: str | None) -> str:
    if override:
        return override
    configured = (project.github_ref or "").strip()
    if configured:
        return configured
    head = _run_git(commands, repo_path, ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], check=False)
    ref = (head.stdout or "").strip()
    if ref.startswith("origin/"):
        return ref.split("/", 1)[1]
    return "main"


def _ensure_remote_target(project: Project) -> str:
    github_url = (project.github_url or "").strip()
    if not github_url:
        raise PublishError(
            "github_url is required for GitHub publish",
            status_code=409,
            phase="project",
            hint="Set project.github_url before publishing.",
        )
    return github_url


@contextmanager
def _prepare_publish_repo(
    project: Project,
    commands: list[dict[str, Any]],
):
    requested_project_path = os.path.abspath((project.project_path or "").strip()) if (project.project_path or "").strip() else None
    if requested_project_path and os.path.isdir(requested_project_path):
        yield {
            "repo_path": requested_project_path,
            "requested_project_path": requested_project_path,
            "ephemeral_repo": False,
            "repo_source": "project_path",
            "cache_source": "project_path",
        }
        return

    github_url = _ensure_remote_target(project)
    with tempfile.TemporaryDirectory(prefix="terarchitect-publish-repo-") as tmp_dir:
        repo_path = os.path.join(tmp_dir, "repo")
        result = subprocess.run(
            ["git", "clone", github_url, repo_path],
            capture_output=True,
            text=True,
            timeout=300,
            env=_env_for_publish_git(),
        )
        commands.append(_serialize_result(result, cmd=["git", "clone", github_url, repo_path], cwd=tmp_dir))
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or "git clone failed"
            raise PublishError(
                "Failed to clone ephemeral GitHub repo for publish",
                status_code=502,
                detail=detail[:1000],
                phase="project",
            )
        if not os.path.isdir(repo_path):
            raise PublishError(
                "git clone completed but ephemeral publish repo is unavailable",
                status_code=502,
                detail=repo_path,
                phase="project",
            )
        yield {
            "repo_path": repo_path,
            "requested_project_path": requested_project_path,
            "ephemeral_repo": True,
            "repo_source": "github_ephemeral_clone",
            "cache_source": "github_url",
        }


def _latest_integrated_winner_attempt(project_id) -> TicketAttempt | None:
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project_id)
        .order_by(TicketAttempt.created_at.desc(), TicketAttempt.attempt_num.desc())
        .all()
    )
    for attempt in attempts:
        if attempt_is_integrated_winner(attempt):
            return attempt
    return None


def _resolve_selection(project: Project, *, attempt_id: str | None, commit_hash: str | None) -> PublishSelection:
    selected_attempt: TicketAttempt | None = None
    commit = (commit_hash or "").strip() or None
    source = "accepted_frontier"

    if attempt_id:
        selected_attempt = TicketAttempt.query.filter_by(project_id=project.id, id=attempt_id).first()
        if selected_attempt is None:
            raise PublishError(
                "Integrated winner attempt not found",
                status_code=404,
                phase="selection",
                detail=f"attempt_id={attempt_id}",
            )
        if not attempt_is_integrated_winner(selected_attempt):
            raise PublishError(
                "Selected attempt is not an integrated winner",
                status_code=409,
                phase="selection",
                detail=f"attempt_id={attempt_id} status={selected_attempt.status}",
                hint="Choose an attempt that has both winner selection and integration recorded.",
            )
        if not (selected_attempt.agenthub_commit_hash or "").strip():
            raise PublishError(
                "Integrated winner attempt has no AgentHub commit hash",
                status_code=409,
                phase="selection",
                detail=f"attempt_id={attempt_id}",
            )
        if commit and commit != selected_attempt.agenthub_commit_hash:
            raise PublishError(
                "Explicit commit does not match selected attempt",
                status_code=409,
                phase="selection",
            )
        commit = selected_attempt.agenthub_commit_hash
        source = "attempt"
    elif commit:
        attempts = (
            TicketAttempt.query
            .filter_by(project_id=project.id, agenthub_commit_hash=commit)
            .order_by(TicketAttempt.created_at.desc(), TicketAttempt.attempt_num.desc())
            .all()
        )
        selected_attempt = next((attempt for attempt in attempts if attempt_is_integrated_winner(attempt)), None)
        if selected_attempt is not None:
            source = "attempt"
        elif commit != (project.accepted_frontier_id or "").strip():
            raise PublishError(
                "Explicit commit is not an integrated winner or current project frontier commit",
                status_code=409,
                phase="selection",
                hint="Use --attempt-id or a commit from an integrated winner attempt.",
            )
    else:
        selected_attempt = _latest_integrated_winner_attempt(project.id)
        if selected_attempt is not None and (selected_attempt.agenthub_commit_hash or "").strip():
            commit = selected_attempt.agenthub_commit_hash
            source = "attempt"
        else:
            commit = (project.accepted_frontier_id or "").strip() or None

    if not commit:
        raise PublishError(
            "No integrated winner AgentHub commit is available to publish",
            status_code=409,
            phase="selection",
            hint="Integrate a winner attempt first or set project.accepted_frontier_id.",
        )
    return PublishSelection(commit_hash=commit, attempt=selected_attempt, source=source)


def _fetch_agenthub_bundle(commit_hash: str, dest_dir: str) -> str:
    base_url, api_key = agenthub_connection_from_env()
    try:
        response = requests.get(
            f"{base_url}/api/git/fetch/{commit_hash}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
            stream=True,
        )
    except requests.RequestException as exc:
        raise PublishError(
            f"Failed to fetch AgentHub commit {commit_hash[:12]}",
            status_code=502,
            detail=str(exc),
            phase="materialize",
        ) from exc
    if not response.ok:
        raise PublishError(
            f"AgentHub fetch failed for commit {commit_hash[:12]}",
            status_code=502,
            detail=f"status={response.status_code}",
            phase="materialize",
        )
    bundle_path = os.path.join(dest_dir, f"{commit_hash[:12]}.bundle")
    with open(bundle_path, "wb") as handle:
        for chunk in response.iter_content(chunk_size=8192):
            handle.write(chunk)
    return bundle_path


def _ensure_commit_materialized(repo_path: str, commit_hash: str, commands: list[dict[str, Any]]) -> None:
    probe = _run_git(commands, repo_path, ["cat-file", "-e", f"{commit_hash}^{{commit}}"], check=False)
    if probe.returncode == 0:
        return
    try:
        with tempfile.TemporaryDirectory(prefix="terarchitect-publish-") as tmp_dir:
            bundle_path = _fetch_agenthub_bundle(commit_hash, tmp_dir)
            _run_git(commands, repo_path, ["bundle", "unbundle", bundle_path], timeout=120)
    except PublishError:
        raise
    verify = _run_git(commands, repo_path, ["cat-file", "-e", f"{commit_hash}^{{commit}}"], check=False)
    if verify.returncode != 0:
        raise PublishError(
            "Selected commit is not available in the local project repo after materialization",
            status_code=502,
            phase="materialize",
            detail=f"commit={commit_hash}",
        )


def _require_clean_repo(repo_path: str, commands: list[dict[str, Any]]) -> None:
    status = _run_git(commands, repo_path, ["status", "--porcelain", "--untracked-files=normal"], check=False)
    if (status.stdout or "").strip():
        raise PublishError(
            "Target repo is dirty; refusing publish",
            status_code=409,
            phase="preflight",
            hint="Commit, stash, or clean the target repo before publishing.",
            detail=(status.stdout or "").strip()[:1000],
        )


def _verify_origin_matches_project_remote(
    project: Project,
    repo_path: str,
    commands: list[dict[str, Any]],
) -> str:
    configured_remote = _ensure_remote_target(project)
    actual_remote_result = _run_git(commands, repo_path, ["remote", "get-url", "origin"], check=False)
    actual_remote = (actual_remote_result.stdout or "").strip()
    if actual_remote_result.returncode != 0 or not actual_remote:
        raise PublishError(
            "git remote origin is not configured for project_path",
            status_code=409,
            phase="project",
            detail=(actual_remote_result.stderr or actual_remote_result.stdout or "").strip()[:1000] or repo_path,
        )

    configured_slug = normalize_github_repo_url(configured_remote)
    actual_slug = normalize_github_repo_url(actual_remote)
    if not configured_slug or not actual_slug or configured_slug != actual_slug:
        raise PublishError(
            "project_path git remote origin does not match project.github_url",
            status_code=409,
            phase="project",
            detail=f"project.github_url={configured_remote} origin={actual_remote}",
            hint="Point origin at the configured GitHub repo or update project.github_url before publishing.",
        )
    return actual_remote


class GitHubPublisher:
    target = "github"

    def publish(
        self,
        project: Project,
        *,
        selection: PublishSelection,
        branch: str | None,
        push: bool,
        force: bool,
    ) -> dict[str, Any]:
        remote_url = _ensure_remote_target(project)
        commands: list[dict[str, Any]] = []
        with _prepare_publish_repo(project, commands) as repo_info:
            repo_path = repo_info["repo_path"]
            git_dir = _run_git(commands, repo_path, ["rev-parse", "--git-dir"], check=False)
            if git_dir.returncode != 0:
                raise PublishError(
                    "Publish repo is not a readable git repository",
                    status_code=409,
                    phase="project",
                    detail=(git_dir.stderr or git_dir.stdout or "").strip()[:1000],
                )
            remote_url = _verify_origin_matches_project_remote(project, repo_path, commands)
            _require_clean_repo(repo_path, commands)
            target_branch = _resolve_branch(project, repo_path, commands, branch)
            _ensure_commit_materialized(repo_path, selection.commit_hash, commands)
            _run_git(commands, repo_path, ["fetch", "origin", target_branch], check=False, timeout=120)

            remote_ref = f"refs/remotes/origin/{target_branch}"
            remote_tip_result = _run_git(commands, repo_path, ["rev-parse", remote_ref], check=False)
            remote_tip = (remote_tip_result.stdout or "").strip()
            if remote_tip_result.returncode != 0 or not remote_tip:
                raise PublishError(
                    "Remote target branch is not available locally",
                    status_code=409,
                    phase="preflight",
                    detail=f"remote=origin branch={target_branch}",
                )

            ff_check = _run_git(
                commands,
                repo_path,
                ["merge-base", "--is-ancestor", remote_tip, selection.commit_hash],
                check=False,
            )
            fast_forward = ff_check.returncode == 0
            if not fast_forward and not force:
                raise PublishError(
                    "Publish would not be a fast-forward update",
                    status_code=409,
                    phase="preflight",
                    detail=f"remote_tip={remote_tip} selected_commit={selection.commit_hash}",
                    hint="Re-run with --force only after verifying the remote branch should be replaced.",
                )

            current_branch_result = _run_git(commands, repo_path, ["branch", "--show-current"], check=False)
            current_branch = (current_branch_result.stdout or "").strip() or None
            local_tip_result = _run_git(commands, repo_path, ["rev-parse", "--verify", target_branch], check=False)
            local_tip = (local_tip_result.stdout or "").strip() or None
            pushed = False

            if push:
                _run_git(commands, repo_path, ["checkout", "-B", target_branch, remote_ref], timeout=120)
                if fast_forward:
                    _run_git(commands, repo_path, ["merge", "--ff-only", selection.commit_hash], timeout=120)
                else:
                    _run_git(commands, repo_path, ["reset", "--hard", selection.commit_hash], timeout=120)
                push_args = ["push"]
                if force:
                    push_args.append(f"--force-with-lease={target_branch}:{remote_tip}")
                push_args.extend(["origin", f"HEAD:refs/heads/{target_branch}"])
                _run_git(commands, repo_path, push_args, timeout=120)
                pushed = True

            return {
                "target": self.target,
                "publish_target": self.target,
                "remote": "origin",
                "remote_url": remote_url,
                "branch": target_branch,
                "local_branch": target_branch,
                "current_branch": current_branch,
                "selected_commit": selection.commit_hash,
                "selected_attempt_id": str(selection.attempt.id) if selection.attempt else None,
                "selected_source": selection.source,
                "accepted_frontier_id": (project.accepted_frontier_id or "").strip() or None,
                "project_path": repo_path,
                "requested_project_path": repo_info["requested_project_path"],
                "ephemeral_repo": repo_info["ephemeral_repo"],
                "repo_source": repo_info["repo_source"],
                "cache_source": repo_info["cache_source"],
                "remote_tip": remote_tip,
                "local_tip_before": local_tip,
                "fast_forward": fast_forward,
                "force": force,
                "dry_run": not push,
                "pushed": pushed,
                "commands": commands,
            }


PUBLISHERS: dict[str, Publisher] = {
    "github": GitHubPublisher(),
}


def publish_project(
    project: Project,
    *,
    target: str = "github",
    attempt_id: str | None = None,
    commit_hash: str | None = None,
    branch: str | None = None,
    push: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    publisher = PUBLISHERS.get((target or "").strip().lower())
    if publisher is None:
        raise PublishError(
            "Unknown publish target",
            status_code=400,
            phase="target",
            detail=f"target={target}",
        )
    selection = _resolve_selection(project, attempt_id=attempt_id, commit_hash=commit_hash)
    result = publisher.publish(
        project,
        selection=selection,
        branch=branch,
        push=push,
        force=force,
    )
    shipped_frontier_updated = False
    if push and result.get("pushed"):
        shipped_at = datetime.now(timezone.utc)
        project.shipped_frontier = selection.commit_hash
        project.shipped_frontier_updated_at = shipped_at
        if selection.attempt is not None and attempt_is_integrated_winner(selection.attempt):
            selection.attempt.status = "shipped"
            selection.attempt.updated_at = shipped_at
        db.session.commit()
        shipped_frontier_updated = True
    result["project_id"] = str(project.id)
    result["shipped_frontier"] = project.shipped_frontier
    result["shipped_at"] = (
        project.shipped_frontier_updated_at.isoformat()
        if project.shipped_frontier_updated_at
        else None
    )
    result["shipped_frontier_updated"] = shipped_frontier_updated
    return result
