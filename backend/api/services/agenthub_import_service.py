"""Explicit local-repo import helpers for AgentHub-backed project frontiers."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import requests
from flask import current_app

from models.db import Project, db

from .project_service import validate_project_frontier_candidate


class AgenthubImportError(RuntimeError):
    """Raised when explicit local-repo import into AgentHub cannot complete."""


def _run_git(path: str, args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def read_git_head(path: str | None) -> str | None:
    """Return the current HEAD commit hash for a local repository."""
    if not path or not os.path.isdir(path):
        return None
    try:
        result = _run_git(path, ["rev-parse", "HEAD"])
    except Exception:
        return None
    if result.returncode != 0:
        return None
    head = (result.stdout or "").strip()
    return head or None


def inspect_local_git_repo(path: str) -> dict[str, Any]:
    """Collect best-effort git metadata for a local directory."""
    metadata: dict[str, Any] = {
        "is_git_repo": False,
        "branch": None,
        "head_sha": None,
        "is_dirty": None,
        "has_untracked": None,
        "has_tracked_changes": None,
    }
    if not os.path.isdir(path):
        return metadata

    try:
        probe = _run_git(path, ["rev-parse", "--git-dir"])
    except Exception:
        return metadata
    if probe.returncode != 0:
        return metadata
    metadata["is_git_repo"] = True

    try:
        head = _run_git(path, ["rev-parse", "HEAD"])
        if head.returncode == 0:
            metadata["head_sha"] = (head.stdout or "").strip() or None
    except Exception:
        pass

    try:
        branch = _run_git(path, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if branch.returncode == 0:
            metadata["branch"] = (branch.stdout or "").strip() or None
    except Exception:
        pass

    try:
        status = _run_git(path, ["status", "--porcelain", "--untracked-files=normal"], timeout=20)
        if status.returncode == 0:
            lines = [line for line in (status.stdout or "").splitlines() if line.strip()]
            metadata["has_untracked"] = any(line.startswith("?? ") for line in lines)
            metadata["has_tracked_changes"] = any(not line.startswith("?? ") for line in lines)
            metadata["is_dirty"] = bool(lines)
    except Exception:
        pass

    return metadata


def _agenthub_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def agenthub_connection_from_env() -> tuple[str, str]:
    base_url = (os.environ.get("AGENTHUB_URL") or "").strip().rstrip("/")
    api_key = (
        (os.environ.get("AGENTHUB_API_KEY") or "").strip()
        or (os.environ.get("AGENTHUB_ADMIN_KEY") or "").strip()
    )
    if not base_url or not api_key:
        raise AgenthubImportError(
            "Explicit AgentHub import requires AGENTHUB_URL and AGENTHUB_API_KEY (or AGENTHUB_ADMIN_KEY)."
        )
    return base_url, api_key


def fetch_agenthub_receipt(base_url: str, api_key: str, commit_hash: str) -> dict[str, Any]:
    response = requests.get(
        f"{base_url}/api/git/receipts/{commit_hash}",
        headers=_agenthub_headers(api_key),
        timeout=30,
    )
    if response.status_code == 404:
        return {"hash": commit_hash, "exists": False, "bundle_fetchable": False}
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise AgenthubImportError("AgentHub receipt response was not valid JSON.")
    return payload


def push_commit_bundle_to_agenthub(repo_path: str, commit_hash: str, base_url: str, api_key: str) -> None:
    """Upload a full git bundle for ``commit_hash`` and its ancestry to AgentHub."""
    repo_dir = Path(repo_path)
    if not repo_dir.is_dir():
        raise AgenthubImportError(
            f"Cannot import AgentHub root {commit_hash[:12]}: project path {repo_path!r} is not available."
        )
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as handle:
        bundle_path = handle.name
    try:
        # Export all local refs so initial/root commits and detached histories remain importable.
        result = _run_git(str(repo_dir), ["bundle", "create", bundle_path, "--all"], timeout=120)
        if result.returncode != 0:
            raise AgenthubImportError(
                f"Could not bundle commit {commit_hash[:12]} from {repo_path}: "
                f"{(result.stderr or result.stdout).strip()[:400]}"
            )
        with open(bundle_path, "rb") as bundle_file:
            response = requests.post(
                f"{base_url}/api/git/push",
                headers={
                    **_agenthub_headers(api_key),
                    "Content-Type": "application/octet-stream",
                },
                data=bundle_file,
                timeout=120,
            )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AgenthubImportError(
            f"Could not publish commit {commit_hash[:12]} into AgentHub: {exc}"
        ) from exc
    finally:
        try:
            os.unlink(bundle_path)
        except OSError:
            pass


def import_project_agenthub_root(project: Project, *, path_override: str | None = None) -> dict[str, Any]:
    """Publish a local repo into AgentHub and set ``project.accepted_frontier_id``."""
    requested_path = (path_override or "").strip() or (project.project_path or "").strip()
    if not requested_path:
        raise ValueError("project_path is required for explicit AgentHub import")
    resolved_path = os.path.abspath(requested_path)
    if not os.path.exists(resolved_path):
        raise ValueError(f"project_path does not exist: {resolved_path}")
    if not os.path.isdir(resolved_path):
        raise ValueError(f"project_path is not a directory: {resolved_path}")

    git_metadata = inspect_local_git_repo(resolved_path)
    head_sha = git_metadata.get("head_sha")
    if not head_sha:
        raise ValueError(
            "project_path must point to a git checkout with a readable HEAD commit for AgentHub import"
        )

    valid, error = validate_project_frontier_candidate(project, head_sha)
    if not valid:
        raise ValueError(error or "Invalid AgentHub frontier id")

    base_url, api_key = agenthub_connection_from_env()
    receipt = fetch_agenthub_receipt(base_url, api_key, head_sha)
    if not (receipt.get("exists") and receipt.get("bundle_fetchable", True)):
        push_commit_bundle_to_agenthub(resolved_path, head_sha, base_url, api_key)
        receipt = fetch_agenthub_receipt(base_url, api_key, head_sha)
    if not (receipt.get("exists") and receipt.get("bundle_fetchable", True)):
        raise AgenthubImportError(
            f"AgentHub does not expose imported commit {head_sha[:12]} after publish."
        )

    project.project_path = resolved_path
    project.accepted_frontier_id = head_sha
    db.session.commit()

    try:
        current_app.logger.info(
            "Imported local repo into AgentHub for project=%s frontier=%s path=%s",
            project.id,
            head_sha[:12],
            resolved_path,
        )
    except Exception:
        pass

    return {
        "path": resolved_path,
        "accepted_frontier_id": head_sha,
        "git": git_metadata,
        "agenthub_receipt": receipt,
    }


def _extract_import_frontier(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    accepted_frontier_id = (
        payload.get("leaf")
        or payload.get("leaf_id")
        or payload.get("frontier_id")
        or payload.get("accepted_frontier_id")
    )
    resolved_sha = (
        payload.get("resolved_commit_sha")
        or payload.get("resolved_sha")
        or payload.get("github_resolved_sha")
        or payload.get("commit_hash")
        or payload.get("hash")
    )
    accepted_frontier_id = str(accepted_frontier_id).strip() if accepted_frontier_id is not None else None
    resolved_sha = str(resolved_sha).strip() if resolved_sha is not None else None
    return accepted_frontier_id or None, resolved_sha or None


def import_github_project_to_agenthub(
    project: Project,
    *,
    github_url: str,
    github_ref: str,
) -> dict[str, Any]:
    """Import a GitHub repository into AgentHub and set project frontier metadata."""
    repo_url = (github_url or "").strip()
    ref = (github_ref or "").strip()
    if not repo_url:
        raise ValueError("github_url is required for AgentHub GitHub import")
    if not ref:
        raise ValueError("github_ref is required for AgentHub GitHub import")

    base_url, api_key = agenthub_connection_from_env()
    try:
        response = requests.post(
            f"{base_url}/api/git/import/github",
            headers=_agenthub_headers(api_key),
            json={"repository_url": repo_url, "base_ref": ref},
            timeout=120,
        )
    except requests.RequestException as exc:
        raise AgenthubImportError(f"Could not import GitHub repo into AgentHub: {exc}") from exc

    if response.status_code >= 400:
        detail = (response.text or "").strip()
        raise AgenthubImportError(
            f"AgentHub GitHub import failed with status {response.status_code}"
            + (f": {detail[:200]}" if detail else "")
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise AgenthubImportError("AgentHub GitHub import response was not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise AgenthubImportError("AgentHub GitHub import response was not valid JSON.")

    accepted_frontier_id, resolved_sha = _extract_import_frontier(payload)
    valid, error = validate_project_frontier_candidate(project, accepted_frontier_id)
    if not valid:
        raise AgenthubImportError(error or "AgentHub GitHub import returned an invalid frontier id.")
    if not resolved_sha:
        raise AgenthubImportError("AgentHub GitHub import did not return a resolved commit SHA.")

    project.source_type = "github"
    project.github_url = repo_url
    project.github_ref = ref
    project.github_resolved_sha = resolved_sha
    project.accepted_frontier_id = accepted_frontier_id

    return {
        "github_url": repo_url,
        "github_ref": ref,
        "github_resolved_sha": resolved_sha,
        "accepted_frontier_id": accepted_frontier_id,
        "agenthub_import": payload,
    }
