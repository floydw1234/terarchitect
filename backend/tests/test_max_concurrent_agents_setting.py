"""
Unit tests for MAX_CONCURRENT_AGENTS setting support in app_settings.
No external services required.
"""
import sys
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from utils.app_settings import ALLOWED_KEYS, SENSITIVE_KEYS


def test_max_concurrent_agents_in_allowed_keys():
    assert "MAX_CONCURRENT_AGENTS" in ALLOWED_KEYS


def test_max_concurrent_agents_not_sensitive():
    assert "MAX_CONCURRENT_AGENTS" not in SENSITIVE_KEYS, (
        "MAX_CONCURRENT_AGENTS is a plain integer, not a secret"
    )


def test_max_concurrent_agents_not_in_agent_env_keys():
    """This is a coordinator-level setting, not forwarded to the agent container."""
    from utils.app_settings import AGENT_ENV_KEYS
    assert "MAX_CONCURRENT_AGENTS" not in AGENT_ENV_KEYS
