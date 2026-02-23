#!/usr/bin/env python3
"""
Test OpenCode HTTP server API locally (session create + message send).
Run with opencode serve in another terminal, then:

  OPENCODE_SERVER_URL=http://127.0.0.1:4096 python backend/scripts/test_opencode_server.py

Optional: OPENCODE_SERVER_PASSWORD, OPENCODE_SERVER_USERNAME (default opencode) for HTTP basic auth.
Optional: OPENCODE_MODEL=provider/model to use a specific model (else we try GET /config/providers for defaults).
Optional: OPENCODE_MESSAGE_TIMEOUT=120 (seconds for POST .../message; increase if LLM is slow).

Provider-from-settings (simulates agent docker):
  Set WORKER_LLM_URL, WORKER_MODEL, WORKER_API_KEY (and optionally OPENCODE_PROVIDER_ID, default terarchitect-proxy).

  OpenCode loads providers at startup; PATCH /config does not add new providers to the runtime. So you must either:
  - Set OPENCODE_START_SERVER=1: this script will start opencode serve with OPENCODE_CONFIG_CONTENT so the provider
    exists at startup (recommended for the test).
  - Or start opencode serve yourself with OPENCODE_CONFIG_CONTENT set to the provider JSON (script prints it if
    provider is missing after PATCH).
"""
import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Need requests: pip install requests", file=sys.stderr)
    sys.exit(1)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ.setdefault(k, v)


def build_provider_config_from_settings() -> dict | None:
    """Build OpenCode config { provider, model } from WORKER_* env (agent-style settings).
    Returns None if no worker settings are set (caller keeps server's existing config).
    """
    base_url = (os.environ.get("WORKER_LLM_URL") or "").strip().rstrip("/")
    raw_model = (os.environ.get("WORKER_MODEL") or "").strip()
    api_key = (os.environ.get("WORKER_API_KEY") or "dummy").strip() or "dummy"
    provider_id = (os.environ.get("OPENCODE_PROVIDER_ID") or "terarchitect-proxy").strip()
    if not base_url and not raw_model:
        return None
    if not base_url:
        base_url = "http://localhost:8080/v1"
    # Model: may be "providerID/modelID" (strip provider prefix) or just "modelID" (e.g. Qwen/Qwen3-Coder-Next-FP8)
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
    return payload


