"""Runtime configuration checks for in-process auto-ship (local shipper + AgentHub)."""

from __future__ import annotations

import os
from urllib.parse import urlparse

_DOCKER_AGENTHUB_HOSTS = frozenset({"agenthub"})


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_compose_agenthub_url(url: str) -> bool:
    hostname = (urlparse((url or "").strip()).hostname or "").lower()
    return hostname in _DOCKER_AGENTHUB_HOSTS


def auto_ship_runtime_issues() -> list[str]:
    """Return human-readable misconfiguration reasons (empty when OK)."""
    issues: list[str] = []
    agenthub_url = (os.environ.get("AGENTHUB_URL") or "").strip().rstrip("/")
    if not agenthub_url:
        issues.append("AGENTHUB_URL is not set in backend runtime (required for auto-ship compose).")
    elif not is_compose_agenthub_url(agenthub_url):
        host_override = (os.environ.get("TERARCHITECT_AGENTHUB_URL") or "").strip()
        if host_override and host_override.rstrip("/") != agenthub_url:
            issues.append(
                "AGENTHUB_URL looks like a host-only URL; in-compose auto-ship must use "
                "http://agenthub:8080 (TERARCHITECT_AGENTHUB_URL is for host CLI only)."
            )

    auth_disabled = _truthy(os.environ.get("AGENTHUB_AUTH_DISABLED"))
    api_key = (os.environ.get("AGENTHUB_API_KEY") or "").strip()
    if not api_key and not auth_disabled:
        issues.append("AGENTHUB_API_KEY is not set in backend runtime (required for auto-ship compose).")

    worker_key = (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if not worker_key:
        issues.append(
            "TERARCHITECT_WORKER_API_KEY is not set in backend runtime "
            "(required for in-process shipper API calls)."
        )
    return issues


def format_auto_ship_runtime_error() -> str:
    issues = auto_ship_runtime_issues()
    if not issues:
        return ""
    return "Auto-ship runtime misconfigured: " + "; ".join(issues)
