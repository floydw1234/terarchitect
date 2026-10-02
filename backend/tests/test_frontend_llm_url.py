import pytest

from utils.frontend_llm_client import normalize_llm_base_url


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "https://api.openai.com/v1"),
        ("https://openrouter.ai/api", "https://openrouter.ai/api/v1"),
        ("https://openrouter.ai/api/", "https://openrouter.ai/api/v1"),
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1"),
        ("http://localhost:8000", "http://localhost:8000/v1"),
        ("http://x/v1/chat/completions", "http://x/v1"),
        (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ),
    ],
)
def test_normalize_llm_base_url(raw, expected):
    assert normalize_llm_base_url(raw) == expected
