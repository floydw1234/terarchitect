"""
Integration test for HippoRAG-backed project memory.

Uses your vLLM for the LLM (OpenIE) and the existing embedding service for
embeddings (via the backend's OpenAI-compatible /v1/embeddings adapter).

Prerequisites (must be running):
  - Embedding service at EMBEDDING_SERVICE_URL (default http://localhost:9009)
  - vLLM at MEMORY_LLM_BASE_URL (default http://localhost:8000/v1)
  - Postgres (for project creation)

Run from repo root:
  cd backend && MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s

Or with env for ports:
  EMBEDDING_SERVICE_URL=http://localhost:9009 MEMORY_LLM_BASE_URL=http://localhost:8000/v1 MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s
"""
import os
import sys
import tempfile
import threading
import time
import unittest

# Set env before app imports so create_app sees them
_TEST_PORT = 5011
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

os.environ.setdefault("MEMORY_SAVE_DIR", tempfile.mkdtemp(prefix="terarchitect_memory_test_"))
os.environ.setdefault("MEMORY_EMBEDDING_BASE_URL", f"http://127.0.0.1:{_TEST_PORT}/v1")
os.environ.setdefault("MEMORY_EMBEDDING_MODEL", "text-embedding-mpnet")
os.environ.setdefault("MEMORY_LLM_BASE_URL", "http://localhost:8000/v1")
os.environ.setdefault("MEMORY_LLM_MODEL", "meta-llama/Llama-3.1-8B-Instruct")  # adjust to your vLLM model

import requests


def _wait_for_url(url: str, timeout: float = 10.0, interval: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def _embedding_available() -> bool:
    url = os.environ.get("EMBEDDING_SERVICE_URL", "http://localhost:9009").rstrip("/") + "/health"
    try:
        return requests.get(url, timeout=2).status_code == 200
    except Exception:
        return False


def _vllm_available() -> bool:
    url = (os.environ.get("MEMORY_LLM_BASE_URL", "http://localhost:8000/v1")).rstrip("/").replace("/v1", "") + "/health"
    try:
        return requests.get(url, timeout=2).status_code == 200
    except Exception:
        return False


class TestMemoryHippoRAG(unittest.TestCase):
    """HippoRAG memory API integration test."""

    _app = None
    _thread = None
    _project_id = None

    @classmethod
    def setUpClass(cls):
        # Start Flask app in background so HippoRAG can call back to /v1/embeddings
        from main import create_app
        cls._app = create_app()
        def run():
            cls._app.run(host="127.0.0.1", port=_TEST_PORT, threaded=True, use_reloader=False)
        cls._thread = threading.Thread(target=run, daemon=True)
        cls._thread.start()
        if not _wait_for_url(f"http://127.0.0.1:{_TEST_PORT}/health", timeout=15):
            raise RuntimeError("Backend did not start in time")

    def test_01_embedding_adapter(self):
        """OpenAI-compatible adapter returns embeddings."""
        base = f"http://127.0.0.1:{_TEST_PORT}/v1"
        r = requests.post(
            f"{base}/embeddings",
            json={"input": ["hello world"], "model": "text-embedding-mpnet"},
            timeout=10,
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("data", data)
        self.assertEqual(len(data["data"]), 1)
        self.assertIn("embedding", data["data"][0])
        emb = data["data"][0]["embedding"]
        self.assertIsInstance(emb, list)
        self.assertGreater(len(emb), 0)

    @unittest.skipUnless(_embedding_available(), "Embedding service not running (e.g. http://localhost:9009/health)")
    @unittest.skipUnless(_vllm_available(), "vLLM not running (e.g. http://localhost:8000/health)")
    def test_02_memory_index_and_retrieve(self):
        """Create project, index docs, retrieve; HippoRAG uses vLLM + embedding service."""
        base = f"http://127.0.0.1:{_TEST_PORT}/api"
        # Create project
        r = requests.post(
            f"{base}/projects",
            json={"name": "HippoRAG test project", "description": "For memory test"},
            timeout=10,
        )
        self.assertEqual(r.status_code, 201, r.text)
        project_id = r.json()["id"]

        docs = [
            "The backend runs on Flask and uses PostgreSQL.",
            "The embedding service runs on port 9009 and returns 768-dimensional vectors.",
            "HippoRAG builds a knowledge graph and uses Personalized PageRank for retrieval.",
        ]
        # Index
        r = requests.post(
            f"{base}/projects/{project_id}/memory/index",
            json={"docs": docs},
            timeout=120,
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json().get("count"), 3)

        # Retrieve
        r = requests.post(
            f"{base}/projects/{project_id}/memory/retrieve",
            json={"queries": ["Where does the embedding service run?"], "num_to_retrieve": 2},
            timeout=60,
        )
        self.assertEqual(r.status_code, 200, r.text)
        results = r.json().get("results", [])
        self.assertEqual(len(results), 1)
        self.assertIn("docs", results[0])
        self.assertIn("question", results[0])
        retrieved = results[0]["docs"]
        self.assertGreater(len(retrieved), 0, "Should retrieve at least one passage")
        # Should surface the sentence about port 9009
        combined = " ".join(retrieved).lower()
        self.assertTrue(
            "9009" in combined or "embedding" in combined or "port" in combined,
            f"Retrieved docs should be relevant to embedding service; got: {retrieved}",
        )


if __name__ == "__main__":
    unittest.main()
