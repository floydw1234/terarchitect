"""Helpers for local swarm runs that need an explicit AgentHub base commit."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

import requests


class AgenthubPreflightError(RuntimeError):
    """Raised when a local swarm run cannot guarantee a valid AgentHub base."""


def read_git_head(path: str | None) -> str | None:
    """Return the current HEAD commit hash for a local repository."""
    if not path or not os.path.isdir(path):
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    head = (result.stdout or "").strip()
    return head or None


def _agenthub_headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _agenthub_receipt(base_url: str, api_key: str, commit_hash: str) -> dict[str, Any]:
    response = requests.get(
        f"{base_url.rstrip('/')}/api/git/receipts/{commit_hash}",
        headers=_agenthub_headers(api_key),
        timeout=30,
    )
    if response.status_code == 404:
        return {"exists": False, "bundle_fetchable": False}
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise AgenthubPreflightError("AgentHub receipt response was not valid JSON.")
    return payload


def _push_bundle(base_url: str, api_key: str, bundle_path: str) -> None:
    with open(bundle_path, "rb") as bundle_file:
        response = requests.post(
            f"{base_url.rstrip('/')}/api/git/push",
            headers={
                **_agenthub_headers(api_key),
                "Content-Type": "application/octet-stream",
            },
            data=bundle_file,
            timeout=120,
        )
    response.raise_for_status()


def seed_commit_from_repo(repo_path: str, commit_hash: str, base_url: str, api_key: str) -> None:
    """Upload a full git bundle for commit_hash and its ancestry to AgentHub."""
    repo_dir = Path(repo_path)
    if not repo_dir.is_dir():
        raise AgenthubPreflightError(
            f"Cannot seed AgentHub base {commit_hash[:12]}: project path {repo_path!r} is not available."
        )
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as handle:
        bundle_path = handle.name
    try:
        result = subprocess.run(
            ["git", "bundle", "create", bundle_path, commit_hash],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise AgenthubPreflightError(
                f"Could not bundle base commit {commit_hash[:12]} from {repo_path}: "
                f"{(result.stderr or result.stdout).strip()[:400]}"
            )
        _push_bundle(base_url, api_key, bundle_path)
    except requests.RequestException as exc:
        raise AgenthubPreflightError(
            f"Could not seed base commit {commit_hash[:12]} into AgentHub: {exc}"
        ) from exc
    finally:
        try:
            os.unlink(bundle_path)
        except OSError:
            pass


def prepare_local_job(job: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return a local swarm job with an explicit base and verified AgentHub lineage."""
    prepared = dict(job)
    run_env = env or os.environ
    explicit_base = (prepared.get("base_hash") or "").strip() or None
    shipped_frontier = (prepared.get("shipped_frontier") or "").strip() or None
    project_path = (prepared.get("project_path") or "").strip() or None
    base_hash = explicit_base or shipped_frontier or read_git_head(project_path)
    if base_hash:
        prepared["base_hash"] = base_hash
    root_hash = (
        (prepared.get("agenthub_root_hash") or "").strip()
        or shipped_frontier
        or base_hash
    )
    if root_hash:
        prepared["agenthub_root_hash"] = root_hash

    if (prepared.get("execution_mode") or "").strip().lower() != "local":
        return prepared
    if (prepared.get("git_mode") or "swarm").strip().lower() != "swarm":
        return prepared

    if not base_hash:
        raise AgenthubPreflightError(
            "Local swarm run requires an explicit base commit. "
            "Set the project's shipped frontier or point project_path at a git checkout with a readable HEAD."
        )

    agenthub_url = (run_env.get("AGENTHUB_URL") or "").strip()
    agenthub_api_key = (run_env.get("AGENTHUB_API_KEY") or "").strip()
    if not agenthub_url or not agenthub_api_key:
        raise AgenthubPreflightError(
            "Local swarm run requires AGENTHUB_URL and AGENTHUB_API_KEY so the base lineage can be verified."
        )

    try:
        receipt = _agenthub_receipt(agenthub_url, agenthub_api_key, base_hash)
    except requests.RequestException as exc:
        raise AgenthubPreflightError(
            f"Could not verify whether AgentHub already has base commit {base_hash[:12]}: {exc}"
        ) from exc
    if receipt.get("exists") and receipt.get("bundle_fetchable", True):
        return prepared

    if not project_path:
        raise AgenthubPreflightError(
            f"AgentHub is missing base commit {base_hash[:12]} and no project_path is available for seeding."
        )

    seed_commit_from_repo(project_path, base_hash, agenthub_url, agenthub_api_key)

    try:
        receipt = _agenthub_receipt(agenthub_url, agenthub_api_key, base_hash)
    except requests.RequestException as exc:
        raise AgenthubPreflightError(
            f"Seeded base commit {base_hash[:12]} but could not verify it in AgentHub: {exc}"
        ) from exc
    if not (receipt.get("exists") and receipt.get("bundle_fetchable", True)):
        raise AgenthubPreflightError(
            f"AgentHub still does not expose base commit {base_hash[:12]} after the seed attempt."
        )
    return prepared
