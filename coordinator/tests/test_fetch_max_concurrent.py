"""
Unit tests for _max_concurrent in coordinator/__main__.py.
Reads MAX_CONCURRENT_AGENTS from environment.
"""
import os
import sys
import unittest
from unittest.mock import patch

_COORDINATOR_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _COORDINATOR_PARENT not in sys.path:
    sys.path.insert(0, _COORDINATOR_PARENT)

from coordinator.__main__ import _max_concurrent


class TestMaxConcurrent(unittest.TestCase):
    def test_returns_env_value_when_set(self):
        with patch.dict(os.environ, {"MAX_CONCURRENT_AGENTS": "4"}, clear=False):
            self.assertEqual(_max_concurrent(1), 4)

    def test_returns_fallback_when_empty_string(self):
        with patch.dict(os.environ, {"MAX_CONCURRENT_AGENTS": ""}, clear=False):
            self.assertEqual(_max_concurrent(2), 2)

    def test_minimum_value_is_1(self):
        with patch.dict(os.environ, {"MAX_CONCURRENT_AGENTS": "0"}, clear=False):
            self.assertEqual(_max_concurrent(1), 1)
        with patch.dict(os.environ, {"MAX_CONCURRENT_AGENTS": "-5"}, clear=False):
            self.assertEqual(_max_concurrent(1), 1)

    def test_returns_fallback_on_non_integer(self):
        with patch.dict(os.environ, {"MAX_CONCURRENT_AGENTS": "not-a-number"}, clear=False):
            self.assertEqual(_max_concurrent(2), 2)


if __name__ == "__main__":
    unittest.main()
