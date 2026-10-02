"""OpenAI-compatible chat completion using FRONTEND_LLM_* / DIRECTOR_* settings."""

from __future__ import annotations

import re
from typing import Any

import requests

try:
    from utils.app_settings import get_frontend_llm_settings
except (ModuleNotFoundError, ImportError):
    from backend.utils.app_settings import get_frontend_llm_settings

DEFAULT_LLM_TIMEOUT_SECONDS = 120


def _uses_responses_api(model_name: str) -> bool:
    name = (model_name or "").strip()
    return name.startswith("gpt-5") or name.startswith("o3") or name.startswith("o4")


def extract_llm_text(raw: dict[str, Any], *, model_name: str) -> str:
    """Parse assistant text from /chat/completions or /responses payloads."""
    if _uses_responses_api(model_name):
        output_items = raw.get("output") or []
        content = ""
        for item in output_items:
            if isinstance(item, dict):
                for part in (item.get("content") or []):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        content += part.get("text", "")
                    elif isinstance(part, str):
                        content += part
        if not content:
            content = raw.get("output_text", "")
        return content or ""

    return (raw.get("choices", [{}])[0].get("message", {}) or {}).get("content", "") or ""


_VERSION_SUFFIX = re.compile(r"/v\d+[a-z0-9]*(?:/openai)?$")


def normalize_llm_base_url(url: str | None) -> str:
    """Return an OpenAI-compatible base URL ending in a version segment.

    Matches the Director's convention (``DIRECTOR_LLM_URL`` + ``/v1/chat/completions``):
    ``https://openrouter.ai/api`` -> ``https://openrouter.ai/api/v1``. URLs that already end
    in a version segment (``/v1``, ``/v1beta/openai``) are kept; a full
    ``.../chat/completions`` endpoint is trimmed back to its base.
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        return "https://api.openai.com/v1"
    for suffix in ("/chat/completions", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
    if not _VERSION_SUFFIX.search(base):
        base = f"{base}/v1"
    return base


def strip_json_fences(content: str) -> str:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return text.strip()


def complete_user_prompt(
    prompt: str,
    *,
    timeout_seconds: int = DEFAULT_LLM_TIMEOUT_SECONDS,
    temperature: float = 0,
    max_tokens: int = 2048,
) -> tuple[str, str]:
    """Return (assistant_text, model_name). Raises on HTTP or config errors."""
    llm = get_frontend_llm_settings()
    model_name = (llm.get("model") or "").strip()
    if not model_name:
        raise ValueError("No LLM model configured (FRONTEND_LLM_MODEL or DIRECTOR_MODEL)")

    llm_url = normalize_llm_base_url(llm.get("url"))
    headers = {"Content-Type": "application/json"}
    api_key = llm.get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if _uses_responses_api(model_name):
        api_url = f"{llm_url}/responses"
        payload = {
            "model": model_name,
            "input": prompt,
        }
    else:
        api_url = f"{llm_url}/chat/completions"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout_seconds)
    resp.raise_for_status()
    raw = resp.json()
    return extract_llm_text(raw, model_name=model_name), model_name
