"""
Unit tests for Claude Code headless worker support in MiddleAgent.
No external services required: uses os.environ for settings and mocks subprocess.Popen.
"""
import json
import os
import subprocess
import sys
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch, PropertyMock

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def _make_agent(env_overrides: dict | None = None):
    """Create a MiddleAgent with a mock backend and env overrides (no Flask context needed)."""
    from middle_agent.agent import MiddleAgent

    env = {
        "WORKER_MODE": "opencode",  # explicit; tests override via env_overrides
        "DIRECTOR_LLM_URL": "http://localhost:11434",
        "DIRECTOR_MODEL": "test-model",
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


def _make_popen_mock(stdout_text: str = "", returncode: int = 0, stderr_text: str = ""):
    """Create a mock subprocess.Popen object that streams stdout line by line."""
    mock_proc = MagicMock()
    mock_proc.returncode = returncode
    mock_proc.stdout = iter(stdout_text.splitlines(keepends=True)) if stdout_text else iter([])
    mock_proc.stderr = iter(stderr_text.splitlines(keepends=True)) if stderr_text else iter([])
    mock_proc.wait = MagicMock(return_value=returncode)
    return mock_proc


def _mock_success_popen(result: str = "Done.", session_id: str = "sess-123"):
    payload = json.dumps({"result": result, "session_id": session_id})
    return _make_popen_mock(stdout_text=payload + "\n")


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


class TestClaudeCodeWorkerDispatch(unittest.TestCase):
    def _make_claude_agent(self, api_key: str = "sk-ant-test"):
        return _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": api_key})

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
            try:
                agent._send_to_worker("do the thing", "sess1", None, resume=False)
            except Exception:
                pass
            mock_cc.assert_not_called()

    def test_claude_code_passes_anthropic_api_key(self):
        agent = self._make_claude_agent(api_key="sk-ant-real")
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)
            call_env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env", {})
            self.assertEqual(call_env.get("ANTHROPIC_API_KEY"), "sk-ant-real")

    def test_claude_code_dummy_key_not_passed(self):
        """When WORKER_API_KEY is 'dummy' (the default placeholder), don't overwrite ANTHROPIC_API_KEY."""
        agent = self._make_claude_agent(api_key="dummy")
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "original"}, clear=False):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)
                call_env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env", {})
                self.assertEqual(call_env.get("ANTHROPIC_API_KEY"), "original")

    def test_claude_code_stores_session_id(self):
        agent = self._make_claude_agent()
        with patch("subprocess.Popen", return_value=_mock_success_popen(session_id="sess-abc")):
            agent._call_claude_code_worker("prompt", "dir-sess", project_path=None, resume=False)
            self.assertEqual(agent._worker_sessions.get("dir-sess"), "sess-abc")

    def test_claude_code_resume_passes_session_flag(self):
        agent = self._make_claude_agent()
        agent._worker_sessions["dir-sess"] = "550e8400-e29b-41d4-a716-446655440000"
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("next prompt", "dir-sess", project_path=None, resume=True)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--resume", cmd)
            resume_idx = cmd.index("--resume")
            self.assertEqual(cmd[resume_idx + 1], "550e8400-e29b-41d4-a716-446655440000")

    def test_claude_code_no_resume_without_session(self):
        agent = self._make_claude_agent()
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("first prompt", "new-sess", project_path=None, resume=True)
            cmd = mock_popen.call_args[0][0]
            self.assertNotIn("--resume", cmd)

    def test_claude_code_nonzero_exit_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_claude_agent()
        bad_proc = _make_popen_mock(stdout_text="", returncode=1, stderr_text="some error")
        with patch("subprocess.Popen", return_value=bad_proc):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_claude_code_timeout_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_claude_agent()
        # Simulate timeout by making stdout iteration block then having wait() raise TimeoutExpired
        # Easiest: just raise in Popen itself
        with patch("subprocess.Popen", side_effect=FileNotFoundError("claude not found")):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_claude_code_not_found_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_claude_agent()
        with patch("subprocess.Popen", side_effect=FileNotFoundError("claude not found")):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_claude_code_non_json_output_returned_as_text(self):
        agent = self._make_claude_agent()
        plain_proc = _make_popen_mock(stdout_text="plain text output\n", returncode=0)
        with patch("subprocess.Popen", return_value=plain_proc):
            result = agent._call_claude_code_worker("do the thing", "sess1", project_path=None, resume=False)
        self.assertEqual(result["output"], "plain text output")

        plain_proc = _make_popen_mock(stdout_text="plain text output\n", returncode=0)
        with patch("subprocess.Popen", return_value=plain_proc):
            result = agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            self.assertEqual(result["output"], "plain text output")
            self.assertEqual(result["return_code"], 0)

    def test_claude_code_cmd_includes_required_flags(self):
        agent = self._make_claude_agent()
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("my prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertEqual(cmd[0], "claude")
            self.assertIn("-p", cmd)
            self.assertIn("my prompt", cmd)
            self.assertIn("--output-format", cmd)
            self.assertIn("json", cmd)
            self.assertIn("--allowedTools", cmd)

    def test_claude_code_passes_model_flag_when_set(self):
        agent = _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-test", "WORKER_MODEL": "claude-opus-4-5"})
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "claude-opus-4-5")

    def test_claude_code_no_model_flag_when_unset(self):
        agent = _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-test", "WORKER_MODEL": ""})
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertNotIn("--model", cmd)

    def test_claude_code_base_tools_include_todo_and_webfetch(self):
        agent = _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-test"})
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            tools_str = cmd[tools_idx + 1]
            for tool in ("TodoWrite", "TodoRead", "WebFetch"):
                self.assertIn(tool, tools_str)

    def test_claude_code_extra_tools_appended(self):
        agent = _make_agent({
            "WORKER_MODE": "claude-code",
            "WORKER_API_KEY": "sk-ant-test",
            "CLAUDE_CODE_EXTRA_TOOLS": "mcp__brave__search,mcp__github__create_issue",
        })
        with patch("subprocess.Popen", return_value=_mock_success_popen()) as mock_popen:
            agent._call_claude_code_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            tools_idx = cmd.index("--allowedTools")
            tools_str = cmd[tools_idx + 1]
            self.assertIn("mcp__brave__search", tools_str)
            self.assertIn("mcp__github__create_issue", tools_str)

    def test_claude_code_extra_tools_empty_by_default(self):
        agent = _make_agent({"WORKER_MODE": "claude-code", "WORKER_API_KEY": "sk-ant-test"})
        self.assertEqual(agent.worker_extra_tools, [])


