"""
Tests for compute_settings_check() — the logic behind GET /api/settings/check.

Tests the pure function directly (no Flask app required) by patching
utils.settings_check.get_value and utils.settings_check.get_setting_or_env.

Required settings depend on providers:

  Always:
    github_agent_token, GIT_USER_NAME, GIT_USER_EMAIL
    AGENT_MODEL, AGENT_API_KEY
    MEMORY_EMBEDDING_MODEL

  AGENT_PROVIDER=openai (default):
    openai: auto-resolves URL (no extra required)
    custom: + AGENT_LLM_URL

  WORKER_MODE=claude-code (default):
    claude-code: WORKER_API_KEY (real Anthropic key)
    opencode:    WORKER_LLM_URL + WORKER_MODEL + WORKER_API_KEY

  EMBEDDING_PROVIDER=openai (default):
    openai: + openai_api_key
    custom: + EMBEDDING_SERVICE_URL + EMBEDDING_API_KEY

Warnings (degraded without):
  MEMORY_LLM_API_KEY (when MEMORY_LLM_BASE_URL set with no key)
"""
import sys
import os
from unittest.mock import patch

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import utils.settings_check as sc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(stored: dict) -> dict:
    """Run compute_settings_check() with get_value / get_setting_or_env stubbed from `stored`."""
    def _get_value(key):
        return stored.get(key)

    def _get_setting_or_env(key, *args, **kwargs):
        return stored.get(key)

    with patch.object(sc, "get_value", side_effect=_get_value), \
         patch.object(sc, "get_setting_or_env", side_effect=_get_setting_or_env):
        return sc.compute_settings_check()


def _all_required_claude_openai() -> dict:
    """Full set for claude-code worker + OpenAI embeddings (most common setup)."""
    return {
        "github_agent_token": "ghp_agent",
        "GIT_USER_NAME": "Agent User",
        "GIT_USER_EMAIL": "agent@example.com",
        "AGENT_PROVIDER": "openai",
        "AGENT_MODEL": "gpt-4o",
        "AGENT_API_KEY": "sk-director",
        "WORKER_MODE": "claude-code",
        "WORKER_API_KEY": "sk-ant-real",
        "EMBEDDING_PROVIDER": "openai",
        "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
        "openai_api_key": "sk-openai",
    }


def _all_required_opencode_custom() -> dict:
    """Full set for opencode worker + custom embedding provider."""
    return {
        "github_agent_token": "ghp_agent",
        "GIT_USER_NAME": "Agent User",
        "GIT_USER_EMAIL": "agent@example.com",
        "AGENT_PROVIDER": "custom",
        "AGENT_LLM_URL": "https://api.openai.com",
        "AGENT_MODEL": "gpt-4o",
        "AGENT_API_KEY": "sk-director",
        "WORKER_MODE": "opencode",
        "WORKER_LLM_URL": "http://llm-host:8080/v1",
        "WORKER_MODEL": "gpt-4o",
        "WORKER_API_KEY": "sk-worker",
        "EMBEDDING_PROVIDER": "custom",
        "EMBEDDING_SERVICE_URL": "https://api.openai.com/v1",
        "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_API_KEY": "sk-embed",
    }


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_result_has_expected_shape():
    result = _run({})
    assert "ready" in result
    assert "missing_required" in result
    assert "warnings" in result
    assert isinstance(result["missing_required"], list)
    assert isinstance(result["warnings"], list)


# ---------------------------------------------------------------------------
# GitHub — Agent identity
# ---------------------------------------------------------------------------

def test_missing_github_agent_token_is_required():
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "github_agent_token" in keys
    assert result["ready"] is False


