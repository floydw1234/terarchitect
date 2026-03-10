"""
Tests for the OpenCode streaming worker (prompt_async + SSE /event).

Verifies:
  - prompt_async is called instead of /message
  - SSE stream is consumed and session.idle terminates the wait
  - Intermediate tool-call log entries are posted during the turn
  - Falls back to synchronous /message if prompt_async returns non-204
  - _fetch_opencode_last_message correctly extracts the last assistant message
  - _extract_opencode_output handles text/reasoning parts
"""
import json
import os
import sys
import uuid
import unittest
from unittest.mock import MagicMock, patch, call

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def _make_agent(env_overrides=None):
    from middle_agent.agent import MiddleAgent

    env = {
        "WORKER_MODE": "opencode",
        "AGENT_LLM_URL": "http://localhost:8000",
        "AGENT_MODEL": "gpt-4o",
        "AGENT_API_KEY": "sk-test",
        "WORKER_LLM_URL": "http://localhost:8080/v1",
        "WORKER_MODEL": "my-model",
        "WORKER_API_KEY": "dummy",
        "MIDDLE_AGENT_DEBUG": "0",
    }
    if env_overrides:
        env.update(env_overrides)
    backend = MagicMock()
    backend.log.return_value = None
    with patch.dict(os.environ, env, clear=False):
        agent = MiddleAgent(backend=backend)
    # Seed a pre-existing worker session so session creation is skipped.
    agent._worker_sessions["sess"] = "oc-worker-sess"
    agent._worker_turn_count["sess"] = 0
    return agent


def _sse_lines(*events):
    """Build a list of decoded SSE lines from a sequence of (event_type, data_dict) tuples."""
    lines = []
    for evt_type, data in events:
        lines.append(f"event: {evt_type}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")  # blank line = end of event
    return iter(lines)


