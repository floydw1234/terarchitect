"""
Unit tests for Codex CLI worker support in MiddleAgent.
No external services required: uses os.environ for settings and mocks subprocess.Popen.
"""
import json
import os
import subprocess
import sys
import tempfile
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
        "DIRECTOR_LLM_URL": "http://localhost:8000",
        "DIRECTOR_MODEL": "test-model",
        "WORKER_LLM_URL": "http://localhost:8080/v1",
        "WORKER_MODEL": "gpt-4o",
        "WORKER_API_KEY": "dummy",
        "CODEX_EXTRA_FLAGS": "",
        "CODEX_SANDBOX": "workspace-write",
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


class TestPromptStack(unittest.TestCase):
    def test_agent_system_prompt_routes_coding_work_through_codex(self):
        from middle_agent.agent import get_agent_system_prompt

        prompt = get_agent_system_prompt()
        self.assertIn("Prefer Codex for implementation-heavy coding work.", prompt)
        self.assertIn("Do not directly freehand implementation code", prompt)
        self.assertIn("research -> planning -> plan review -> execution", prompt)

    def test_agent_system_prompt_requires_dynamic_ports_for_integration_services(self):
        from middle_agent.agent import get_agent_system_prompt

        prompt = get_agent_system_prompt()
        self.assertIn("prefer OS-assigned/free dynamic localhost ports".lower(), prompt.lower())
        self.assertIn("inject BASE_URL/PORT", prompt)
        self.assertIn("unique compose project name", prompt)

    def test_worker_first_prompt_requires_dynamic_ports_for_integration_services(self):
        from middle_agent.agent import _load_prompts

        prompt = _load_prompts()["worker_first_prompt_prefix"]
        self.assertIn("prefer OS-assigned/free dynamic localhost ports".lower(), prompt.lower())
        self.assertIn("inject BASE_URL/PORT", prompt)
        self.assertIn("unique compose project name", prompt)

    def test_worker_plan_prompt_requires_dynamic_ports_for_integration_services(self):
        from middle_agent.agent import get_worker_plan_prompt_prefix

        prompt = get_worker_plan_prompt_prefix(task_plan_path="/tmp/task-plan.md")
        self.assertIn("prefer OS-assigned/free dynamic localhost ports".lower(), prompt.lower())
        self.assertIn("inject BASE_URL/PORT", prompt)
        self.assertIn("unique compose project name", prompt)

    def test_task_plan_path_falls_back_without_project_path(self):
        from middle_agent.agent import _get_task_plan_path

        ticket_id = "12345678-1234-5678-1234-567812345678"
        path = _get_task_plan_path(None, ticket_id)

        self.assertEqual(
            path,
            os.path.join(tempfile.gettempdir(), "terarchitect-middle-agent", "plan", f"{ticket_id}_task_plan.md"),
        )



