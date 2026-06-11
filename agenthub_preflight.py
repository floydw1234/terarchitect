"""Helpers for local swarm runs that need an explicit AgentHub base commit."""

from __future__ import annotations

import os
from typing import Any, Mapping

import requests


class AgenthubPreflightError(RuntimeError):
    """Raised when a local swarm run cannot guarantee a valid AgentHub base."""


def _import_helpers():
    from backend.api.services.agenthub_import_service import (
        AgenthubImportError,
        fetch_agenthub_receipt,
        push_commit_bundle_to_agenthub,
        read_git_head,
    )

    return AgenthubImportError, fetch_agenthub_receipt, push_commit_bundle_to_agenthub, read_git_head


def read_git_head(path: str | None) -> str | None:
    _, _, _, read_head = _import_helpers()
    return read_head(path)


def _agenthub_receipt(base_url: str, api_key: str, commit_hash: str) -> dict[str, Any]:
    _, fetch_receipt, _, _ = _import_helpers()
    return fetch_receipt(base_url.rstrip("/"), api_key, commit_hash)


def seed_commit_from_repo(repo_path: str, commit_hash: str, base_url: str, api_key: str) -> None:
    """Upload a full git bundle for commit_hash and its ancestry to AgentHub."""
    AgenthubImportError, _, push_bundle, _ = _import_helpers()
    try:
        push_bundle(repo_path, commit_hash, base_url.rstrip("/"), api_key)
    except AgenthubImportError as exc:
        raise AgenthubPreflightError(str(exc)) from exc


def prepare_local_job(job: Mapping[str, Any], *, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return a local swarm job with a verified AgentHub base leaf."""
    prepared = dict(job)
    run_env = env or os.environ
    base_leaf_id = (prepared.get("base_leaf_id") or "").strip() or None
    explicit_base = (prepared.get("base_hash") or "").strip() or None
    shipped_frontier = (prepared.get("shipped_frontier") or "").strip() or None
    base_hash = base_leaf_id or explicit_base or shipped_frontier
    if base_leaf_id:
        prepared["base_leaf_id"] = base_leaf_id
    if base_hash:
        prepared["base_hash"] = base_hash
    root_hash = (
        (prepared.get("agenthub_root_hash") or "").strip()
        or base_hash
        or shipped_frontier
    )
    if root_hash:
        prepared["agenthub_root_hash"] = root_hash

    if (prepared.get("execution_mode") or "").strip().lower() != "local":
        return prepared
    if (prepared.get("git_mode") or "swarm").strip().lower() != "swarm":
        return prepared

    if not base_leaf_id:
        raise AgenthubPreflightError(
            "Local swarm run requires ticket.base_leaf_id so the workspace can be materialized from AgentHub."
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
            f"Could not verify whether AgentHub already has base leaf {base_hash[:12]}: {exc}"
        ) from exc
    if receipt.get("exists") and receipt.get("bundle_fetchable", True):
        return prepared

    raise AgenthubPreflightError(
        f"AgentHub base leaf {base_hash[:12]} is missing or not fetchable."
    )
