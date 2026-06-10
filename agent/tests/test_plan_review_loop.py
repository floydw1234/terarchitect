"""
Tests for the plan-review loop in MiddleAgent.process_ticket.

The loop should:
  - continue iterating (sending feedback to the worker) when plan_approved=False
  - break and advance to execution when plan_approved=True
"""
import os
import sys
import uuid
import unittest
from unittest.mock import MagicMock, patch, call

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def _make_agent():
    from middle_agent.agent import MiddleAgent

    env = {
        "WORKER_MODE": "codex",
        "DIRECTOR_LLM_URL": "http://localhost:8000",
        "DIRECTOR_MODEL": "gpt-4o",
        "DIRECTOR_API_KEY": "sk-test",
        "WORKER_API_KEY": "sk-openai-test",
        "WORKER_MODEL": "gpt-4o",
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


class TestPlanReviewLoop(unittest.TestCase):
    def _make_worker_response(self, text="ok"):
        return {"output": text, "error": "", "return_code": 0}

    def _make_agent_response_rejected(self, next_prompt="Fix this"):
        return {"plan_approved": False, "next_prompt": next_prompt, "complete": False, "summary": ""}

    def _make_agent_response_approved(self, approved_plan_text="- step 1\n- step 2"):
        return {"plan_approved": True, "approved_plan_text": approved_plan_text, "complete": False, "summary": ""}

    def _make_agent_response_complete(self):
        return {"plan_approved": False, "complete": True, "summary": "Done", "next_prompt": ""}

    def test_plan_review_breaks_on_approval(self):
        """When plan_approved=True on first review, the loop breaks immediately."""
        agent, backend = _make_agent()
        project_path = "/tmp/fakerepo"

        worker_calls = []

        def fake_send_to_worker(prompt, session_id, path, resume=False):
            worker_calls.append(prompt)
            return self._make_worker_response(f"worker response to: {prompt[:30]}")

        assess_calls = []

        def fake_agent_assess(*args, **kwargs):
            phase = kwargs.get("phase")
            assess_calls.append(phase)
            if phase == "plan_review":
                return self._make_agent_response_approved(), []
            # execution phase: complete on turn 2 (not turn 0)
            if len([c for c in assess_calls if c == "execution"]) >= 2:
                return self._make_agent_response_complete(), []
            return {"complete": False, "next_prompt": "implement", "summary": ""}, []

        with patch.object(agent, "_send_to_worker", side_effect=fake_send_to_worker), \
             patch.object(agent, "_agent_assess", side_effect=fake_agent_assess), \
             patch.object(agent, "_finalize"), \
             patch("os.path.isdir", return_value=True):
            agent.process_ticket(uuid.uuid4(), project_path=project_path, project_id=uuid.uuid4())

        plan_review_assess_count = assess_calls.count("plan_review")
        self.assertEqual(plan_review_assess_count, 1, "Should call agent_assess for plan_review exactly once when plan is approved immediately")
        self.assertIn("Phase 1 of 4: research.", worker_calls[0])
        self.assertIn("Phase 2 of 4: planning.", worker_calls[1])

    def test_plan_review_iterates_on_rejection(self):
        """When plan_approved=False, the loop sends feedback to the worker and tries again."""
        agent, backend = _make_agent()
        project_path = "/tmp/fakerepo"

        worker_calls = []

        def fake_send_to_worker(prompt, session_id, path, resume=False):
            worker_calls.append(prompt[:60])
            return self._make_worker_response(f"worker response")

        assess_calls = []
        plan_review_call_count = [0]

        def fake_agent_assess(*args, **kwargs):
            phase = kwargs.get("phase")
            assess_calls.append(phase)
            if phase == "plan_review":
                plan_review_call_count[0] += 1
                if plan_review_call_count[0] < 3:
                    # Reject first 2 reviews
                    return self._make_agent_response_rejected(next_prompt=f"Fix issue #{plan_review_call_count[0]}"), []
                # Approve on 3rd review
                return self._make_agent_response_approved(), []
            # execution phase
            if len([c for c in assess_calls if c == "execution"]) >= 2:
                return self._make_agent_response_complete(), []
            return {"complete": False, "next_prompt": "implement", "summary": ""}, []

        with patch.object(agent, "_send_to_worker", side_effect=fake_send_to_worker), \
             patch.object(agent, "_agent_assess", side_effect=fake_agent_assess), \
             patch.object(agent, "_finalize"), \
             patch("os.path.isdir", return_value=True):
            agent.process_ticket(uuid.uuid4(), project_path=project_path, project_id=uuid.uuid4())

        self.assertEqual(plan_review_call_count[0], 3, "Plan review loop should run until plan is approved (3 turns)")
        # Worker should have received feedback prompts for the 2 rejections
        feedback_prompts = [p for p in worker_calls if "Fix issue" in p]
        self.assertEqual(len(feedback_prompts), 2, "Worker should receive 2 rejection feedback prompts")
        self.assertIn("Phase 1 of 4: research.", worker_calls[0])
        self.assertIn("Phase 2 of 4: planning.", worker_calls[1])

    def test_execution_turn_zero_empty_director_json_uses_default_prompt(self):
        from middle_agent.agent import AgentAPIError

        agent, backend = _make_agent()
        ticket = MagicMock()
        ticket.id = uuid.uuid4()
        ticket.project_id = uuid.uuid4()
        session_id = "session-1"
        project_path = "/tmp/fakerepo"
        initial_director_messages = [{"role": "assistant", "content": "prior plan review"}]
        worker_prompts = []

        def fake_send_to_worker(prompt, sent_session_id, path, resume=False):
            worker_prompts.append(prompt)
            return self._make_worker_response("implemented")

        assess_calls = {"count": 0}

        def fake_agent_assess(*args, **kwargs):
            assess_calls["count"] += 1
            if assess_calls["count"] == 1:
                self.assertEqual(kwargs["director_messages"], initial_director_messages)
                raise AgentAPIError(
                    "Director API returned empty chat content three times while requesting JSON output"
                )
            self.assertEqual(kwargs["director_messages"], initial_director_messages)
            return self._make_agent_response_complete(), initial_director_messages

        with patch.object(agent, "_retrieve_memory_passages", return_value=[]), \
             patch.object(agent, "_format_memories", return_value=""), \
             patch.object(agent, "_agent_assess", side_effect=fake_agent_assess), \
             patch.object(agent, "_send_to_worker", side_effect=fake_send_to_worker), \
             patch.object(agent, "_cleanup_after_completion"), \
             patch.object(agent, "_index_completion_memory"), \
             patch.object(agent, "_trace_log") as mock_trace:
            completion = agent._run_execution_loop(
                ticket=ticket,
                session_id=session_id,
                context={"current_ticket": {"title": "Test ticket", "description": "desc"}},
                prompt_history=["Phase 2 planning prompt"],
                conversation_history=["Approved plan is ready."],
                director_messages=initial_director_messages,
                approved_plan_text="- step 1",
                start_memory_passages=[],
                base_save_dir=None,
                memory_kwargs={},
                project_path=project_path,
            )

        self.assertEqual(completion, "Done")
        self.assertEqual(assess_calls["count"], 2)
        self.assertEqual(len(worker_prompts), 1)
        self.assertIn("Implement the approved plan above. Start with the first step.", worker_prompts[0])
        self.assertTrue(
            any(
                "using the default execution prompt fallback" in call.args[1]
                for call in mock_trace.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
