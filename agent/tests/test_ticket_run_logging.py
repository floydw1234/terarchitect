import json
import os
import sys
import unittest
import uuid
from unittest.mock import MagicMock, patch

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def _make_agent():
    from middle_agent.agent import MiddleAgent

    env = {
        "WORKER_MODE": "stub",
        "DIRECTOR_PROVIDER": "custom",
        "DIRECTOR_LLM_URL": "https://openrouter.ai/api",
        "DIRECTOR_MODEL": "google/gemini-2.5-flash-lite",
        "OPENROUTER_API_KEY": "or-key",
        "MIDDLE_AGENT_DEBUG": "0",
    }
    backend = MagicMock()
    backend.get_context.return_value = {
        "project_name": "test",
        "current_ticket": {"title": "Test ticket", "description": "desc"},
        "graph_relevant_to_current_ticket": {"nodes": [], "edges": []},
        "agent_settings": {},
        "project_path": "/tmp/fake",
    }
    backend.cancel_requested.return_value = False
    backend.retrieve_memory.return_value = []
    backend.log.return_value = None
    backend.complete.return_value = None
    with patch.dict(os.environ, env, clear=False):
        agent = MiddleAgent(backend=backend)
    return agent, backend


class TestTicketRunLogging(unittest.TestCase):
    def test_process_ticket_emits_structured_phase_event_logs(self):
        agent, backend = _make_agent()
        project_id = uuid.uuid4()
        ticket_id = uuid.uuid4()

        assess_calls = {"execution": 0}

        def fake_assess(*args, **kwargs):
            phase = kwargs.get("phase")
            if phase == "plan_review":
                return {"plan_approved": True, "approved_plan_text": "- step", "complete": False, "summary": ""}, []
            assess_calls["execution"] += 1
            if assess_calls["execution"] >= 2:
                return {"complete": True, "summary": "Implemented with pytest passing", "next_prompt": ""}, []
            return {"complete": False, "summary": "", "next_prompt": "Run pytest"}, []

        with patch.object(agent, "_send_to_worker", return_value={"output": "worker output", "error": "", "return_code": 0}), \
             patch.object(agent, "_agent_assess", side_effect=fake_assess), \
             patch.object(agent, "_retrieve_memory_passages", return_value=[]), \
             patch.object(agent, "_format_memories", return_value=""), \
             patch.object(agent, "_read_task_plan", return_value="- step"), \
             patch.object(agent, "_finalize"), \
             patch("middle_agent.agent.git_backend.prepare_work"), \
             patch("os.path.isdir", return_value=True):
            agent.process_ticket(ticket_id, project_path="/tmp/fakerepo", project_id=project_id)

        event_payloads = []
        for call in backend.log.call_args_list:
            raw_output = call.kwargs.get("raw_output")
            if not raw_output and len(call.args) >= 6:
                raw_output = call.args[5]
            if not raw_output or not str(raw_output).startswith("{"):
                continue
            parsed = json.loads(raw_output)
            if parsed.get("kind") == "ticket_run_event":
                event_payloads.append(parsed)

        phases = {(payload["phase"], payload["status"]) for payload in event_payloads}
        self.assertIn(("context", "completed"), phases)
        self.assertIn(("research", "completed"), phases)
        self.assertIn(("planning", "completed"), phases)
        self.assertIn(("plan_review", "completed"), phases)
        self.assertIn(("execution", "completed"), phases)

        sample = event_payloads[0]
        self.assertEqual(sample["project_id"], str(project_id))
        self.assertEqual(sample["ticket_id"], str(ticket_id))
        self.assertIn("timestamp", sample)
        self.assertIn("message", sample)

    def test_finalize_logs_success_receipt_with_attempt_and_next_actions(self):
        agent, backend = _make_agent()
        ticket = MagicMock()
        ticket.project_id = uuid.uuid4()
        ticket.id = uuid.uuid4()
        ticket.title = "Test ticket"

        with patch("middle_agent.agent.git_backend.swarm_publish", return_value="a" * 40), \
             patch("os.path.isdir", return_value=True), \
             patch.dict(os.environ, {"BASE_HASH": "b" * 40}, clear=False):
            agent._finalize(
                ticket,
                "sess-1",
                project_path="/tmp/fakerepo",
                completion_summary="pytest passed",
            )

        receipt_payloads = []
        for call in backend.log.call_args_list:
            raw_output = call.kwargs.get("raw_output")
            if not raw_output and len(call.args) >= 6:
                raw_output = call.args[5]
            if not raw_output or not str(raw_output).startswith("{"):
                continue
            parsed = json.loads(raw_output)
            if parsed.get("kind") == "ticket_run_receipt":
                receipt_payloads.append(parsed)

        self.assertEqual(len(receipt_payloads), 1)
        receipt = receipt_payloads[0]
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(receipt["agenthub_commit_hash"], "a" * 40)
        self.assertEqual(receipt["base_hash"], "b" * 40)
        self.assertEqual(receipt["runner_workdir"], "/tmp/fakerepo")
        self.assertEqual(receipt["evidence_summary"], "pytest passed")
        self.assertIn("ta ticket logs", " ".join(receipt["next_actions"]))
        self.assertIn("ta ticket attempts", " ".join(receipt["next_actions"]))
