import os
from pathlib import Path
from unittest.mock import patch

from utils.worker_api_key import (
    configured_worker_api_token,
    is_worker_api_auth_enforced,
    resolve_terarchitect_worker_api_key,
    terarchitect_worker_request_headers,
)


def test_resolve_from_env():
    with patch.dict(os.environ, {"TERARCHITECT_WORKER_API_KEY": "abc"}, clear=True):
        assert resolve_terarchitect_worker_api_key() == "abc"


def test_resolve_from_path(tmp_path):
    key_file = tmp_path / "k"
    key_file.write_text("path-key\r\n", encoding="utf-8")
    with patch.dict(
        os.environ,
        {"TERARCHITECT_WORKER_API_KEY_PATH": str(key_file)},
        clear=True,
    ):
        assert resolve_terarchitect_worker_api_key() == "path-key"


def test_auth_not_enforced_without_config():
    with patch.dict(os.environ, {}, clear=True):
        assert not is_worker_api_auth_enforced()
        assert configured_worker_api_token() == ""


def test_request_headers_omit_bearer_when_auth_disabled():
    with patch.dict(os.environ, {}, clear=True):
        headers = terarchitect_worker_request_headers()
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


def test_request_headers_include_bearer_when_key_set():
    with patch.dict(os.environ, {"TERARCHITECT_WORKER_API_KEY": "tok"}, clear=True):
        headers = terarchitect_worker_request_headers()
    assert headers["Authorization"] == "Bearer tok"
