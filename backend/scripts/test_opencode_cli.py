#!/usr/bin/env python3
"""
Quick test: run OpenCode CLI with a simple prompt and print where output goes.
Usage: from backend dir, run: python scripts/test_opencode_cli.py
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


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


backend_dir = Path(__file__).resolve().parent.parent
load_dotenv(backend_dir / ".env")

cmd_str = os.environ.get("OPENCODE_CMD", "opencode")
cmd = cmd_str.split() if isinstance(cmd_str, str) else [cmd_str]
session_id = f"ses-{uuid.uuid4()}"
default_prompt = "What is 2+2? Reply with only the number."
tool_prompt = (
    "Read backend/README.md and give exactly one short key point from that file. "
    "Then do a brief web_search for 'FastAPI read receipt best practices' and give exactly one short finding. "
    "Use tools directly. Respond with exactly 2 bullets, each <= 12 words, no extra text."
)
prompt = os.environ.get("OPENCODE_TEST_PROMPT", "").strip()
if not prompt:
    prompt = tool_prompt if os.environ.get("OPENCODE_TOOL_TEST", "").strip().lower() in {"1", "true", "yes"} else default_prompt

provider_id = os.environ.get("OPENCODE_PROVIDER_ID", "terarchitect-proxy")
base_url = os.environ.get("OPENCODE_BASE_URL", "http://localhost:8080/v1")
api_key = os.environ.get("OPENCODE_API_KEY", "dummy")
model = os.environ.get(
    "OPENCODE_MODEL",
    f"{provider_id}/{os.environ.get('AGENT_MODEL', 'Qwen/Qwen3-Coder-Next-FP8')}",
)
local_model_name = model[len(provider_id) + 1 :] if model.startswith(f"{provider_id}/") else model

full_cmd = [
    *cmd,
    "run",
    "--format",
    "json",
    "--model",
    model,
    prompt,
]

# OpenCode requires an existing session for --session. For this smoke test we
# start a fresh run and avoid forcing a session id.
if os.environ.get("OPENCODE_TEST_CONTINUE", "").strip().lower() in {"1", "true", "yes"}:
    full_cmd.insert(2, "--continue")

env = dict(os.environ)
env.setdefault(
    "OPENCODE_CONFIG_CONTENT",
    json.dumps(
        {
            "model": f"{provider_id}/{local_model_name}",
            "provider": {
                provider_id: {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Terarchitect Proxy",
                    "options": {
                        "baseURL": base_url,
                        "apiKey": api_key,
                    },
                    "models": {
                        local_model_name: {
                            "name": local_model_name,
                            "tool_call": True,
                        }
                    },
                }
            }
        }
    ),
)

print("Command:", " ".join(full_cmd), flush=True)
print("Prompt:", repr(prompt), flush=True)
print("Model:", model, flush=True)
print("Proxy base URL:", base_url, flush=True)
print("-" * 60, flush=True)

timeout_sec = int(os.environ.get("OPENCODE_CLI_TEST_TIMEOUT", "120"))
try:
    r = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=env,
    )
except subprocess.TimeoutExpired as e:
    print(f"TIMEOUT after {timeout_sec}s (LLM/proxy may be slow or unreachable)")
    out = getattr(e, "stdout", None) or getattr(e, "output", None)
    err = getattr(e, "stderr", None)
    if out:
        s = out.decode() if isinstance(out, bytes) else out
        print("stdout before timeout:", repr(s[:500]))
    if err:
        s = err.decode() if isinstance(err, bytes) else err
        print("stderr before timeout:", repr(s[:500]))
    sys.exit(1)
except FileNotFoundError:
    print("ERROR: OpenCode CLI not found. Is 'opencode' on PATH?")
    sys.exit(1)

stdout = (r.stdout or "").strip()
stderr = (r.stderr or "").strip()

print("returncode:", r.returncode)
print("len(stdout):", len(stdout))
print("len(stderr):", len(stderr))
print()
show_raw = os.environ.get("OPENCODE_SHOW_RAW", "").strip().lower() in {"1", "true", "yes"}
preview_chars = int(os.environ.get("OPENCODE_STDOUT_PREVIEW_CHARS", "1600"))
print("--- stdout ---")
if not stdout:
    print("(empty)")
elif show_raw:
    print(stdout)
else:
    truncated = stdout[:preview_chars]
    print(truncated)
    if len(stdout) > preview_chars:
        print(f"\n... (stdout truncated, set OPENCODE_SHOW_RAW=1 to print all {len(stdout)} chars)")
print()
print("--- stderr ---")
if not stderr:
    print("(empty)")
elif show_raw:
    print(stderr)
else:
    truncated = stderr[:preview_chars]
    print(truncated)
    if len(stderr) > preview_chars:
        print(f"\n... (stderr truncated, set OPENCODE_SHOW_RAW=1 to print all {len(stderr)} chars)")

if "--format" in full_cmd and "json" in full_cmd:
    # Summarize event stream so tool-call behavior is obvious at a glance.
    tool_events = []
    text_parts = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        evt_type = evt.get("type")
        part = evt.get("part") or {}
        if evt_type and ("tool" in evt_type or "tool" in str(part.get("type", "")).lower()):
            tool_events.append(evt)
        if evt_type == "text":
            t = (part.get("text") or "").strip()
            if t:
                text_parts.append(t)

    print()
    print("--- parsed summary ---")
    print("tool_event_count:", len(tool_events))
    if text_parts:
        joined = " ".join(text_parts)
        print("text_preview:", joined[:500])
