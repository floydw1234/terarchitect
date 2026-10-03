"""Resolve Terarchitect worker API Bearer token (backend auth + auto-ship clients)."""

from __future__ import annotations

import os
from pathlib import Path


def _read_secret_file(path_var: str) -> str:
    path = (os.environ.get(path_var) or "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip().strip("\r\n")
    except OSError:
        return ""


def resolve_terarchitect_worker_api_key() -> str:
    """TERARCHITECT_WORKER_API_KEY, else contents of TERARCHITECT_WORKER_API_KEY_PATH."""
    direct = (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if direct:
        return direct
    return _read_secret_file("TERARCHITECT_WORKER_API_KEY_PATH")


def is_worker_api_auth_enforced() -> bool:
    """True when the backend requires Bearer auth on /api/worker/* (key or key path configured)."""
    if (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip():
        return True
    return bool((os.environ.get("TERARCHITECT_WORKER_API_KEY_PATH") or "").strip())


def configured_worker_api_token() -> str:
    """Resolved worker API token when auth is enabled; empty when auth is disabled."""
    if not is_worker_api_auth_enforced():
        return ""
    return resolve_terarchitect_worker_api_key()


def terarchitect_worker_request_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = resolve_terarchitect_worker_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers
