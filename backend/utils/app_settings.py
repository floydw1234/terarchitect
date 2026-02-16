"""
App-level settings (tokens, URLs, env-style keys). Sensitive keys encrypted at rest when TERARCHITECT_SECRET_KEY is set.
Use from request context or with an active app context.
"""
import os
from typing import Optional, Any

from utils.app_settings_crypto import encrypt_value, decrypt_value, is_encryption_available


# All keys that can be set via the Settings UI. Use same names as env vars where applicable.
# openai_api_key: used as fallback for memory LLM (HippoRAG). anthropic_api_key: reserved for future Claude workers.
ALLOWED_KEYS = frozenset({
    # GitHub (sensitive)
    "github_user_token",
    "github_agent_token",
    # LLM / API keys (sensitive)
    "openai_api_key",
    "anthropic_api_key",
    "AGENT_API_KEY",
    "WORKER_API_KEY",
    "EMBEDDING_API_KEY",
    # Agent
    "VLLM_URL",
    "AGENT_MODEL",
    "WORKER_TYPE",
    "WORKER_LLM_URL",
    "WORKER_MODEL",
    "WORKER_TIMEOUT_SEC",
    "MIDDLE_AGENT_DEBUG",
    # Memory (HippoRAG) - MEMORY_SAVE_DIR not configurable; fixed default /tmp/terarchitect
    "MEMORY_LLM_MODEL",
    "MEMORY_LLM_BASE_URL",
    "MEMORY_LLM_API_KEY",
    "MEMORY_EMBEDDING_MODEL",
    "MEMORY_EMBEDDING_BASE_URL",
    # Embedding service
    "EMBEDDING_SERVICE_URL",
})

# Keys stored encrypted; rest stored plain (URLs, paths, model names, etc.)
SENSITIVE_KEYS = frozenset({
    "github_user_token",
    "github_agent_token",
    "openai_api_key",
    "anthropic_api_key",
    "AGENT_API_KEY",
    "WORKER_API_KEY",
    "MEMORY_LLM_API_KEY",
    "EMBEDDING_API_KEY",
})


def get_value(key: str) -> Optional[str]:
    """Get value for key (decrypted if sensitive, raw if plain). Returns None if missing or invalid. Requires app context."""
    if key not in ALLOWED_KEYS:
        return None
    from flask import current_app
    from models.db import AppSetting
    with current_app.app_context():
        row = AppSetting.query.filter_by(key=key).first()
        if not row or not row.value:
            return None
        if key in SENSITIVE_KEYS:
            dec = decrypt_value(row.value)
            return dec if dec else None
        return row.value


def set_value(key: str, plaintext: str) -> bool:
    """Store value. Sensitive keys are encrypted (requires TERARCHITECT_SECRET_KEY). Plain keys stored as-is. Returns False if key not allowed or encryption required but unavailable."""
    import sys
    if key not in ALLOWED_KEYS:
        print(f"[DEBUG] set_value: key {key!r} not in ALLOWED_KEYS", file=sys.stderr, flush=True)
        return False
    from flask import current_app
    from models.db import db, AppSetting
    if key in SENSITIVE_KEYS:
        if not is_encryption_available():
            print("[DEBUG] set_value: encryption not available", file=sys.stderr, flush=True)
            return False
        encrypted = encrypt_value(plaintext)
        if not encrypted:
            print("[DEBUG] set_value: encrypt_value returned None", file=sys.stderr, flush=True)
            return False
        value_to_store = encrypted
    else:
        value_to_store = plaintext
    try:
        with current_app.app_context():
            row = AppSetting.query.filter_by(key=key).first()
            if row:
                row.value = value_to_store
            else:
                db.session.add(AppSetting(key=key, value=value_to_store))
            db.session.commit()
            return True
    except Exception as e:
        import sys
        print(f"[DEBUG] set_value exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def get_decrypted(key: str) -> Optional[str]:
    """Get decrypted value for a sensitive key. Convenience alias for get_value for backward compat."""
    return get_value(key)


def set_encrypted(key: str, plaintext: str) -> bool:
    """Store value (encrypted if sensitive). Convenience for backward compat."""
    return set_value(key, plaintext)


def delete_key(key: str) -> bool:
    """Remove a setting. Returns True if deleted or didn't exist. Requires app context."""
    if key not in ALLOWED_KEYS:
        return False
    from flask import current_app
    from models.db import db, AppSetting
    with current_app.app_context():
        AppSetting.query.filter_by(key=key).delete()
        try:
            db.session.commit()
            return True
        except Exception:
            db.session.rollback()
            return False


def get_setting_or_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return app setting value if set, else os.environ.get(key, default). Use for agent/memory/embedding config."""
    if key not in ALLOWED_KEYS:
        return os.environ.get(key, default)
    val = get_value(key)
    if val is not None and (val or key not in SENSITIVE_KEYS):
        return val if val else None
    return os.environ.get(key, default)


def get_all_for_api() -> dict:
    """Return dict for GET /api/settings: sensitive keys -> bool (is set), plain keys -> value or null. Requires app context."""
    import sys
    try:
        from flask import current_app
        from models.db import AppSetting
        with current_app.app_context():
            rows = {r.key: r.value for r in AppSetting.query.filter(AppSetting.key.in_(ALLOWED_KEYS)).all()}
        out: dict = {}
        for key in ALLOWED_KEYS:
            if key in SENSITIVE_KEYS:
                out[key] = bool(rows.get(key))
            else:
                out[key] = rows.get(key) or None
        return out
    except Exception as e:
        print(f"[DEBUG] get_all_for_api exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        raise


def get_masked_status() -> dict:
    """Legacy: dict of key -> bool for sensitive keys only. Prefer get_all_for_api for full response."""
    status = get_all_for_api()
    return {k: status[k] for k in SENSITIVE_KEYS} if SENSITIVE_KEYS else {}


def get_gh_env_for_user() -> dict:
    """Env dict for gh CLI when doing UI actions. Merge with os.environ. Empty if no token set."""
    token = get_value("github_user_token")
    if not token:
        return {}
    return {"GH_TOKEN": token, "GITHUB_TOKEN": token}


def get_gh_env_for_agent() -> dict:
    """Env dict for gh/git when agent pushes and creates PRs. Merge with os.environ. Empty if no token set."""
    token = get_value("github_agent_token")
    if not token:
        return {}
    return {"GH_TOKEN": token, "GITHUB_TOKEN": token}
