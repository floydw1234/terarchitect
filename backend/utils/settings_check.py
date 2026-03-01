"""
Compute which required and recommended settings are missing.
Extracted from the route so it can be tested without spinning up Flask.

Required settings depend on selected providers:

  GitHub (always):
    github_agent_token, GIT_USER_NAME, GIT_USER_EMAIL
    (one token is used for agent pushes/PRs and UI polling/review actions)

  Director LLM (always):
    AGENT_MODEL, AGENT_API_KEY
    + AGENT_LLM_URL  (only when AGENT_PROVIDER=custom)
    → When AGENT_PROVIDER=openai (default), AGENT_LLM_URL is auto-resolved to https://api.openai.com.

  Worker (mode-specific):
    claude-code (default): WORKER_API_KEY (real Anthropic key; 'dummy' not accepted)
    opencode:              WORKER_LLM_URL, WORKER_MODEL, WORKER_API_KEY (dummy OK)

  Embeddings:
    MEMORY_EMBEDDING_MODEL (always)
    + When EMBEDDING_PROVIDER=openai (default):  openai_api_key
    + When EMBEDDING_PROVIDER=custom:            EMBEDDING_SERVICE_URL, EMBEDDING_API_KEY

Warnings (degraded without):
  MEMORY_LLM_API_KEY (when MEMORY_LLM_BASE_URL set without a key)
"""
from utils.app_settings import get_value, get_setting_or_env


