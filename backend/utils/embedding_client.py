"""
OpenAI-compatible embedding client.
Points at any OpenAI-compatible embedding endpoint: real OpenAI, vLLM, Ollama, LiteLLM, etc.

Settings (via Settings UI or env):
  EMBEDDING_SERVICE_URL  — base URL of the embedding service, e.g. https://api.openai.com/v1
                           Leave blank to use OpenAI directly (https://api.openai.com/v1).
  EMBEDDING_API_KEY      — API key (Bearer token). For real OpenAI this is your sk-... key.
  MEMORY_EMBEDDING_MODEL — default model name, e.g. text-embedding-3-small.
"""
import os
from typing import List

import httpx
from openai import OpenAI


def _get_client() -> OpenAI:
    """Build an OpenAI client pointed at EMBEDDING_SERVICE_URL (or real OpenAI if unset)."""
    try:
        from utils.app_settings import get_setting_or_env
        base_url = (get_setting_or_env("EMBEDDING_SERVICE_URL") or "").strip().rstrip("/") or None
        api_key = (get_setting_or_env("EMBEDDING_API_KEY") or "").strip() or None
    except Exception:
        base_url = (os.environ.get("EMBEDDING_SERVICE_URL") or "").strip().rstrip("/") or None
        api_key = (os.environ.get("EMBEDDING_API_KEY") or "").strip() or None

    # Fall back to OPENAI_API_KEY so real OpenAI works out of the box
    if not api_key:
        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip() or None

    # openai SDK requires a non-empty api_key; use a placeholder for local services that don't check it
    effective_key = api_key or "sk-placeholder"

    http_client = httpx.Client(
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=10),
        timeout=httpx.Timeout(60.0, read=60.0),
    )
    return OpenAI(
        base_url=base_url,
        api_key=effective_key,
        http_client=http_client,
    )


def _default_model() -> str:
    try:
        from utils.app_settings import get_setting_or_env
        return (get_setting_or_env("MEMORY_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
    except Exception:
        return (os.environ.get("MEMORY_EMBEDDING_MODEL") or "text-embedding-3-small").strip()


def embed(
    texts: List[str],
    model_id: str = "",
    normalize: bool = True,
) -> List[List[float]]:
    """
    Embed one or more texts via an OpenAI-compatible /v1/embeddings endpoint.
    Returns a list of vectors (one list of floats per input text).
    normalize is accepted for interface compatibility but the service controls normalization.
    """
    if not texts:
        return []
    model = (model_id or "").strip() or _default_model()
    client = _get_client()
    response = client.embeddings.create(input=texts, model=model)
    return [item.embedding for item in response.data]


def embed_single(text: str, model_id: str = "", normalize: bool = True) -> List[float]:
    """Convenience: embed a single string and return its vector."""
    return embed([text], model_id=model_id, normalize=normalize)[0]