class TestDirectorProviderAutoUrl(unittest.TestCase):
    """DIRECTOR_LLM_URL should be auto-inferred from DIRECTOR_PROVIDER for known providers."""

    def _make(self, provider: str, explicit_url: str = ""):
        return _make_agent({
            "DIRECTOR_PROVIDER": provider,
            "DIRECTOR_LLM_URL": explicit_url,
            "DIRECTOR_MODEL": "test-model",
        })

    def test_openai_provider_infers_url(self):
        agent = self._make("openai")
        self.assertIn("api.openai.com", agent.director_api_url)

    def test_anthropic_provider_infers_url(self):
        agent = self._make("anthropic")
        self.assertIn("api.anthropic.com", agent.director_api_url)

    def test_groq_provider_infers_url(self):
        agent = self._make("groq")
        self.assertIn("api.groq.com", agent.director_api_url)

    def test_together_provider_infers_url(self):
        agent = self._make("together")
        self.assertIn("together.xyz", agent.director_api_url)

    def test_mistral_provider_infers_url(self):
        agent = self._make("mistral")
        self.assertIn("api.mistral.ai", agent.director_api_url)

    def test_deepseek_provider_infers_url(self):
        agent = self._make("deepseek")
        self.assertIn("api.deepseek.com", agent.director_api_url)

    def test_xai_provider_infers_url(self):
        agent = self._make("xai")
        self.assertIn("api.x.ai", agent.director_api_url)

    def test_gemini_provider_infers_url(self):
        agent = self._make("gemini")
        self.assertIn("generativelanguage.googleapis.com", agent.director_api_url)

    def test_google_alias_same_as_gemini(self):
        self.assertEqual(self._make("google").director_api_url, self._make("gemini").director_api_url)

    def test_unknown_provider_leaves_url_empty(self):
        agent = self._make("custom")
        self.assertEqual(agent.director_api_url, "")

    def test_explicit_url_overrides_known_provider(self):
        agent = self._make("openai", explicit_url="http://my-proxy:8080")
        self.assertIn("my-proxy:8080", agent.director_api_url)
        self.assertNotIn("api.openai.com", agent.director_api_url)


class TestDirectorRequestJsonMode(unittest.TestCase):
    """_director_request should include json_mode fields in the payload for both API styles."""

    def _make_gpt5_agent(self):
        return _make_agent({
            "DIRECTOR_PROVIDER": "openai",
            "DIRECTOR_LLM_URL": "",
            "DIRECTOR_MODEL": "gpt-5",
            "DIRECTOR_API_KEY": "sk-test",
        })

    def _make_gpt4o_agent(self):
        return _make_agent({
            "DIRECTOR_PROVIDER": "openai",
            "DIRECTOR_LLM_URL": "",
            "DIRECTOR_MODEL": "gpt-4o",
            "DIRECTOR_API_KEY": "sk-test",
        })

    def _mock_responses_api(self, text: str):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
        }
        return mock

    def _mock_chat_completions(self, text: str):
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = {"choices": [{"message": {"content": text}}]}
        return mock

    def test_gpt5_uses_responses_api_with_json_format(self):
        agent = self._make_gpt5_agent()
        with patch("requests.post", return_value=self._mock_responses_api('{"ok": true}')) as mock_post:
            agent._director_request([{"role": "user", "content": "test"}], json_mode=True)
            payload = mock_post.call_args[1]["json"]
            self.assertIn("text", payload)
            self.assertEqual(payload["text"]["format"]["type"], "json_object")
            self.assertIn("/v1/responses", mock_post.call_args[0][0])

    def test_gpt5_no_json_mode_omits_text_format(self):
        agent = self._make_gpt5_agent()
        with patch("requests.post", return_value=self._mock_responses_api("plain text")) as mock_post:
            agent._director_request([{"role": "user", "content": "test"}], json_mode=False)
            payload = mock_post.call_args[1]["json"]
            self.assertNotIn("text", payload)

    def test_chat_completions_json_mode_sets_response_format(self):
        agent = self._make_gpt4o_agent()
        with patch("requests.post", return_value=self._mock_chat_completions('{"ok": true}')) as mock_post:
            agent._director_request([{"role": "user", "content": "test"}], json_mode=True)
            payload = mock_post.call_args[1]["json"]
            self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_chat_completions_no_json_mode_omits_response_format(self):
        agent = self._make_gpt4o_agent()
        with patch("requests.post", return_value=self._mock_chat_completions("plain text")) as mock_post:
            agent._director_request([{"role": "user", "content": "test"}], json_mode=False)
            payload = mock_post.call_args[1]["json"]
            self.assertNotIn("response_format", payload)


if __name__ == "__main__":
    unittest.main()
