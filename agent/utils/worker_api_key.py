"""Resolve Terarchitect worker API Bearer token for coordinator, shipper, and agent runner."""

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
    direct = (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if direct:
        return direct
    return _read_secret_file("TERARCHITECT_WORKER_API_KEY_PATH")


def terarchitect_worker_request_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = resolve_terarchitect_worker_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def agenthub_request_headers() -> dict[str, str]:
    disabled = (os.environ.get("AGENTHUB_AUTH_DISABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if disabled:
        return {}
    key = (os.environ.get("AGENTHUB_API_KEY") or "").strip()
    if not key:
        path = (os.environ.get("AGENTHUB_API_KEY_PATH") or "").strip()
        if path:
            try:
                key = Path(path).read_text(encoding="utf-8").strip().strip("\r\n")
            except OSError:
                key = ""
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}
