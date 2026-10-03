"""Resolve the Bearer token for Terarchitect worker API routes."""

from __future__ import annotations

import os


def effective_terarchitect_worker_api_key() -> str:
    """TERARCHITECT_WORKER_API_KEY, falling back to WORKER_API_KEY (common in .env)."""
    return (
        (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip()
        or (os.environ.get("WORKER_API_KEY") or "").strip()
    )
