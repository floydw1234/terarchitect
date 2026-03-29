#!/usr/bin/env python3
"""
Stub OpenAI-compatible LLM server for integration tests.

Mimics POST /v1/chat/completions and returns canned responses that drive the
Director state machine through all phases without any real LLM call.

Response logic (detected from the request's system/user message content):
  - system contains "single-line commit message"
      → plain text commit message
  - system contains "PR descriptions"
      → plain text PR description paragraph
  - user message contains "Judge the plan"
      → plan_approved JSON (always approves)
  - otherwise (execution assess)
      → {"complete": false, ...} on the first assess call (0 prior assistant messages)
      → {"complete": true, "summary": "..."} on subsequent assess calls

Run with: python tests/stubs/llm_server.py [--port 8099]
"""

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


def _build_response(messages: list) -> str:
    """Return the assistant content string for the given message list."""
    system = next(
        (m.get("content", "") for m in messages if m.get("role") == "system"), ""
    )
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    last_user = user_msgs[-1] if user_msgs else ""

    # Commit message generation
    if "single-line commit message" in system:
        return "Add stub implementation output"

    # PR description generation
    if "PR descriptions" in system or "PR description" in system:
        return "Stub worker created stub_output.txt to satisfy the ticket requirements."

    # Plan review — approve unconditionally
    if "plan_approved" in system or "Judge the plan" in last_user:
        return json.dumps({
            "plan_approved": True,
            "approved_plan_text": (
                "1. Write stub_output.txt to the project root with content 'stub complete'.\n"
                "2. Verify the file exists.\n"
                "3. Done."
            ),
            "feedback": "Plan is straightforward and complete.",
        })

    # Execution assess — check how many assistant turns have happened
    assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
    if assistant_count == 0:
        # Turn 0: cannot be complete yet — must send at least one execution prompt
        return json.dumps({
            "complete": False,
            "next_prompt": (
                "Implement the approved plan now. "
                "Write stub_output.txt to the project root with the content 'stub complete'."
            ),
        })
    # Turn 1+: mark complete
    return json.dumps({
        "complete": True,
        "summary": "Stub worker completed the task. stub_output.txt created successfully.",
    })


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Health / models endpoints
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok", "data": []}).encode())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._error(400, "invalid JSON")
            return

        messages = body.get("messages", [])
        content = _build_response(messages)

        response = {
            "id": "stub-chatcmpl-000",
            "object": "chat.completion",
            "model": body.get("model", "stub-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 50,
                "total_tokens": 250,
            },
        }
        encoded = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, code: int, msg: str):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # silence access log
        pass


def main():
    parser = argparse.ArgumentParser(description="Stub LLM server for integration tests")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), _Handler)
    print(f"[stub-llm] listening on {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[stub-llm] stopped", flush=True)


if __name__ == "__main__":
    main()
