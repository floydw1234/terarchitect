import io
from unittest.mock import patch

import pytest

from cli._api import API, APIError


def test_api_error_preserves_backend_error_envelope():
    api = API("http://example.test")
    response = io.BytesIO(
        b'{"error":"compose failed","detail":"Validation blockers remain.","hint":"Review candidate blockers.","request_id":"req-123","phase":"compose","next_commands":["ta ship candidates proj"]}'
    )

    http_error = __import__("urllib.error").error.HTTPError(
        "http://example.test/api/projects/proj/ship/candidates",
        502,
        "Bad Gateway",
        hdrs=None,
        fp=response,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(APIError) as exc:
            api.get("/api/projects/proj/ship/candidates")

    error = exc.value
    assert error.status == 502
    assert error.message == "compose failed"
    assert error.detail == "Validation blockers remain."
    assert error.hint == "Review candidate blockers."
    assert error.request_id == "req-123"
    assert error.phase == "compose"
    assert error.next_commands == ["ta ship candidates proj"]


def test_api_error_uses_message_field_when_error_field_missing():
    api = API("http://example.test")
    response = io.BytesIO(b'{"message":"backend timeout","request_id":"req-999"}')
    http_error = __import__("urllib.error").error.HTTPError(
        "http://example.test/api/projects/proj/ship/runs",
        504,
        "Gateway Timeout",
        hdrs=None,
        fp=response,
    )

    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(APIError) as exc:
            api.get("/api/projects/proj/ship/runs")

    error = exc.value
    assert error.status == 504
    assert error.message == "backend timeout"
    assert error.request_id == "req-999"
