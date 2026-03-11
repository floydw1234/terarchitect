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


def check_execution_readiness() -> Tuple[bool, List[MissingRequired]]:
    """Backend-side readiness before moving ticket to In Progress.

    This validates only backend-owned requirements, not coordinator/agent LLM config.
    """
    missing: List[MissingRequired] = []

    if not _env("github_agent_token") and not _env("GITHUB_TOKEN") and not _env("GH_TOKEN") and not _env("GITHUB_AGENT_TOKEN"):
        missing.append(("github_agent_token", "GitHub token"))

    if not _env("MEMORY_EMBEDDING_MODEL"):
        missing.append(("MEMORY_EMBEDDING_MODEL", "Embedding model"))

    emb_provider = (_env("EMBEDDING_PROVIDER") or "openai").strip().lower()
    if emb_provider == "openai":
        if not _env("openai_api_key") and not _env("OPENAI_API_KEY"):
            missing.append(("openai_api_key", "OpenAI API key (embeddings)"))
    else:
        if not _env("EMBEDDING_SERVICE_URL"):
            missing.append(("EMBEDDING_SERVICE_URL", "Embedding service URL"))
        if not _env("EMBEDDING_API_KEY") and not _env("openai_api_key") and not _env("OPENAI_API_KEY"):
            missing.append(("EMBEDDING_API_KEY", "Embedding API key"))

    return (len(missing) == 0, missing)