def _port_from_url(url: str, default: int = 4096) -> int:
    """Extract port from http(s) URL; return default if missing."""
    try:
        from urllib.parse import urlparse
        p = urlparse(url if "://" in url else f"http://{url}")
        return p.port or default
    except Exception:
        return default


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(repo_root / "backend" / ".env")

    base = (os.environ.get("OPENCODE_SERVER_URL") or "http://127.0.0.1:4096").strip().rstrip("/")
    user = (os.environ.get("OPENCODE_SERVER_USERNAME") or "opencode").strip()
    password = (os.environ.get("OPENCODE_SERVER_PASSWORD") or "").strip()
    auth = (user, password) if password else None
    model = (os.environ.get("OPENCODE_MODEL") or "").strip()

    session = requests.Session()
    session.auth = auth
    session.headers["Content-Type"] = "application/json"

    provider_config = build_provider_config_from_settings()
    started_server = False
    server_proc = None

    # 0. Optionally start opencode serve with our provider config (so provider exists at startup).
    if provider_config and (os.environ.get("OPENCODE_START_SERVER") or "").strip().lower() in ("1", "true", "yes"):
        port = _port_from_url(base, 4096)
        env = dict(os.environ)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(provider_config)
        try:
            server_proc = subprocess.Popen(
                ["opencode", "serve", "--port", str(port)],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            print("OPENCODE_START_SERVER=1 but 'opencode' not on PATH. Install OpenCode or run without OPENCODE_START_SERVER.", file=sys.stderr)
            sys.exit(1)

        def _kill_server() -> None:
            if server_proc and server_proc.poll() is None:
                server_proc.terminate()
                try:
                    server_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_proc.kill()

        atexit.register(_kill_server)
        print(f"Started opencode serve on port {port} with provider config; waiting for health...")
        for _ in range(30):
            try:
                r = session.get(f"{base}/global/health", timeout=2)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        else:
            stderr = (server_proc.stderr and server_proc.stderr.read()) or b""
            print(f"Server did not become healthy in time. stderr: {stderr.decode()[:500]}", file=sys.stderr)
            sys.exit(1)
        started_server = True
        print("Server healthy.")

    # 1. Health
    try:
        r = session.get(f"{base}/global/health", timeout=5)
        r.raise_for_status()
        health = r.json()
        print(f"Health: {health}")
    except requests.RequestException as e:
        print(f"Health check failed: {e}")
        if hasattr(e, "response") and e.response is not None and e.response.text:
            print(f"Response: {e.response.text[:500]}")
        sys.exit(1)

    # 1b. Use provider from settings: either we started server with it, or PATCH (then verify server has it).
    if provider_config:
        if not started_server:
            try:
                r = session.patch(f"{base}/config", json=provider_config, timeout=10)
                r.raise_for_status()
                print("PATCH /config OK (provider from WORKER_* settings)")
            except requests.RequestException as e:
                print(f"PATCH /config failed: {e}")
                if getattr(e, "response", None) is not None and e.response is not None:
                    print("Response:", e.response.text[:500])
                sys.exit(1)
            # OpenCode loads providers at startup; PATCH may not add new providers to runtime. Verify.
            prov_id = next(iter(provider_config.get("provider") or {}), None)
            model_full = provider_config.get("model") or ""
            _, _, mod_id = model_full.partition("/")
            if not mod_id:
                mod_id = model_full
            try:
                r = session.get(f"{base}/config/providers", timeout=5)
                r.raise_for_status()
                cfg = r.json()
                providers_val = cfg.get("providers")
                # API can return providers as list of {id, models} or as dict keyed by id.
                if isinstance(providers_val, list):
                    found = any(
                        (p.get("id") == prov_id or p.get("providerID") == prov_id)
                        and (mod_id in (p.get("models") or {}))
                        for p in providers_val
                        if isinstance(p, dict)
                    )
                elif isinstance(providers_val, dict):
                    found = prov_id in providers_val and mod_id in (providers_val.get(prov_id) or {}).get("models", {})
                else:
                    found = False
                if not found:
                    # Provider not in runtime registry; tell user to start server with config.
                    print("Provider not registered (OpenCode loads providers at startup). Start the server with:", file=sys.stderr)
                    port = _port_from_url(base, 4096)
                    json_one_line = json.dumps(provider_config)
                    print("  OPENCODE_CONFIG_CONTENT='%s' opencode serve --port %s" % (
                        json_one_line.replace("'", "'\"'\"'"),
                        port,
                    ), file=sys.stderr)
                    print("Or run this script with OPENCODE_START_SERVER=1 to start the server automatically.", file=sys.stderr)
                    sys.exit(1)
            except requests.RequestException as e:
                print(f"GET /config/providers failed: {e}; continuing anyway.")
        # Use the model we configured
        full_model = provider_config.get("model") or "terarchitect-proxy/Qwen/Qwen3-Coder-Next-FP8"
        prov_id, _, mod_id = full_model.partition("/")
        if not mod_id:
            mod_id = full_model
            prov_id = "terarchitect-proxy"
        model_obj = {"providerID": prov_id, "modelID": mod_id}
        print(f"Using model from provider config: {model_obj}")
    else:
        # 2. Resolve model: API expects { providerID, modelID }, not a string.
        if model and "/" in model:
            provider_id, _, model_id = model.partition("/")
            model_obj = {"providerID": provider_id, "modelID": model_id}
        elif model:
            model_obj = {"providerID": "terarchitect-proxy", "modelID": model}
        else:
            try:
                r = session.get(f"{base}/config/providers", timeout=5)
                r.raise_for_status()
                cfg = r.json()
                defaults = cfg.get("default") or {}
                if defaults:
                    first = next(iter(defaults.items()), None)
                    if first:
                        model_obj = {"providerID": first[0], "modelID": first[1]}
                        print(f"Using default model from server: {model_obj}")
                    else:
                        model_obj = {"providerID": "terarchitect-proxy", "modelID": "Qwen/Qwen3-Coder-Next-FP8"}
                else:
                    model_obj = {"providerID": "terarchitect-proxy", "modelID": "Qwen/Qwen3-Coder-Next-FP8"}
                    print(f"No default from server; using: {model_obj}")
            except requests.RequestException as e:
                print(f"Config/providers failed: {e}; using fallback model")
                model_obj = {"providerID": "terarchitect-proxy", "modelID": "Qwen/Qwen3-Coder-Next-FP8"}

    # 3. Create session
    try:
        r = session.post(
            f"{base}/session",
            json={"title": "test-terarchitect"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"POST /session failed: {r.status_code}")
            print(r.text[:1000])
            sys.exit(1)
        data = r.json()
        session_id = data.get("id") or data.get("sessionID") or ""
        if not session_id:
            print("POST /session returned no id:", data)
            sys.exit(1)
        print(f"Session id: {session_id}")
    except requests.RequestException as e:
        print(f"Session create failed: {e}")
        if getattr(e, "response", None) and e.response is not None:
            print("Response body:", e.response.text[:800])
        sys.exit(1)

    # 4. Send message (same format as agent: model is object { providerID, modelID })
    body = {
        "parts": [{"type": "text", "text": "Say hello in exactly one word."}],
        "model": model_obj,
    }
    msg_timeout = int(os.environ.get("OPENCODE_MESSAGE_TIMEOUT", "120"))
    print(f"POST /session/{session_id}/message body: {json.dumps(body, indent=2)}")
    try:
        r = session.post(
            f"{base}/session/{session_id}/message",
            json=body,
            timeout=msg_timeout,
        )
        if r.status_code != 200:
            print(f"POST /session/.../message failed: {r.status_code} {r.reason}")
            print("Response body (full):")
            print(r.text)
            sys.exit(1)
        print(f"Response: status={r.status_code}, len(body)={len(r.text)}, Content-Type={r.headers.get('Content-Type')}")
        if not r.text or not r.text.strip():
            print("Response 200 but body is empty. Server may have timed out or provider/model not configured.")
            print("Response headers:", dict(r.headers))
            print("Verify: 1) LLM at WORKER_LLM_URL is running and has this model. 2) Try OPENCODE_MESSAGE_TIMEOUT=300.")
            sys.exit(1)
        try:
            data = r.json()
        except json.JSONDecodeError as e:
            print(f"Response is not JSON: {e}")
            print("Raw body (first 1000 chars):", repr(r.text[:1000]))
            print("Content-Type:", r.headers.get("Content-Type"))
            sys.exit(1)
        parts = data.get("parts") or []
        text_bits = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") in ("text", "reasoning")]
        output = "\n".join(t for t in text_bits if t).strip()
        print("Response parts count:", len(parts))
        print("Part types:", [p.get("type") for p in parts if isinstance(p, dict)])
        print("Output text:", repr(output[:500]) if output else "(empty)")
        if not output and parts:
            print("Raw first part keys:", list(parts[0].keys()) if parts else None)
    except requests.RequestException as e:
        print(f"Message send failed: {e}")
        if getattr(e, "response", None) and e.response is not None:
            print("Response status:", e.response.status_code)
            print("Response body:", e.response.text[:1000])
        sys.exit(1)

    print("OK")


if __name__ == "__main__":
    main()
