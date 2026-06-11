import os
import sys
import types
import unittest
from unittest.mock import patch

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from agent_runner import __main__ as agent_runner_main


class TestAgentRunnerMaterialization(unittest.TestCase):
    def test_run_ticket_exits_without_explicit_base_or_workspace(self):
        with patch.dict(os.environ, {
            "TERARCHITECT_API_URL": "http://backend:5000",
            "TICKET_ID": "11111111-1111-1111-1111-111111111111",
            "PROJECT_ID": "22222222-2222-2222-2222-222222222222",
            "REPO_URL": "https://github.com/org/repo",
        }, clear=False):
            with self.assertRaises(SystemExit) as ctx:
                agent_runner_main.run_ticket()

        self.assertEqual(ctx.exception.code, 1)

    def test_run_ticket_exits_before_worker_when_base_leaf_materialization_fails(self):
        backend_instances = []
        agent_instances = []

        class FakeBackend:
            def __init__(self, *args, **kwargs):
                backend_instances.append(self)

            def log(self, *args, **kwargs):
                raise AssertionError("worker logging should not run before materialization succeeds")

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                agent_instances.append(self)

            def process_ticket(self, *args, **kwargs):
                raise AssertionError("worker should not start when base leaf materialization fails")

        fake_backend_mod = types.SimpleNamespace(HttpAgentBackend=FakeBackend)
        fake_agent_mod = types.SimpleNamespace(
            MiddleAgent=FakeAgent,
            WorkerUnavailableError=RuntimeError,
        )

        with patch.dict(os.environ, {
            "TERARCHITECT_API_URL": "http://backend:5000",
            "TICKET_ID": "11111111-1111-1111-1111-111111111111",
            "PROJECT_ID": "22222222-2222-2222-2222-222222222222",
            "REPO_URL": "https://github.com/org/repo",
            "BASE_LEAF_ID": "leaf_01HZX3BASE0123456789ABCDEFG",
        }, clear=False), patch.dict(sys.modules, {
            "middle_agent.backend": fake_backend_mod,
            "middle_agent.agent": fake_agent_mod,
        }, clear=False), patch(
            "agent_runner.__main__.materialize_workspace_from_agenthub",
            side_effect=agent_runner_main.AgentHubMaterializationError(
                "AgentHub fetch failed for leaf_01HZX3"
            )
        ):
            with self.assertRaises(SystemExit) as ctx:
                agent_runner_main.run_ticket()

        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(backend_instances, [])
        self.assertEqual(agent_instances, [])
