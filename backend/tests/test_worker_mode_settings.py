"""
Unit tests for WORKER_MODE setting support in app_settings.
No external services required.
"""
import sys
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from utils.app_settings import ALLOWED_KEYS, SENSITIVE_KEYS, AGENT_ENV_KEYS


def test_worker_mode_in_allowed_keys():
    assert "WORKER_MODE" in ALLOWED_KEYS, "WORKER_MODE must be in ALLOWED_KEYS"


def test_worker_mode_not_sensitive():
    assert "WORKER_MODE" not in SENSITIVE_KEYS, "WORKER_MODE must not be sensitive (it's a plain string, not a secret)"


def test_worker_mode_in_agent_env_keys():
    assert "WORKER_MODE" in AGENT_ENV_KEYS, "WORKER_MODE must be forwarded to the agent container via AGENT_ENV_KEYS"


def test_worker_api_key_still_sensitive():
    """Regression: WORKER_API_KEY (used as Anthropic key in claude-code mode) must stay sensitive."""
    assert "WORKER_API_KEY" in SENSITIVE_KEYS


def test_existing_worker_keys_unchanged():
    """Regression: existing worker settings remain in ALLOWED_KEYS and AGENT_ENV_KEYS."""
    for key in ("WORKER_LLM_URL", "WORKER_MODEL", "WORKER_API_KEY", "WORKER_TIMEOUT_SEC"):
        assert key in ALLOWED_KEYS, f"{key} must remain in ALLOWED_KEYS"
    for key in ("WORKER_LLM_URL", "WORKER_MODEL", "WORKER_API_KEY", "WORKER_TIMEOUT_SEC"):
        assert key in AGENT_ENV_KEYS, f"{key} must remain in AGENT_ENV_KEYS"
