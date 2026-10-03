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


def test_auto_ship_runtime_ok_without_worker_key_when_auth_disabled():
    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_API_KEY": "secret",
            "WORKER_API_KEY": "llm-provider-key-not-worker-auth",
        },
        clear=True,
    ):
        assert auto_ship_runtime_issues() == []


def test_auto_ship_runtime_ok_with_agenthub_auth_disabled():
    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_AUTH_DISABLED": "1",
        },
        clear=True,
    ):
        assert auto_ship_runtime_issues() == []


def test_auto_ship_runtime_ok_in_compose_with_worker_key():
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


def test_auto_ship_runtime_requires_worker_key_when_auth_enabled(tmp_path):
    key_file = tmp_path / "worker.key"
    key_file.write_text("from-file\n", encoding="utf-8")
    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_API_KEY": "secret",
            "TERARCHITECT_WORKER_API_KEY_PATH": str(key_file),
        },
        clear=True,
    ):
        assert auto_ship_runtime_issues() == []


def test_auto_ship_runtime_fails_when_worker_auth_configured_but_unreadable(tmp_path):
    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "http://agenthub:8080",
            "AGENTHUB_API_KEY": "secret",
            "TERARCHITECT_WORKER_API_KEY_PATH": str(tmp_path / "missing"),
        },
        clear=True,
    ):
        issues = auto_ship_runtime_issues()
    assert any("could not be resolved" in item for item in issues)
