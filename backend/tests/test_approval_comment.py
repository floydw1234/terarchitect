"""
Unit tests for classify_comment_is_approval — the LLM-based filter that prevents
the review agent from firing on pure approval comments.
No network or DB required; mocks requests.post.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from utils.pr_comment_classifier import classify_comment_is_approval  # noqa: E402


def _chat_response(text: str):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"choices": [{"message": {"content": text}}]}
    return mock


def _responses_api_response(text: str):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}]
    }
    return mock


LLM = {"model": "gpt-4o-mini", "url": "https://api.openai.com/v1", "api_key": "sk-test"}
GPT5 = {"model": "gpt-5", "url": "https://api.openai.com/v1", "api_key": "sk-test"}


class TestClassifyCommentIsApproval(unittest.TestCase):

    def test_yes_returns_true(self):
        with patch("requests.post", return_value=_chat_response("YES")):
            self.assertTrue(classify_comment_is_approval("LGTM, great work!", LLM))

    def test_no_returns_false(self):
        with patch("requests.post", return_value=_chat_response("NO")):
            self.assertFalse(classify_comment_is_approval("Please fix the null check on line 42.", LLM))

    def test_yes_with_trailing_whitespace(self):
        with patch("requests.post", return_value=_chat_response("YES ")):
            self.assertTrue(classify_comment_is_approval("Looks good", LLM))

    def test_no_with_punctuation(self):
        with patch("requests.post", return_value=_chat_response("NO.")):
            self.assertFalse(classify_comment_is_approval("Minor nit on imports", LLM))

    def test_empty_body_returns_false_without_llm_call(self):
        with patch("requests.post") as mock_post:
            result = classify_comment_is_approval("", LLM)
        mock_post.assert_not_called()
        self.assertFalse(result)

    def test_whitespace_only_body_returns_false_without_llm_call(self):
        with patch("requests.post") as mock_post:
            result = classify_comment_is_approval("   ", LLM)
        mock_post.assert_not_called()
        self.assertFalse(result)

    def test_unconfigured_model_returns_false(self):
        with patch("requests.post") as mock_post:
            result = classify_comment_is_approval("LGTM", {"model": "", "url": "", "api_key": ""})
        mock_post.assert_not_called()
        self.assertFalse(result)

    def test_llm_request_exception_propagates(self):
        with patch("requests.post", side_effect=Exception("timeout")):
            with self.assertRaises(Exception):
                classify_comment_is_approval("looks good", LLM)

    def test_gpt5_uses_responses_endpoint(self):
        with patch("requests.post", return_value=_responses_api_response("YES")) as mock_post:
            result = classify_comment_is_approval("Ship it!", GPT5)
        url_called = mock_post.call_args[0][0]
        self.assertIn("/responses", url_called)
        self.assertTrue(result)

    def test_gpt4o_uses_chat_completions_endpoint(self):
        with patch("requests.post", return_value=_chat_response("NO")) as mock_post:
            result = classify_comment_is_approval("Please fix the null check.", LLM)
        url_called = mock_post.call_args[0][0]
        self.assertIn("/chat/completions", url_called)
        self.assertFalse(result)

    def test_max_tokens_is_small(self):
        with patch("requests.post", return_value=_chat_response("YES")) as mock_post:
            classify_comment_is_approval("LGTM", LLM)
        payload = mock_post.call_args[1]["json"]
        self.assertLessEqual(
            payload.get("max_tokens", payload.get("max_output_tokens", 999)), 10
        )

    def test_o3_model_uses_responses_endpoint(self):
        settings = {**LLM, "model": "o3-mini"}
        with patch("requests.post", return_value=_responses_api_response("YES")) as mock_post:
            result = classify_comment_is_approval("Approved!", settings)
        url_called = mock_post.call_args[0][0]
        self.assertIn("/responses", url_called)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
