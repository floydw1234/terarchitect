"""
Focused unit tests for Director chat-completions payload construction.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)


def _make_agent(env_overrides: dict | None = None):
    from middle_agent.agent import MiddleAgent

    env = {
        "DIRECTOR_LLM_URL": "https://api.openai.com",
        "DIRECTOR_MODEL": "gpt-4o-mini",
        "WORKER_MODE": "stub",
        "MIDDLE_AGENT_DEBUG": "0",
    }
    if env_overrides:
        env.update(env_overrides)

    with patch.dict(os.environ, env, clear=False):
        return MiddleAgent(backend=MagicMock())


def _mock_chat_response(content: str = '{"complete":true,"summary":"ok","next_prompt":""}'):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }
    return response


class TestDirectorRequestPayload(unittest.TestCase):
    def test_chat_completions_json_mode_uses_strict_default_json_schema(self):
        agent = _make_agent()
        messages = [{"role": "user", "content": "Assess this."}]

        with patch("middle_agent.agent.requests.post", return_value=_mock_chat_response()) as mock_post:
            agent._director_request(messages, json_mode=True)

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")

        json_schema = payload["response_format"]["json_schema"]
        self.assertEqual(json_schema["name"], "director_response")
        self.assertIs(json_schema["strict"], True)

        schema = json_schema["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), {"complete", "summary", "next_prompt"})
        self.assertEqual(schema["properties"]["complete"]["type"], "boolean")
        self.assertEqual(schema["properties"]["summary"]["type"], "string")
        self.assertEqual(schema["properties"]["next_prompt"]["type"], "string")

    def test_chat_completions_plan_review_json_mode_uses_phase_schema(self):
        agent = _make_agent()
        messages = [{"role": "user", "content": "Review this plan."}]

        with patch("middle_agent.agent.requests.post", return_value=_mock_chat_response()) as mock_post:
            agent._director_request(messages, json_mode=True, phase="plan_review")

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["response_format"]["type"], "json_schema")

        json_schema = payload["response_format"]["json_schema"]
        self.assertEqual(json_schema["name"], "director_plan_review_response")
        self.assertIs(json_schema["strict"], True)

        schema = json_schema["schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(
            set(schema["required"]),
            {"plan_approved", "feedback", "next_prompt", "approved_plan_text"},
        )
        self.assertEqual(schema["properties"]["plan_approved"]["type"], "boolean")
        self.assertEqual(schema["properties"]["feedback"]["type"], "string")
        self.assertEqual(schema["properties"]["next_prompt"]["type"], "string")
        self.assertEqual(schema["properties"]["approved_plan_text"]["type"], "string")

    def test_openrouter_chat_payload_requires_parameter_support(self):
        agent = _make_agent(
            {
                "DIRECTOR_LLM_URL": "https://openrouter.ai/api",
                "DIRECTOR_MODEL": "openai/gpt-4o-mini",
            }
        )
        messages = [{"role": "user", "content": "Assess this."}]

        with patch("middle_agent.agent.requests.post", return_value=_mock_chat_response()) as mock_post:
            agent._director_request(messages, json_mode=True)

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["provider"], {"require_parameters": True})
        self.assertEqual(payload["response_format"]["type"], "json_schema")

    def test_execution_assessment_uses_compact_json_instructions_and_capped_max_tokens(self):
        agent = _make_agent(
            {
                "DIRECTOR_LLM_URL": "https://openrouter.ai/api",
                "DIRECTOR_MODEL": "openai/gpt-4o-mini",
            }
        )

        with patch("middle_agent.agent.requests.post", return_value=_mock_chat_response()) as mock_post:
            agent._agent_assess(
                context={"current_ticket": {"title": "Fix bug"}},
                prompt_history=["Run the next test."],
                conversation_history=["Implemented the change and ran pytest."],
                phase="execution",
            )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], 512)
        self.assertEqual(payload["provider"], {"require_parameters": True})

        messages = payload["messages"]
        self.assertIn("Return exactly one compact JSON object that matches the schema.", messages[0]["content"])
        self.assertIn("Do not pad with whitespace", messages[0]["content"])
        self.assertIn("No markdown, no prose outside the JSON, and no whitespace padding.", messages[-1]["content"])

    def test_plan_review_assessment_uses_phase_specific_token_cap(self):
        agent = _make_agent()

        with patch("middle_agent.agent.requests.post", return_value=_mock_chat_response('{"plan_approved":true,"feedback":"","next_prompt":"","approved_plan_text":"ship it"}')) as mock_post:
            agent._agent_assess(
                context={"current_ticket": {"title": "Review plan"}},
                prompt_history=["Drafted the plan."],
                conversation_history=["Here is the plan."],
                phase="plan_review",
            )

        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["max_tokens"], 768)
        self.assertIn("Keep all four keys present.", payload["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
