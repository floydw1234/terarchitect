"""
Agent config read from environment variables only.
The coordinator injects these when starting local/docker agent runs.
"""
import os
from typing import Optional


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(key) or default
    if v is None:
        return None
    return (v or "").strip() or None


def get_setting_or_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return value for key from environment."""
    v = os.environ.get(key)
    if v is not None:
        v = (v or "").strip() or None
    return v if v is not None else (default if default is not None else None)


def get_gh_env_for_agent() -> dict:
    """Env dict for gh/git when agent pushes and creates PRs."""
    token = (
        _env("github_agent_token")
        or _env("GITHUB_TOKEN")
        or _env("GH_TOKEN")
        or _env("GITHUB_AGENT_TOKEN")
    )
    if not token:
        return {}
    return {"GH_TOKEN": token, "GITHUB_TOKEN": token}
