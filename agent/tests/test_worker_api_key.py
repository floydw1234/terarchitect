import os
from unittest.mock import patch

from agent.utils.worker_api_key import (
    agenthub_request_headers,
    terarchitect_worker_request_headers,
)


def test_terarchitect_worker_request_headers_no_bearer_without_key():
    with patch.dict(os.environ, {}, clear=True):
        headers = terarchitect_worker_request_headers()
    assert "Authorization" not in headers


def test_terarchitect_worker_request_headers_bearer_when_key_set():
    with patch.dict(os.environ, {"TERARCHITECT_WORKER_API_KEY": "worker-secret"}, clear=True):
        headers = terarchitect_worker_request_headers()
    assert headers["Authorization"] == "Bearer worker-secret"


def test_agenthub_request_headers_skip_when_auth_disabled():
    with patch.dict(os.environ, {"AGENTHUB_AUTH_DISABLED": "1"}, clear=True):
        assert agenthub_request_headers() == {}


def test_agenthub_request_headers_bearer_when_key_set():
    with patch.dict(os.environ, {"AGENTHUB_API_KEY": "ah-key"}, clear=True):
        headers = agenthub_request_headers()
    assert headers == {"Authorization": "Bearer ah-key"}
