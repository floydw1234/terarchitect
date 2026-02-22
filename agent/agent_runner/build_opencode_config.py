#!/usr/bin/env python3
"""Build OpenCode config JSON from WORKER_* env. Used by agent entrypoint so opencode serve
starts with the terarchitect-proxy provider (OpenCode loads providers at startup; PATCH does not add them).
Prints JSON to stdout; prints nothing if no worker settings are set.
"""
import json
import os

# Same structure as backend/scripts/test_opencode_server.py build_provider_config_from_settings()
def main() -> None:
    base_url = (os.environ.get("WORKER_LLM_URL") or "").strip().rstrip("/")
    raw_model = (os.environ.get("WORKER_MODEL") or "").strip()
    api_key = (os.environ.get("WORKER_API_KEY") or "dummy").strip() or "dummy"
    provider_id = (os.environ.get("OPENCODE_PROVIDER_ID") or "terarchitect-proxy").strip()
    if not base_url and not raw_model:
        return
    if not base_url:
        base_url = "http://localhost:8080/v1"
    if raw_model.startswith(provider_id + "/"):
        model_id = raw_model[len(provider_id) + 1 :].strip()
    else:
        model_id = (raw_model or "Qwen/Qwen3-Coder-Next-FP8").strip()
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Terarchitect Proxy",
                "options": {"baseURL": base_url, "apiKey": api_key},
                "models": {
                    model_id: {"name": model_id, "tool_call": True},
                },
            }
        },
        "model": f"{provider_id}/{model_id}",
    }
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
