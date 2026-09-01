"""
Backend app settings read from environment variables only.

Backend-owned config:
- GitHub token for UI/automation actions
- Embedding + memory LLM settings (RAG/HippoRAG)
- Worker API auth token for /api/worker/*
"""
import os
from typing import List, Optional, Tuple

MissingRequired = Tuple[str, str]  # (key, label)


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(key) or default
    if v is None:
        return None
    v = (v or "").strip()
    return v or None


def get_value(key: str) -> Optional[str]:
    """Return value for key from environment (no DB-backed settings)."""
    return _env(key)


def get_setting_or_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return env value for key, falling back to default when unset or empty."""
    v = os.environ.get(key)
    if v is not None:
        v = (v or "").strip() or None
    return v if v is not None else (default if default is not None else None)


def get_gh_env_for_user() -> dict:
    return get_gh_env_for_agent()


def get_dashboard_git_env() -> dict:
    out: dict = {}
    name = _env("GIT_USER_NAME")
    email = _env("GIT_USER_EMAIL")
    if name:
        out["GIT_AUTHOR_NAME"] = out["GIT_COMMITTER_NAME"] = name
    if email:
        out["GIT_AUTHOR_EMAIL"] = out["GIT_COMMITTER_EMAIL"] = email
    return out


def get_gh_env_for_agent() -> dict:
    token = (
        _env("github_agent_token")
        or _env("GITHUB_TOKEN")
        or _env("GH_TOKEN")
        or _env("GITHUB_AGENT_TOKEN")
    )
    if not token:
        return {}
    return {"GH_TOKEN": token, "GITHUB_TOKEN": token}


def get_frontend_llm_settings() -> dict:
    """Return FRONTEND_LLM_* settings, falling back to DIRECTOR_* when unset.
    Returns dict with keys: url, model, api_key. Values may be None if unconfigured."""
    url = _env("FRONTEND_LLM_URL") or _env("DIRECTOR_LLM_URL")
    model = _env("FRONTEND_LLM_MODEL") or _env("DIRECTOR_MODEL")
    api_key = _env("FRONTEND_LLM_API_KEY") or _env("DIRECTOR_API_KEY") or _env("openai_api_key") or _env("OPENAI_API_KEY")
    return {"url": url, "model": model, "api_key": api_key}


def get_github_token() -> str | None:
    """Return the configured GitHub token (any of the accepted env var names)."""
    return (
        _env("github_agent_token")
        or _env("GITHUB_TOKEN")
        or _env("GH_TOKEN")
        or _env("GITHUB_AGENT_TOKEN")
    )


def check_execution_readiness() -> Tuple[bool, List[MissingRequired]]:
    """Backend-side readiness before moving ticket to In Progress.

    This validates only backend-owned requirements, not coordinator/agent LLM config.

    Note: Embedding/memory configuration is OPTIONAL. When not configured, the system
    uses a no-op memory backend that returns empty results. Embedding keys are not
    required for local/AgentHub execution; they are only needed for HippoRAG memory
    and ticket/graph search features.

    GitHub token is NOT required for basic readiness. Local/AgentHub/worktree modes
    can execute without GitHub auth. GitHub is only required for GitHub-backed
    import/export paths (enforced at those operation boundaries, not here).
    """
    missing: List[MissingRequired] = []
    return (len(missing) == 0, missing)