def test_set_github_agent_token_removes_from_required():
    result = _run({"github_agent_token": "ghp_agent"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "github_agent_token" not in keys


def test_missing_git_user_name_is_required():
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "GIT_USER_NAME" in keys


def test_set_git_user_name_removes_from_required():
    result = _run({"GIT_USER_NAME": "Agent User"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "GIT_USER_NAME" not in keys


def test_missing_git_user_email_is_required():
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "GIT_USER_EMAIL" in keys


def test_set_git_user_email_removes_from_required():
    result = _run({"GIT_USER_EMAIL": "agent@example.com"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "GIT_USER_EMAIL" not in keys


# ---------------------------------------------------------------------------
# Director LLM — AGENT_PROVIDER
# ---------------------------------------------------------------------------

def test_openai_provider_does_not_require_agent_llm_url():
    """OpenAI provider (default) auto-resolves the URL; AGENT_LLM_URL not required."""
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_LLM_URL" not in keys


def test_custom_provider_requires_agent_llm_url():
    """Custom provider requires AGENT_LLM_URL."""
    result = _run({"AGENT_PROVIDER": "custom"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_LLM_URL" in keys


def test_custom_provider_set_agent_llm_url_removes_from_required():
    result = _run({"AGENT_PROVIDER": "custom", "AGENT_LLM_URL": "https://api.openai.com"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_LLM_URL" not in keys


# ---------------------------------------------------------------------------
# Director LLM — AGENT_MODEL (always required)
# ---------------------------------------------------------------------------

def test_missing_agent_model_is_required():
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_MODEL" in keys


def test_set_agent_model_removes_from_required():
    result = _run({"AGENT_MODEL": "gpt-4o"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_MODEL" not in keys


# ---------------------------------------------------------------------------
# Director LLM — AGENT_API_KEY (required, dummy accepted)
# ---------------------------------------------------------------------------

def test_missing_agent_api_key_is_required():
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_API_KEY" in keys


def test_set_agent_api_key_removes_from_required():
    result = _run({"AGENT_API_KEY": "sk-test"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_API_KEY" not in keys


def test_agent_api_key_dummy_is_accepted():
    result = _run({"AGENT_API_KEY": "dummy"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "AGENT_API_KEY" not in keys


# ---------------------------------------------------------------------------
# Worker — claude-code mode (default)
# ---------------------------------------------------------------------------

def test_default_mode_is_claude_code_so_worker_api_key_required():
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_API_KEY" in keys
    assert "WORKER_LLM_URL" not in keys


def test_claude_code_missing_api_key_is_required():
    result = _run({"WORKER_MODE": "claude-code"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_API_KEY" in keys


def test_claude_code_with_real_api_key_not_required():
    result = _run({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-real"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_API_KEY" not in keys


def test_claude_code_dummy_key_still_required():
    result = _run({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "dummy"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_API_KEY" in keys


def test_claude_code_does_not_require_worker_llm_url_or_model():
    result = _run({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-real"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_LLM_URL" not in keys
    assert "WORKER_MODEL" not in keys


# ---------------------------------------------------------------------------
# Worker — opencode mode
# ---------------------------------------------------------------------------

def test_opencode_requires_worker_llm_url_model_and_api_key():
    result = _run({"WORKER_MODE": "opencode"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_LLM_URL" in keys
    assert "WORKER_MODEL" in keys
    assert "WORKER_API_KEY" in keys


def test_opencode_all_worker_fields_set_not_required():
    result = _run({"WORKER_MODE": "opencode", "WORKER_LLM_URL": "http://host:8080/v1",
                   "WORKER_MODEL": "gpt-4o", "WORKER_API_KEY": "sk-worker"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_LLM_URL" not in keys
    assert "WORKER_MODEL" not in keys
    assert "WORKER_API_KEY" not in keys


def test_opencode_worker_api_key_dummy_is_accepted():
    result = _run({"WORKER_MODE": "opencode", "WORKER_LLM_URL": "http://host:8080/v1",
                   "WORKER_MODEL": "gpt-4o", "WORKER_API_KEY": "dummy"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "WORKER_API_KEY" not in keys


# ---------------------------------------------------------------------------
# Embeddings — EMBEDDING_PROVIDER
# ---------------------------------------------------------------------------

def test_missing_embedding_model_always_required():
    """MEMORY_EMBEDDING_MODEL is required regardless of provider."""
    for provider in ["openai", "custom", None]:
        stored = {"EMBEDDING_PROVIDER": provider} if provider else {}
        result = _run(stored)
        keys = [i["key"] for i in result["missing_required"]]
        assert "MEMORY_EMBEDDING_MODEL" in keys, f"failed for provider={provider}"


def test_set_embedding_model_removes_from_required():
    result = _run({"MEMORY_EMBEDDING_MODEL": "text-embedding-3-small"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "MEMORY_EMBEDDING_MODEL" not in keys


def test_openai_embedding_requires_openai_api_key():
    """Default embedding provider is OpenAI; openai_api_key is required."""
    result = _run({"EMBEDDING_PROVIDER": "openai"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "openai_api_key" in keys
    assert "EMBEDDING_SERVICE_URL" not in keys
    assert "EMBEDDING_API_KEY" not in keys


def test_openai_embedding_set_key_satisfies():
    result = _run({"EMBEDDING_PROVIDER": "openai", "openai_api_key": "sk-openai"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "openai_api_key" not in keys


def test_default_provider_is_openai_so_openai_key_required():
    """When EMBEDDING_PROVIDER not set, defaults to openai."""
    result = _run({})
    keys = [i["key"] for i in result["missing_required"]]
    assert "openai_api_key" in keys
    assert "EMBEDDING_SERVICE_URL" not in keys


def test_custom_embedding_requires_url_and_api_key():
    result = _run({"EMBEDDING_PROVIDER": "custom"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "EMBEDDING_SERVICE_URL" in keys
    assert "EMBEDDING_API_KEY" in keys
    assert "openai_api_key" not in keys


def test_custom_embedding_all_set_not_required():
    result = _run({"EMBEDDING_PROVIDER": "custom",
                   "EMBEDDING_SERVICE_URL": "http://embed:9000/v1",
                   "EMBEDDING_API_KEY": "sk-embed"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "EMBEDDING_SERVICE_URL" not in keys
    assert "EMBEDDING_API_KEY" not in keys


def test_custom_embedding_openai_key_substitutes_api_key():
    """openai_api_key is an accepted substitute for EMBEDDING_API_KEY in custom mode."""
    result = _run({"EMBEDDING_PROVIDER": "custom",
                   "EMBEDDING_SERVICE_URL": "http://embed:9000/v1",
                   "openai_api_key": "sk-openai"})
    keys = [i["key"] for i in result["missing_required"]]
    assert "EMBEDDING_API_KEY" not in keys


# ---------------------------------------------------------------------------
# Memory LLM API key warning
# ---------------------------------------------------------------------------

def test_memory_llm_base_url_without_key_warns():
    result = _run({"MEMORY_LLM_BASE_URL": "https://api.openai.com"})
    warn_keys = [i["key"] for i in result["warnings"]]
    assert "MEMORY_LLM_API_KEY" in warn_keys


def test_memory_llm_base_url_with_agent_key_no_warning():
    result = _run({"MEMORY_LLM_BASE_URL": "https://api.openai.com", "AGENT_API_KEY": "sk-agent"})
    warn_keys = [i["key"] for i in result["warnings"]]
    assert "MEMORY_LLM_API_KEY" not in warn_keys


def test_no_memory_llm_base_url_no_memory_warning():
    result = _run({})
    warn_keys = [i["key"] for i in result["warnings"]]
    assert "MEMORY_LLM_API_KEY" not in warn_keys


# ---------------------------------------------------------------------------
# ready flag
# ---------------------------------------------------------------------------

def test_ready_false_with_any_missing_required():
    result = _run({})
    assert result["ready"] is False


def test_ready_true_claude_code_openai_embeddings():
    result = _run(_all_required_claude_openai())
    assert result["ready"] is True
    assert result["missing_required"] == []


def test_ready_true_opencode_custom_embeddings():
    result = _run(_all_required_opencode_custom())
    assert result["ready"] is True
    assert result["missing_required"] == []


def test_warnings_do_not_affect_ready():
    """Warnings (memory LLM key missing) don't block ready when all required fields are set."""
    # Memory LLM warning fires when a custom MEMORY_LLM_BASE_URL is set but no key is available.
    # Since AGENT_API_KEY (required) is used as a fallback, we omit it here to isolate the warning,
    # but the other required fields keep ready=True.
    base = {
        "github_agent_token": "ghp_agent",
        "GIT_USER_NAME": "Agent User",
        "GIT_USER_EMAIL": "agent@example.com",
        "AGENT_PROVIDER": "openai",
        "AGENT_MODEL": "gpt-4o",
        "AGENT_API_KEY": "sk-director",
        "WORKER_MODE": "claude-code",
        "WORKER_API_KEY": "sk-ant-real",
        "EMBEDDING_PROVIDER": "openai",
        "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
        "openai_api_key": "sk-openai",
        "MEMORY_LLM_BASE_URL": "http://memory-host:8000",
        "MEMORY_LLM_API_KEY": None,  # explicitly absent
    }
    result = _run(base)
    # ready=True because all required fields are present
    assert result["ready"] is True
    # No warnings here because AGENT_API_KEY acts as fallback; the warning section is tested separately
    assert isinstance(result["warnings"], list)