class TestCodexWorkerDispatch(unittest.TestCase):
    def _make_codex_agent(self, api_key: str = "sk-test"):
        return _make_agent({"WORKER_MODE": "codex", "WORKER_API_KEY": api_key})

    def test_send_to_worker_dispatches_to_codex(self):
        agent = self._make_codex_agent()
        with patch.object(agent, "_call_codex_worker", return_value={"output": "ok", "error": "", "return_code": 0}) as mock_codex:
            agent._send_to_worker("do the thing", "sess1", "/tmp/repo", resume=False)
            mock_codex.assert_called_once_with("do the thing", "sess1", "/tmp/repo", False)

    def test_send_to_worker_opencode_does_not_call_codex(self):
        agent = _make_agent({"WORKER_MODE": "opencode"})
        with patch.object(agent, "_call_codex_worker") as mock_codex, \
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
            mock_codex.assert_not_called()

    def test_codex_passes_openai_api_key(self):
        agent = self._make_codex_agent(api_key="sk-real")
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("do the thing", "sess1", project_path=None, resume=False)
            call_env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env", {})
            self.assertEqual(call_env.get("OPENAI_API_KEY"), "sk-real")

    def test_codex_dummy_key_not_passed(self):
        agent = self._make_codex_agent(api_key="dummy")
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            with patch.dict(os.environ, {"OPENAI_API_KEY": "original"}, clear=False):
                agent._call_codex_worker("do the thing", "sess1", project_path=None, resume=False)
                call_env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env", {})
                self.assertEqual(call_env.get("OPENAI_API_KEY"), "original")

    def test_codex_no_session_resume(self):
        agent = self._make_codex_agent()
        agent._worker_sessions["dir-sess"] = "worker-session"
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("next prompt", "dir-sess", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertNotIn("--resume", cmd)
            self.assertNotIn("worker-session", cmd)

    def test_codex_nonzero_exit_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_codex_agent()
        bad_proc = _make_popen_mock(stdout_text="", returncode=1, stderr_text="some error")
        with patch("subprocess.Popen", return_value=bad_proc):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_codex_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_codex_not_found_raises_worker_unavailable(self):
        from middle_agent.agent import WorkerUnavailableError
        agent = self._make_codex_agent()
        with patch("subprocess.Popen", side_effect=FileNotFoundError("codex not found")):
            with self.assertRaises(WorkerUnavailableError):
                agent._call_codex_worker("do the thing", "sess1", project_path=None, resume=False)

    def test_codex_non_json_output_returned_as_text(self):
        agent = self._make_codex_agent()
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="plain text output\n", returncode=0)):
            result = agent._call_codex_worker("do the thing", "sess1", project_path=None, resume=False)
        self.assertEqual(result["output"], "plain text output")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["return_code"], 0)

    def test_codex_cmd_includes_required_flags(self):
        agent = self._make_codex_agent()
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("my prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertEqual(cmd[:3], ["codex", "exec", "--json"])
            self.assertIn("--sandbox", cmd)
            self.assertEqual(cmd[cmd.index("--sandbox") + 1], "workspace-write")
            self.assertIn("my prompt", cmd)

    def test_codex_passes_model_flag_when_set(self):
        agent = _make_agent({"WORKER_MODE": "codex", "WORKER_API_KEY": "sk-test", "WORKER_MODEL": "gpt-4o"})
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--model", cmd)
            model_idx = cmd.index("--model")
            self.assertEqual(cmd[model_idx + 1], "gpt-4o")

    def test_codex_no_model_flag_when_unset(self):
        agent = _make_agent({"WORKER_MODE": "codex", "WORKER_API_KEY": "sk-test", "WORKER_MODEL": ""})
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertNotIn("--model", cmd)

    def test_codex_extra_flags_appended(self):
        agent = _make_agent({
            "WORKER_MODE": "codex",
            "WORKER_API_KEY": "sk-test",
            "CODEX_EXTRA_FLAGS": "--max-turns,50",
        })
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
            cmd = mock_popen.call_args[0][0]
            self.assertIn("--max-turns", cmd)
            self.assertIn("50", cmd)

    def test_codex_extra_flags_empty_by_default(self):
        agent = self._make_codex_agent()
        self.assertEqual(agent.codex_extra_flags, [])

    def test_codex_stores_thread_id_from_jsonl(self):
        agent = self._make_codex_agent()
        stdout = (
            "{\"type\":\"thread.started\",\"thread_id\":\"thread-123\"}\n"
            "{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"Done.\"}}\n"
        )
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text=stdout)):
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
        self.assertEqual(agent._worker_sessions["sess1"], "thread-123")

    def test_codex_returns_agent_message_from_jsonl(self):
        agent = self._make_codex_agent()
        stdout = (
            "{\"type\":\"thread.started\",\"thread_id\":\"thread-123\"}\n"
            "{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\",\"text\":\"Done.\"}}\n"
        )
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text=stdout)):
            result = agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
        self.assertEqual(result["output"], "Done.")

    def test_codex_resume_uses_stored_thread_id(self):
        agent = self._make_codex_agent()
        agent._worker_sessions["sess1"] = "thread-123"
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=True)
            cmd = mock_popen.call_args[0][0]
            self.assertEqual(cmd[:4], ["codex", "exec", "resume", "--json"])
            self.assertIn("thread-123", cmd)
            self.assertNotIn("--sandbox", cmd)
            self.assertNotIn("workspace-write", cmd)

    def test_codex_resume_without_session_starts_new_exec(self):
        agent = self._make_codex_agent()
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=True)
            cmd = mock_popen.call_args[0][0]
            self.assertEqual(cmd[:3], ["codex", "exec", "--json"])
            self.assertIn("--sandbox", cmd)
            self.assertEqual(cmd[cmd.index("--sandbox") + 1], "workspace-write")
            self.assertNotIn("resume", cmd)

    def test_codex_turn_count_incremented(self):
        agent = self._make_codex_agent()
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")):
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
        self.assertEqual(agent._worker_turn_count["sess1"], 2)
    def test_codex_cwd_set_correctly(self):
        agent = self._make_codex_agent()
        with patch("subprocess.Popen", return_value=_make_popen_mock(stdout_text="Done.\n")) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path="/tmp", resume=False)
            self.assertEqual(mock_popen.call_args.kwargs.get("cwd"), "/tmp")

    def test_codex_passes_agenthub_lineage_env(self):
        agent = self._make_codex_agent(api_key="dummy")
        with patch.dict(os.environ, {
            "BASE_LEAF_ID": "leaf_01HZX3BASE0123456789ABCDEFG",
            "BASE_HASH": "b" * 40,
            "AGENTHUB_ROOT_HASH": "f" * 40,
        }, clear=False), patch(
            "subprocess.Popen",
            return_value=_make_popen_mock(stdout_text="Done.\n"),
        ) as mock_popen:
            agent._call_codex_worker("prompt", "sess1", project_path=None, resume=False)
            call_env = mock_popen.call_args.kwargs.get("env") or mock_popen.call_args[1].get("env", {})
            self.assertEqual(call_env.get("BASE_LEAF_ID"), "leaf_01HZX3BASE0123456789ABCDEFG")
            self.assertEqual(call_env.get("BASE_HASH"), "b" * 40)
            self.assertEqual(call_env.get("AGENTHUB_ROOT_HASH"), "f" * 40)


if __name__ == "__main__":
    unittest.main()
