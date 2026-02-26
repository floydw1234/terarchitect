"""
Unit tests for fetch_max_concurrent in coordinator/__main__.py.
Mocks requests.get — no running backend required.
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

_COORDINATOR_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _COORDINATOR_PARENT not in sys.path:
    sys.path.insert(0, _COORDINATOR_PARENT)

from coordinator.__main__ import fetch_max_concurrent


def _mock_settings_response(value):
    """Build a mock requests.Response returning {"MAX_CONCURRENT_AGENTS": value}."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"MAX_CONCURRENT_AGENTS": value}
    return r


class TestFetchMaxConcurrent(unittest.TestCase):
    def test_returns_backend_value_when_set(self):
        with patch("requests.get", return_value=_mock_settings_response("4")):
            result = fetch_max_concurrent("http://localhost:5010", fallback=1)
        self.assertEqual(result, 4)

    def test_returns_backend_int_value(self):
        with patch("requests.get", return_value=_mock_settings_response(3)):
            result = fetch_max_concurrent("http://localhost:5010", fallback=1)
        self.assertEqual(result, 3)

    def test_returns_fallback_when_key_absent(self):
        r = MagicMock()
        r.raise_for_status.return_value = None
        r.json.return_value = {}
        with patch("requests.get", return_value=r):
            result = fetch_max_concurrent("http://localhost:5010", fallback=2)
        self.assertEqual(result, 2)

    def test_returns_fallback_when_value_is_none(self):
        with patch("requests.get", return_value=_mock_settings_response(None)):
            result = fetch_max_concurrent("http://localhost:5010", fallback=2)
        self.assertEqual(result, 2)

    def test_returns_fallback_when_value_is_empty_string(self):
        with patch("requests.get", return_value=_mock_settings_response("")):
            result = fetch_max_concurrent("http://localhost:5010", fallback=2)
        self.assertEqual(result, 2)

    def test_returns_fallback_on_request_error(self):
        with patch("requests.get", side_effect=Exception("connection refused")):
            result = fetch_max_concurrent("http://localhost:5010", fallback=3)
        self.assertEqual(result, 3)

    def test_returns_fallback_on_http_error(self):
        r = MagicMock()
        r.raise_for_status.side_effect = Exception("503")
        with patch("requests.get", return_value=r):
            result = fetch_max_concurrent("http://localhost:5010", fallback=1)
        self.assertEqual(result, 1)

    def test_minimum_value_is_1(self):
        """Even if the backend sends 0 or negative, we clamp to at least 1."""
        with patch("requests.get", return_value=_mock_settings_response("0")):
            result = fetch_max_concurrent("http://localhost:5010", fallback=1)
        self.assertEqual(result, 1)

    def test_minimum_value_clamped_from_negative(self):
        with patch("requests.get", return_value=_mock_settings_response("-5")):
            result = fetch_max_concurrent("http://localhost:5010", fallback=1)
        self.assertEqual(result, 1)

    def test_returns_fallback_on_non_integer_value(self):
        with patch("requests.get", return_value=_mock_settings_response("not-a-number")):
            result = fetch_max_concurrent("http://localhost:5010", fallback=2)
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
