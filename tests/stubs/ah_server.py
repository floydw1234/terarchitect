"""
Stub agenthub HTTP server for Phase 4 swarm integration tests.

Handles:
  GET  /health
  GET  /api/git/leaves          → []  (empty; agents skip prepare_work bundle fetch)
  POST /api/channels/{ch}/posts → store post, return 201
  GET  /api/channels/{ch}/posts → return stored posts
  GET  /api/posts               → return all posts across all channels (test helper)
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

_lock = threading.Lock()
_posts: dict[str, list] = {}  # channel -> [post, ...]
_post_id = 0


def _next_id() -> int:
    global _post_id
    _post_id += 1
    return _post_id


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def _send(self, code: int, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._send(200, {"ok": True})

        elif path == "/api/git/leaves":
            # Return empty list → agents skip bundle fetch and work from origin clone
            self._send(200, [])

        elif path.startswith("/api/channels/") and path.endswith("/posts"):
            # GET /api/channels/{channel}/posts
            parts = path.split("/")
            channel = parts[3] if len(parts) >= 5 else ""
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["100"])[0])
            with _lock:
                posts = (_posts.get(channel) or [])[-limit:]
            self._send(200, posts)

        elif path == "/api/posts":
            # Test helper: all posts across all channels
            with _lock:
                all_posts = [p for ch in _posts.values() for p in ch]
            self._send(200, all_posts)

        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/channels/") and path.endswith("/posts"):
            parts = path.split("/")
            channel = parts[3] if len(parts) >= 5 else "general"
            body = self._read_json()
            with _lock:
                post = {
                    "id": _next_id(),
                    "channel": channel,
                    "agent_id": body.get("agent_id", "agent"),
                    "content": body.get("content", ""),
                }
                _posts.setdefault(channel, []).append(post)
            self._send(201, post)

        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8098)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Stub agenthub running on port {args.port}", flush=True)
    server.serve_forever()
