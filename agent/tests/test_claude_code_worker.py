"""
Unit tests for Claude Code headless worker support in MiddleAgent.
No external services required: uses os.environ for settings and mocks subprocess.run.
"""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def _make_agent(env_overrides: dict | None = None):
    """Create a MiddleAgent with a mock backend and env overrides (no Flask context needed)."""
    from middle_agent.agent import MiddleAgent

    env = {
        "WORKER_MODE": "opencode",  # explicit; tests override via env_overrides
        "AGENT_LLM_URL": "http://localhost:8000",
        "WORKER_LLM_URL": "http://localhost:8080/v1",
        "WORKER_MODEL": "gpt-4o",
        "WORKER_API_KEY": "dummy",
    }
    if env_overrides:
        env.update(env_overrides)

    backend = MagicMock()
    with patch.dict(os.environ, env, clear=False):
        agent = MiddleAgent(backend=backend)
    return agent


class TestWorkerModeInit(unittest.TestCase):
    def test_default_mode_is_opencode(self):
        agent = _make_agent()
        self.assertEqual(agent.worker_mode, "opencode")

    def test_claude_code_mode_set_from_env(self):
        agent = _make_agent({"WORKER_MODE": "claude-code"})
        self.assertEqual(agent.worker_mode, "claude-code")

    def test_invalid_mode_falls_back_to_claude_code(self):
        agent = _make_agent({"WORKER_MODE": "unknown-mode"})
        self.assertEqual(agent.worker_mode, "claude-code")

    def test_apply_agent_settings_updates_worker_mode(self):
        agent = _make_agent({"WORKER_MODE": "opencode"})
        agent._apply_agent_settings({"WORKER_MODE": "claude-code"})
        self.assertEqual(agent.worker_mode, "claude-code")

    def test_apply_agent_settings_rejects_invalid_mode(self):
        agent = _make_agent({"WORKER_MODE": "opencode"})
        agent._apply_agent_settings({"WORKER_MODE": "bad-value"})
        self.assertEqual(agent.worker_mode, "claude-code")


class TestClaudeCodeWorkerDispatch(unittest.TestCase):
    def _make_claude_agent(self, api_key: str = "sk-ant-test"):
        return _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": api_key})

    def _mock_success(self, result: str = "Done.", session_id: str = "sess-123"):
        payload = json.dumps({"result": result, "session_id": session_id})
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = payload
        mock_result.stderr = ""
        return mock_result

    def test_send_to_worker_dispatches_to_claude_code(self):
        agent = self._make_claude_agent()
        with patch.object(agent, "_call_claude_code_worker", return_value={"output": "ok", "error": "", "return_code": 0}) as mock_cc:
            agent._send_to_worker("do the thing", "sess1", "/tmp/repo", resume=False)
            mock_cc.assert_called_once_with("do the thing", "sess1", "/tmp/repo", False)

    def test_send_to_worker_opencode_does_not_call_claude_code(self):
        agent = _make_agent({"WORKER_MODE": "opencode"})
        with patch.object(agent, "_call_claude_code_worker") as mock_cc, \
             patch.object(agent, "_worker_sessions", {}), \
             patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_resp.json.return_value = {"id": "oc-sess"}
            mock_post.return_value = mock_resp
            # Won't succeed fully (message post would also need mocking) but we only care claude code not called
            try:
                agent._send_to_worker("do the thing", "sess1", None, resume=False)
            except Exception:
                pass
            mock_cc.assert_not_called()

    def test_claude_code_passes_anthropic_api_key(self):
        agent = self._make_claude_agent(api_key="sk-ant-real")
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)
            call_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env", {})
            self.assertEqual(call_env.get("ANTHROPIC_API_KEY"), "sk-ant-real")

    def test_claude_code_dummy_key_not_passed(self):
        """When WORKER_API_KEY is 'dummy' (the default placeholder), don't overwrite ANTHROPIC_API_KEY."""
        agent = self._make_claude_agent(api_key="dummy")
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original"}, clear=False):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)
                call_env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env", {})
                self.assertEqual(call_env.get("ANTHROPIC_API_KEY"), "original")

    def test_claude_code_stores_session_id(self):
        agent = self._make_claude_agent()
        with patch("subprocess.run", return_value=self._mock_success(session_id="sess-abc")):
            agent._call_claude_code_worker("prompt", "dir-sess", project_path=None, resume=False)
            self.assertEqual(agent._worker_sessions.get("dir-sess"), "sess-abc")

    def test_claude_code_resume_passes_session_flag(self):
        agent = self._make_claude_agent()
        agent._worker_sessions["dir-sess"] = "existing-sess"
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            agent._call_claude_code_worker("next prompt", "dir-sess", project_path=None, resume=True)
            cmd = mock_run.call_args[0][0]
            self.assertIn("--resume", cmd)
            resume_idx = cmd.index("--resume")
            self.assertEqual(cmd[resume_idx + 1], "existing-sess")

    def test_claude_code_no_resume_without_session(self):
        agent = self._make_claude_agent()
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            agent._call_claude_code_worker("first prompt", "new-sess", project_path=None, resume=True)
            cmd = mock_run.call_args[0][0]
            self.assertNotIn("--resume", cmd)

    def test_claude_code_nonzero_exit_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_claude_agent()
        bad_result = MagicMock()
        bad_result.returncode = 1
        bad_result.stdout = ""
        bad_result.stderr = "some error"
        with patch("subprocess.run", return_value=bad_result):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_claude_code_timeout_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_claude_agent()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=3600)):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_claude_code_not_found_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_claude_agent()
        with patch("subprocess.run", side_effect=FileNotFoundError("claude not found")):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_claude_code_non_json_output_returned_as_text(self):
        agent = self._make_claude_agent()
        plain_result = MagicMock()
        plain_result.returncode = 0
        plain_result.stdout = "plain text output"
        plain_result.stderr = ""
        with patch("subprocess.run", return_value=plain_result):
            result = agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            self.assertEqual(result["output"], "plain text output")
            self.assertEqual(result["return_code"], 0)

    def test_claude_code_cmd_includes_required_flags(self):
        agent = self._make_claude_agent()
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            agent._call_claude_code_worker("my prompt", "sess1", project_path=None, resume=False)
            cmd = mock_run.call_args[0][0]
            self.assertEqual(cmd[0], "claude")
            self.assertIn("-p", cmd)
            self.assertIn("my prompt", cmd)
            self.assertIn("--output-format", cmd)
            self.assertIn("json", cmd)
            self.assertIn("--allowedTools", cmd)

    def test_claude_code_passes_model_flag_when_set(self):
        agent = _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-test", "WORKER_MODEL": "claude-opus-4-5"})
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_run.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-opus-4-5")

    def test_claude_code_no_model_flag_when_unset(self):
        agent = _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-test", "WORKER_MODEL": ""})
        with patch("subprocess.run", return_value=self._mock_success()) as mock_run:
            agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_run.call_args[0][0]
            self.assertNotIn("--model", cmd)


if __name__ == "__main__":
    unittest.main()
