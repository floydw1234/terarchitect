"""Agent-friendly attempt inspection helpers backed by a local git checkout."""
import os
import subprocess
from typing import Optional

from models.db import Project, TicketAttempt

from .attempt_service import SATISFIED_STATUSES

GIT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_DIFF_BYTES = 200_000


def _run_git(project_path: str, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )


def _repo_unavailable(project: Project) -> tuple[bool, str | None]:
    project_path = (project.project_path or "").strip()
    if not project_path:
        return False, "Project has no local project_path configured."
    if not os.path.isdir(project_path):
        return False, f"Project path is unavailable: {project_path}"
    probe = _run_git(project_path, ["rev-parse", "--git-dir"])
    if probe.returncode != 0:
        return False, "Project path is not a readable git repository."
    return True, None


def _commit_exists(project_path: str, commit_hash: str | None) -> bool:
    if not commit_hash:
        return False
    probe = _run_git(project_path, ["cat-file", "-e", f"{commit_hash}^{{commit}}"])
    return probe.returncode == 0


def _parse_name_status(raw: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") or status.startswith("C"):
            if len(parts) < 3:
                continue
            path = parts[2]
            result[path] = {
                "path": path,
                "status": status[0],
                "old_path": parts[1],
            }
            continue
        if len(parts) < 2:
            continue
        path = parts[1]
        result[path] = {
            "path": path,
            "status": status[:1] or status,
        }
    return result


def _parse_numstat(raw: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        additions_raw, deletions_raw, path = parts[0], parts[1], parts[2]
        result[path] = {
            "path": path,
            "additions": 0 if additions_raw == "-" else int(additions_raw),
            "deletions": 0 if deletions_raw == "-" else int(deletions_raw),
        }
    return result


def _next_actions_from_state(
    *,
    repo_available: bool,
    commit_hash: str | None,
    commit_available: bool,
    base_hash: str | None,
    base_available: bool,
) -> list[str]:
    if not commit_hash:
        return ["Re-run the agent and publish an AgentHub commit for this attempt."]
    if not repo_available:
        return ["Configure project.project_path to a local git checkout that contains the attempt commit."]
    if not commit_available:
        return ["Fetch or materialize the attempt commit into the local project git checkout."]
    if base_hash and not base_available:
        return ["Fetch the attempt base commit into the local git checkout for an exact base-to-attempt diff."]
    return []


def inspect_changed_files(project: Project, attempt: TicketAttempt) -> dict:
    repo_available, repo_reason = _repo_unavailable(project)
    commit_hash = attempt.agenthub_commit_hash
    base_hash = attempt.base_hash
    result = {
        "attempt_id": str(attempt.id),
        "commit_hash": commit_hash,
        "base_hash": base_hash,
        "git_available": repo_available,
        "commit_available": False,
        "base_available": False if base_hash else None,
        "changed_files": [],
        "unavailable_reason": repo_reason,
        "next_actions": [],
    }

    if not repo_available:
        result["next_actions"] = _next_actions_from_state(
            repo_available=repo_available,
            commit_hash=commit_hash,
            commit_available=False,
            base_hash=base_hash,
            base_available=False,
        )
        return result

    project_path = (project.project_path or "").strip()
    commit_available = _commit_exists(project_path, commit_hash)
    result["commit_available"] = commit_available
    if not commit_available:
        result["unavailable_reason"] = (
            "Attempt commit is missing from the local git checkout."
            if commit_hash
            else "Attempt has no published commit hash."
        )
        result["next_actions"] = _next_actions_from_state(
            repo_available=repo_available,
            commit_hash=commit_hash,
            commit_available=commit_available,
            base_hash=base_hash,
            base_available=False,
        )
        return result

    base_available = _commit_exists(project_path, base_hash) if base_hash else None
    result["base_available"] = base_available
    diff_args = ["diff", "--name-status", "--find-renames", base_hash, commit_hash]
    numstat_args = ["diff", "--numstat", "--find-renames", base_hash, commit_hash]
    fallback_reason = None
    if base_hash and not base_available:
        diff_args = ["show", "--format=", "--name-status", "--find-renames", commit_hash]
        numstat_args = ["show", "--format=", "--numstat", "--find-renames", commit_hash]
        fallback_reason = "Base commit is unavailable in the local checkout; showing single-commit file changes."
    elif not base_hash:
        diff_args = ["show", "--format=", "--name-status", "--find-renames", commit_hash]
        numstat_args = ["show", "--format=", "--numstat", "--find-renames", commit_hash]
        fallback_reason = "Attempt has no base hash; showing single-commit file changes."

    status_proc = _run_git(project_path, diff_args)
    numstat_proc = _run_git(project_path, numstat_args)
    if status_proc.returncode != 0 or numstat_proc.returncode != 0:
        result["unavailable_reason"] = "Git could not inspect the attempt diff in the local checkout."
        result["next_actions"] = ["Verify the local project checkout can diff the attempt commit."]
        return result

    status_map = _parse_name_status(status_proc.stdout)
    numstat_map = _parse_numstat(numstat_proc.stdout)
    paths = sorted(set(status_map) | set(numstat_map))
    result["changed_files"] = [
        {
            **status_map.get(path, {"path": path, "status": "M"}),
            **numstat_map.get(path, {"path": path, "additions": 0, "deletions": 0}),
        }
        for path in paths
    ]
    result["unavailable_reason"] = fallback_reason
    result["next_actions"] = _next_actions_from_state(
        repo_available=repo_available,
        commit_hash=commit_hash,
        commit_available=commit_available,
        base_hash=base_hash,
        base_available=bool(base_available) if base_available is not None else False,
    ) if fallback_reason else []
    return result


def inspect_diff(
    project: Project,
    attempt: TicketAttempt,
    *,
    file_path: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> dict:
    file_report = inspect_changed_files(project, attempt)
    limit = DEFAULT_MAX_DIFF_BYTES if max_bytes is None else max_bytes
    result = {
        "attempt_id": str(attempt.id),
        "commit_hash": attempt.agenthub_commit_hash,
        "base_hash": attempt.base_hash,
        "file": file_path,
        "diff": "",
        "truncated": False,
        "bytes": 0,
        "git_available": file_report["git_available"],
        "commit_available": file_report["commit_available"],
        "base_available": file_report["base_available"],
        "unavailable_reason": file_report["unavailable_reason"],
        "next_actions": list(file_report["next_actions"]),
    }
    if not file_report["git_available"] or not file_report["commit_available"]:
        return result

    project_path = (project.project_path or "").strip()
    args = ["diff", "--no-ext-diff", "--no-color", "--find-renames", attempt.base_hash, attempt.agenthub_commit_hash]
    if attempt.base_hash and not file_report["base_available"]:
        args = ["show", "--format=", "--no-color", "--find-renames", attempt.agenthub_commit_hash]
    elif not attempt.base_hash:
        args = ["show", "--format=", "--no-color", "--find-renames", attempt.agenthub_commit_hash]
    if file_path:
        args.extend(["--", file_path])

    diff_proc = _run_git(project_path, args)
    if diff_proc.returncode != 0:
        result["unavailable_reason"] = "Git could not render the attempt diff in the local checkout."
        result["next_actions"] = ["Verify the local project checkout can render this attempt diff."]
        return result

    diff_text = diff_proc.stdout or ""
    diff_bytes = diff_text.encode("utf-8")
    if limit < 0:
        limit = 0
    if len(diff_bytes) > limit:
        result["diff"] = diff_bytes[:limit].decode("utf-8", errors="ignore")
        result["truncated"] = True
        result["bytes"] = limit
    else:
        result["diff"] = diff_text
        result["bytes"] = len(diff_bytes)
    return result


def attempt_inspection_json(project: Project, attempt: TicketAttempt) -> dict:
    inspection = inspect_changed_files(project, attempt)
    ticket = getattr(attempt, "ticket", None)
    stale = (
        attempt.base_hash != project.shipped_frontier
        if getattr(project, "shipped_frontier", None) and attempt.base_hash
        else None
    )
    changed_paths = [item["path"] for item in inspection["changed_files"]]
    satisfied = attempt.status in SATISFIED_STATUSES
    accepted = attempt.status == "accepted"
    return {
        "id": str(attempt.id),
        "attempt_id": str(attempt.id),
        "project_id": str(attempt.project_id),
        "ticket_id": str(attempt.ticket_id),
        "ticket_title": getattr(ticket, "title", None),
        "status": attempt.status,
        "accepted": accepted,
        "satisfied": satisfied,
        "summary": attempt.summary,
        "agenthub_commit_hash": attempt.agenthub_commit_hash,
        "short_commit_hash": (attempt.agenthub_commit_hash or "")[:12] or None,
        "base_hash": attempt.base_hash,
        "wave_num": attempt.wave_num,
        "attempt_num": attempt.attempt_num,
        "agent_id": attempt.agent_id,
        "test_status": attempt.test_status,
        "validation_error": attempt.validation_error,
        "stale": stale,
        "git_available": inspection["git_available"],
        "commit_available": inspection["commit_available"],
        "base_available": inspection["base_available"],
        "changed_files": changed_paths,
        "unavailable_reason": inspection["unavailable_reason"],
        "next_actions": inspection["next_actions"],
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }
