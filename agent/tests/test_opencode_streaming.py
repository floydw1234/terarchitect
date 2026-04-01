"""
Tests for the OpenCode streaming worker (prompt_async + SSE /global/event).

Covers:
  - The actual OpenCode wire format: data-only events (no "event:" prefix),
    type embedded in the JSON payload.
  - GET /global/event is used (not /event) so session events are received
    regardless of which directory instance the session was initialised in.
  - SSE connection opens BEFORE prompt_async fires (race-condition fix).
  - session.idle terminates the wait; session.status{idle} does NOT.
  - server.connected and other non-idle events are ignored.
  - Events from other sessions are filtered out.
  - Intermediate tool-call log entries are posted during the turn.
  - Falls back to synchronous /message if prompt_async returns non-204.
  - _fetch_opencode_last_message correctly extracts the last assistant message.
  - _extract_opencode_output handles text/reasoning parts.
"""
import json
import os
import sys
import threading
import time
import uuid
import unittest
from unittest.mock import MagicMock, patch, call

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _sse_lines_legacy(*events):
    """Build SSE lines in the legacy format: event: TYPE + data: PAYLOAD.
    Kept for backwards-compatibility tests only."""
    lines = []
    for evt_type, data in events:
        lines.append(f"event: {evt_type}")
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")
    return iter(lines)


def _sse_lines(*events):
    """Build SSE lines in the actual OpenCode wire format: data-only, type embedded in JSON.

    OpenCode emits:
        data: {"type":"session.idle","properties":{"sessionID":"ses_..."}}
        (blank line)
    There is no leading "event:" line.
    """
    lines = []
    for evt_type, properties in events:
        payload = {"type": evt_type, "properties": properties}
        lines.append(f"data: {json.dumps(payload)}")
        lines.append("")  # blank line = end of SSE event
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


# ---------------------------------------------------------------------------
# _extract_opencode_output
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _fetch_opencode_last_message
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# SSE event format
# ---------------------------------------------------------------------------

class TestSseEventFormat(unittest.TestCase):
    """Verify the parser handles the actual OpenCode wire format."""

    def _run_stream(self, sse_iter):
        agent = _make_agent()
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_get_response(sse_iter),
                _mock_messages_response("output"),
            ]
            return agent._stream_opencode_until_idle(
                base="http://localhost:4096",
                worker_session_id="oc-worker-sess",
                timeout_sec=30,
                project_id=None,
                ticket_id=None,
                session_id="sess",
                project_path=None,
            )

    def test_data_only_format_session_idle(self):
        """OpenCode sends data-only events (no 'event:' line); type is in JSON."""
        events = _sse_lines(
            ("session.idle", {"sessionID": "oc-worker-sess"}),
        )
        result = self._run_stream(events)
        self.assertEqual(result, "output")

    def test_server_connected_ignored(self):
        """server.connected is the first event OpenCode sends; must be ignored."""
        events = _sse_lines(
            ("server.connected", {}),
            ("session.idle", {"sessionID": "oc-worker-sess"}),
        )
        result = self._run_stream(events)
        self.assertEqual(result, "output")

    def test_session_status_idle_not_confused_with_session_idle(self):
        """session.status with status.type=idle fires before session.idle; must NOT terminate early."""
        events = _sse_lines(
            ("session.status", {"sessionID": "oc-worker-sess", "status": {"type": "idle"}}),
            ("session.idle", {"sessionID": "oc-worker-sess"}),
        )
        result = self._run_stream(events)
        self.assertEqual(result, "output")

    def test_multiple_message_part_delta_then_idle(self):
        """Many token-streaming events before idle — realistic workload."""
        events = _sse_lines(
            *[("message.part.delta", {"sessionID": "oc-worker-sess", "part": {"text": f"tok{i}"}})
              for i in range(50)],
            ("session.idle", {"sessionID": "oc-worker-sess"}),
        )
        result = self._run_stream(events)
        self.assertEqual(result, "output")

    def test_legacy_event_prefix_format_still_works(self):
        """Backwards compat: if OpenCode ever sends 'event: TYPE\\ndata: ...' format, we still handle it."""
        events = _sse_lines_legacy(
            ("session.idle", {"properties": {"sessionID": "oc-worker-sess"}}),
        )
        result = self._run_stream(events)
        self.assertEqual(result, "output")


# ---------------------------------------------------------------------------
# _stream_opencode_until_idle: session filtering and fallback
# ---------------------------------------------------------------------------

class TestStreamOpencodeUntilIdle(unittest.TestCase):
    def test_returns_output_on_session_idle(self):
        agent = _make_agent()
        sse_events = _sse_lines(
            ("session.updated", {"sessionID": "oc-worker-sess", "status": "running"}),
            ("session.idle", {"sessionID": "oc-worker-sess"}),
        )
        with patch("requests.get") as mock_get:
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
            ("session.idle", {"sessionID": "other-session"}),
            ("session.idle", {"sessionID": "oc-worker-sess"}),
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
        self.assertEqual(mock_get.call_count, 2)

    def test_posts_intermediate_logs_during_turn(self):
        agent = _make_agent()
        project_id = uuid.uuid4()
        ticket_id = uuid.uuid4()

        tick = [0.0]
        def fake_monotonic():
            tick[0] += 20.0
            return tick[0]

        sse_events = _sse_lines(
            ("message.part.updated", {"sessionID": "oc-worker-sess", "part": {"tool": "bash"}}),
            ("message.part.updated", {"sessionID": "oc-worker-sess", "part": {"tool": "read_file"}}),
            ("session.idle", {"sessionID": "oc-worker-sess"}),
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
        log_calls = [c for c in agent._backend.log.call_args_list if c[0][3] == "worker_activity"]
        self.assertGreater(len(log_calls), 0, "Should post at least one worker_activity log")

    def test_falls_back_to_fetch_on_stream_end_without_idle(self):
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


# ---------------------------------------------------------------------------
# _send_to_worker: API call ordering and endpoint validation
# ---------------------------------------------------------------------------

class TestSendToWorkerOpencodeStreaming(unittest.TestCase):

    def test_uses_global_event_endpoint(self):
        """GET /global/event must be used, NOT /event, to avoid instance-scoping issues."""
        agent = _make_agent()
        get_urls = []

        def fake_get(url, **kwargs):
            get_urls.append(url)
            if "global/event" in url or "/event" in url:
                return _mock_get_response(_sse_lines(
                    ("session.idle", {"sessionID": "oc-worker-sess"}),
                ))
            return _mock_messages_response("output")

        with patch("requests.post") as mock_post, \
             patch("requests.get", side_effect=fake_get):
            mock_post.return_value = MagicMock(status_code=204, raise_for_status=MagicMock())
            agent._send_to_worker("do the thing", "sess", project_path=None, resume=True)

        event_urls = [u for u in get_urls if "event" in u]
        self.assertTrue(
            any("/global/event" in u for u in event_urls),
            f"Expected /global/event in SSE URL, got: {event_urls}",
        )
        self.assertFalse(
            any(u.endswith("/event") and "/global/" not in u for u in event_urls),
            f"Should NOT call instance-scoped /event (without /global/), got: {event_urls}",
        )

    def test_sse_opens_before_prompt_async(self):
        """SSE connection must be established before prompt_async fires.

        This is the race-condition fix: if prompt_async fires before the SSE
        connection is open, session.idle can be missed because OpenCode's bus
        event has no listeners yet.
        """
        agent = _make_agent()
        call_order = []
        sse_connected = threading.Event()

        def fake_get(url, **kwargs):
            if "event" in url:
                call_order.append("SSE_connect")
                sse_connected.set()
                return _mock_get_response(_sse_lines(
                    ("session.idle", {"sessionID": "oc-worker-sess"}),
                ))
            return _mock_messages_response("output")

        def fake_post(url, **kwargs):
            if "prompt_async" in url:
                call_order.append("prompt_async")
            mock_resp = MagicMock()
            mock_resp.status_code = 204
            mock_resp.raise_for_status.return_value = None
            return mock_resp

        with patch("requests.get", side_effect=fake_get), \
             patch("requests.post", side_effect=fake_post):
            agent._send_to_worker("do the thing", "sess", project_path=None, resume=True)

        self.assertIn("SSE_connect", call_order)
        self.assertIn("prompt_async", call_order)
        sse_idx = call_order.index("SSE_connect")
        async_idx = call_order.index("prompt_async")
        self.assertLess(
            sse_idx, async_idx,
            f"SSE must connect before prompt_async fires. Order was: {call_order}",
        )

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
            ("session.idle", {"sessionID": "oc-worker-sess"}),
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

        with patch("requests.post", side_effect=fake_post), \
             patch("requests.get", return_value=_mock_get_response(iter([]))):
            result = agent._send_to_worker("do the thing", "sess", project_path=None, resume=True)

        self.assertEqual(result["output"], "sync output")
        sync_urls = [u for u in posted_urls if "/message" in u and "prompt_async" not in u]
        self.assertGreater(len(sync_urls), 0, "Should fall back to /message")

    def test_sse_receives_all_events_regardless_of_directory(self):
        """/global/event must work even when project_path is a deep temp directory.

        Regression: when project_path != the OpenCode default working dir, the
        instance-scoped /event endpoint would receive events from a different bus
        and miss session.idle.  /global/event has no such restriction.
        """
        agent = _make_agent()
        get_urls = []

        def fake_get(url, **kwargs):
            get_urls.append(url)
            return _mock_get_response(_sse_lines(
                ("server.connected", {}),
                ("session.idle", {"sessionID": "oc-worker-sess"}),
            ))

        with patch("requests.post") as mock_post, \
             patch("requests.get", side_effect=fake_get):
            mock_post.return_value = MagicMock(status_code=204, raise_for_status=MagicMock())
            result = agent._send_to_worker(
                "do the thing", "sess",
                project_path="/tmp/terarchitect_runner_abc123",
                resume=True,
            )

        self.assertEqual(result["output"], "")  # no messages endpoint called → empty is fine
        event_calls = [u for u in get_urls if "event" in u]
        self.assertTrue(
            any("/global/event" in u for u in event_calls),
            f"Should use /global/event even with a custom project_path. Got: {event_calls}",
        )


if __name__ == "__main__":
    unittest.main()
