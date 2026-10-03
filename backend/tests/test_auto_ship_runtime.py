import os
from unittest.mock import patch

from utils.auto_ship_runtime import auto_ship_runtime_issues, format_auto_ship_runtime_error, is_compose_agenthub_url


def test_is_compose_agenthub_url():
    assert is_compose_agenthub_url("http://agenthub:8080")
    assert not is_compose_agenthub_url("http://127.0.0.1:8088")


def test_auto_ship_runtime_issues_when_misconfigured():
    with patch.dict(os.environ, {}, clear=True):
        issues = auto_ship_runtime_issues()
    assert any("AGENTHUB_URL" in item for item in issues)
    assert any("WORKER_API_KEY" in item or "TERARCHITECT_WORKER_API_KEY" in item for item in issues)


def test_auto_ship_runtime_ok_with_worker_api_key_fallback():
    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_API_KEY": "secret",
            "WORKER_API_KEY": "worker-from-env",
        },
        clear=True,
    ):
        assert auto_ship_runtime_issues() == []


def test_auto_ship_runtime_ok_in_compose():
    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_API_KEY": "secret",
            "TERARCHITECT_WORKER_API_KEY": "worker",
        },
        clear=True,
    ):
        assert auto_ship_runtime_issues() == []
        assert format_auto_ship_runtime_error() == ""