def compute_settings_check() -> dict:
    """Return a dict describing missing required settings and warnings.

    Shape:
        {
            "ready": bool,
            "missing_required": [{"key": str, "label": str, "reason": str}, ...],
            "warnings":         [{"key": str, "label": str, "reason": str}, ...],
        }
    """
    missing_required: list[dict] = []
    warnings: list[dict] = []

    # -------------------------------------------------------------------------
    # GitHub (always required — one token for agent and UI actions)
    # -------------------------------------------------------------------------

    if not get_value("github_agent_token"):
        missing_required.append({
            "key": "github_agent_token",
            "label": "GitHub agent token",
            "reason": "Required for the agent to push branches and open PRs.",
        })

    if not get_setting_or_env("GIT_USER_NAME"):
        missing_required.append({
            "key": "GIT_USER_NAME",
            "label": "Agent git name",
            "reason": "Required for agent git commits. Cannot commit or push without a git author identity.",
        })

    if not get_setting_or_env("GIT_USER_EMAIL"):
        missing_required.append({
            "key": "GIT_USER_EMAIL",
            "label": "Agent git email",
            "reason": "Required for agent git commits. Cannot commit or push without a git author identity.",
        })

    # -------------------------------------------------------------------------
    # Director LLM
    # -------------------------------------------------------------------------

    agent_provider = (get_setting_or_env("AGENT_PROVIDER") or "openai").strip().lower()

    # AGENT_LLM_URL only required for custom provider (openai auto-resolves it)
    if agent_provider != "openai":
        vllm_url = get_setting_or_env("AGENT_LLM_URL")
        if not vllm_url:
            missing_required.append({
                "key": "AGENT_LLM_URL",
                "label": "Director LLM URL",
                "reason": "Required for custom provider. No default — set to your LLM base URL (e.g. http://your-host:8000).",
            })

    agent_model = get_setting_or_env("AGENT_MODEL")
    if not agent_model:
        missing_required.append({
            "key": "AGENT_MODEL",
            "label": "Director LLM model",
            "reason": "Required. No default — set to your model name (e.g. gpt-4o, claude-opus-4-5).",
        })

    agent_api_key = get_value("AGENT_API_KEY")
    if not agent_api_key:
        missing_required.append({
            "key": "AGENT_API_KEY",
            "label": "Director LLM API key",
            "reason": "Required. Use your cloud API key (OpenAI, Anthropic, etc.) or 'dummy' for local LLMs that skip auth.",
        })

    # -------------------------------------------------------------------------
    # Worker (mode-specific, no defaults)
    # -------------------------------------------------------------------------

    worker_mode = (get_setting_or_env("WORKER_MODE") or "claude-code").strip().lower()

    if worker_mode == "claude-code":
        worker_api_key = get_value("WORKER_API_KEY")
        if not worker_api_key or worker_api_key == "dummy":
            missing_required.append({
                "key": "WORKER_API_KEY",
                "label": "Worker API key (Anthropic)",
                "reason": "Required for Claude Code mode — this is your Anthropic API key.",
            })
    else:  # opencode
        worker_llm_url = get_setting_or_env("WORKER_LLM_URL")
        if not worker_llm_url:
            missing_required.append({
                "key": "WORKER_LLM_URL",
                "label": "Worker LLM URL",
                "reason": "Required for OpenCode worker mode. No default — set to your LLM base URL (e.g. http://your-host:8080/v1).",
            })

        worker_model = get_setting_or_env("WORKER_MODEL")
        if not worker_model:
            missing_required.append({
                "key": "WORKER_MODEL",
                "label": "Worker model",
                "reason": "Required for OpenCode worker mode. No default — set to your model name.",
            })

        worker_api_key_oc = get_value("WORKER_API_KEY")
        if not worker_api_key_oc:
            missing_required.append({
                "key": "WORKER_API_KEY",
                "label": "Worker API key",
                "reason": "Required for OpenCode worker mode. Use your LLM provider key or 'dummy' for local services that skip auth.",
            })

    # -------------------------------------------------------------------------
    # Embeddings (ticket/graph/note search and HippoRAG memory)
    # -------------------------------------------------------------------------

    embedding_provider = (get_setting_or_env("EMBEDDING_PROVIDER") or "openai").strip().lower()

    # Embedding model always required (no useful default for custom; optional for OpenAI but good practice)
    embedding_model = get_setting_or_env("MEMORY_EMBEDDING_MODEL")
    if not embedding_model:
        missing_required.append({
            "key": "MEMORY_EMBEDDING_MODEL",
            "label": "Embedding model",
            "reason": "Required. No default — set to your embedding model name (e.g. text-embedding-3-small for OpenAI).",
        })

    openai_api_key = get_value("openai_api_key")

    if embedding_provider == "openai":
        # OpenAI: just need openai_api_key; URL is handled by the SDK
        if not openai_api_key:
            missing_required.append({
                "key": "openai_api_key",
                "label": "OpenAI API key (embeddings)",
                "reason": "Required for OpenAI embeddings. Set openai_api_key (sk-...).",
            })
    else:
        # Custom: need URL and API key
        embedding_service_url = get_setting_or_env("EMBEDDING_SERVICE_URL")
        if not embedding_service_url:
            missing_required.append({
                "key": "EMBEDDING_SERVICE_URL",
                "label": "Embedding service URL",
                "reason": "Required for custom embedding provider. Set to your embedding endpoint base URL.",
            })

        embedding_api_key = get_value("EMBEDDING_API_KEY")
        if not embedding_api_key and not openai_api_key:
            missing_required.append({
                "key": "EMBEDDING_API_KEY",
                "label": "Embedding API key",
                "reason": "Required for custom embedding provider. Set EMBEDDING_API_KEY or openai_api_key as a substitute. Use 'dummy' for local services that skip auth.",
            })

    # -------------------------------------------------------------------------
    # HippoRAG memory LLM
    # Falls back to Agent LLM settings, so only warn when a custom
    # MEMORY_LLM_BASE_URL is set without a corresponding API key.
    # -------------------------------------------------------------------------

    memory_llm_url = get_setting_or_env("MEMORY_LLM_BASE_URL")
    memory_api_key = get_value("MEMORY_LLM_API_KEY")
    if memory_llm_url and not memory_api_key and not agent_api_key and not openai_api_key:
        warnings.append({
            "key": "MEMORY_LLM_API_KEY",
            "label": "Memory LLM API key",
            "reason": "MEMORY_LLM_BASE_URL is set but no API key is configured for it. Set MEMORY_LLM_API_KEY, AGENT_API_KEY, or openai_api_key.",
        })

    return {
        "ready": len(missing_required) == 0,
        "missing_required": missing_required,
        "warnings": warnings,
    }