def _mock_get_response(lines, status=200):
    """Mock a streaming requests.get response whose iter_lines returns lines."""
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.raise_for_status.return_value = None
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _mock_messages_response(text="worker did the thing"):
    """Mock GET /session/:id/message response with one assistant message."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = [
        {
            "info": {"role": "assistant", "sessionID": "oc-worker-sess"},
            "parts": [{"type": "text", "text": text}],
        }
    ]
    return mock_resp


class TestExtractOpencodeOutput(unittest.TestCase):
    def setUp(self):
        from middle_agent.agent import MiddleAgent
        self.MiddleAgent = MiddleAgent

    def test_extracts_text_parts(self):
        data = {"parts": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]}
        self.assertEqual(self.MiddleAgent._extract_opencode_output(data), "hello\nworld")

    def test_extracts_reasoning_parts(self):
        data = {"parts": [{"type": "reasoning", "text": "thinking…"}, {"type": "text", "text": "done"}]}
        result = self.MiddleAgent._extract_opencode_output(data)
        self.assertIn("thinking", result)
        self.assertIn("done", result)

    def test_ignores_non_text_parts(self):
        data = {"parts": [{"type": "tool_call", "name": "bash"}, {"type": "text", "text": "ok"}]}
        self.assertEqual(self.MiddleAgent._extract_opencode_output(data), "ok")

    def test_handles_list_input(self):
        data = [
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": "prompt"}]},
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "response"}]},
        ]
        self.assertEqual(self.MiddleAgent._extract_opencode_output(data), "response")

    def test_empty_parts(self):
        self.assertEqual(self.MiddleAgent._extract_opencode_output({"parts": []}), "")


class TestFetchOpencodeLastMessage(unittest.TestCase):
    def test_returns_last_assistant_text(self):
        agent = _make_agent()
        with patch("requests.get", return_value=_mock_messages_response("hello from assistant")):
            result = agent._fetch_opencode_last_message("http://localhost:4096", "oc-worker-sess")
        self.assertEqual(result, "hello from assistant")

    def test_skips_user_messages(self):
        agent = _make_agent()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": "user prompt"}]},
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "assistant reply"}]},
        ]
        with patch("requests.get", return_value=mock_resp):
            result = agent._fetch_opencode_last_message("http://localhost:4096", "oc-worker-sess")
        self.assertEqual(result, "assistant reply")

    def test_returns_empty_on_http_error(self):
        agent = _make_agent()
        import requests as req
        with patch("requests.get", side_effect=req.RequestException("timeout")):
            result = agent._fetch_opencode_last_message("http://localhost:4096", "oc-worker-sess")
        self.assertEqual(result, "")


class TestStreamOpencodeUntilIdle(unittest.TestCase):
    def test_returns_output_on_session_idle(self):
        agent = _make_agent()
        sse_events = _sse_lines(
            ("session.updated", {"properties": {"sessionID": "oc-worker-sess", "status": "running"}}),
            ("session.idle", {"properties": {"sessionID": "oc-worker-sess"}}),
        )
        with patch("requests.get") as mock_get:
            # First call = SSE stream, second call = fetch messages
            mock_get.side_effect = [
                _mock_get_response(sse_events),
                _mock_messages_response("final output"),
            ]
            result = agent._stream_opencode_until_idle(
                base="http://localhost:4096",
                worker_session_id="oc-worker-sess",
                timeout_sec=30,
                project_id=None,
                ticket_id=None,
                session_id="sess",
                project_path=None,
            )
        self.assertEqual(result, "final output")

    def test_ignores_events_from_other_sessions(self):
        agent = _make_agent()
        sse_events = _sse_lines(
            ("session.idle", {"properties": {"sessionID": "other-session"}}),
            ("session.idle", {"properties": {"sessionID": "oc-worker-sess"}}),
        )
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_get_response(sse_events),
                _mock_messages_response("correct output"),
            ]
            result = agent._stream_opencode_until_idle(
                base="http://localhost:4096",
                worker_session_id="oc-worker-sess",
                timeout_sec=30,
                project_id=None,
                ticket_id=None,
                session_id="sess",
                project_path=None,
            )
        self.assertEqual(result, "correct output")
        # The messages fetch should happen exactly once (after the correct idle).
        self.assertEqual(mock_get.call_count, 2)

    def test_posts_intermediate_logs_during_turn(self):
        agent = _make_agent()
        project_id = uuid.uuid4()
        ticket_id = uuid.uuid4()
        # Simulate several tool events then idle, spacing them so heartbeat triggers.
        # We patch time.monotonic to control the clock.
        import time as _time_mod
        tick = [0.0]

        def fake_monotonic():
            tick[0] += 20.0  # jump 20s each call → well past 15s log_interval
            return tick[0]

        sse_events = _sse_lines(
            ("message.part.updated", {"properties": {"sessionID": "oc-worker-sess", "part": {"tool": "bash"}}}),
            ("message.part.updated", {"properties": {"sessionID": "oc-worker-sess", "part": {"tool": "read_file"}}}),
            ("session.idle", {"properties": {"sessionID": "oc-worker-sess"}}),
        )
        with patch("requests.get") as mock_get, \
             patch("time.monotonic", side_effect=fake_monotonic):
            mock_get.side_effect = [
                _mock_get_response(sse_events),
                _mock_messages_response("done"),
            ]
            agent._stream_opencode_until_idle(
                base="http://localhost:4096",
                worker_session_id="oc-worker-sess",
                timeout_sec=300,
                project_id=project_id,
                ticket_id=ticket_id,
                session_id="sess",
                project_path=None,
            )
        # Backend log should have been called with "worker_activity"
        log_calls = [c for c in agent._backend.log.call_args_list if c[0][3] == "worker_activity"]
        self.assertGreater(len(log_calls), 0, "Should post at least one worker_activity log during the turn")

    def test_falls_back_to_fetch_on_stream_end_without_idle(self):
        """If SSE stream ends without session.idle, fall back to fetching messages."""
        agent = _make_agent()
        sse_events = iter([""])  # empty stream
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_get_response(sse_events),
                _mock_messages_response("fallback output"),
            ]
            result = agent._stream_opencode_until_idle(
                base="http://localhost:4096",
                worker_session_id="oc-worker-sess",
                timeout_sec=30,
                project_id=None,
                ticket_id=None,
                session_id="sess",
                project_path=None,
            )
        self.assertEqual(result, "fallback output")


class TestSendToWorkerOpencodeStreaming(unittest.TestCase):
    def test_uses_prompt_async_not_message(self):
        """_send_to_worker (opencode mode) should POST to prompt_async, not /message."""
        agent = _make_agent()
        posted_urls = []

        def fake_post(url, **kwargs):
            posted_urls.append(url)
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_resp.raise_for_status.return_value = None
            return mock_resp

        sse_lines = _sse_lines(
            ("session.idle", {"properties": {"sessionID": "oc-worker-sess"}}),
        )
        with patch("requests.post", side_effect=fake_post), \
             patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_get_response(sse_lines),
                _mock_messages_response("output"),
            ]
            agent._send_to_worker("do the thing", "sess", project_path=None, resume=True)

        async_urls = [u for u in posted_urls if "prompt_async" in u]
        sync_urls = [u for u in posted_urls if "/message" in u and "prompt_async" not in u]
        self.assertGreater(len(async_urls), 0, "Should call prompt_async")
        self.assertEqual(len(sync_urls), 0, "Should NOT call synchronous /message")

    def test_falls_back_to_sync_message_on_prompt_async_failure(self):
        """If prompt_async fails, fall back to synchronous POST /message."""
        agent = _make_agent()
        posted_urls = []

        def fake_post(url, **kwargs):
            posted_urls.append(url)
            mock_resp = MagicMock()
            if "prompt_async" in url:
                mock_resp.status_code = 404
                mock_resp.raise_for_status.side_effect = __import__("requests").HTTPError("404")
            else:
                mock_resp.status_code = 200
                mock_resp.raise_for_status.return_value = None
                mock_resp.json.return_value = {
                    "parts": [{"type": "text", "text": "sync output"}],
                    "info": {"role": "assistant"},
                }
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            result = agent._send_to_worker("do the thing", "sess", project_path=None, resume=True)

        self.assertEqual(result["output"], "sync output")
        sync_urls = [u for u in posted_urls if "/message" in u and "prompt_async" not in u]
        self.assertGreater(len(sync_urls), 0, "Should fall back to /message")


if __name__ == "__main__":
    unittest.main()
