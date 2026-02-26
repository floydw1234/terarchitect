"""
Integration test for HippoRAG-backed project memory.

Uses an OpenAI-compatible LLM for OpenIE and an OpenAI-compatible embedding service.
Both can be real OpenAI (set OPENAI_API_KEY) or any compatible local endpoint
(e.g. vLLM/Ollama — set EMBEDDING_SERVICE_URL and MEMORY_LLM_BASE_URL).

Prerequisites:
  - Embedding: set OPENAI_API_KEY (uses OpenAI directly) OR
                set EMBEDDING_SERVICE_URL to an OpenAI-compatible endpoint.
  - LLM (OpenIE): set OPENAI_API_KEY (uses OpenAI directly) OR
                   set MEMORY_LLM_BASE_URL + MEMORY_LLM_MODEL to a local vLLM endpoint.
  - Postgres (for project creation).

Run from backend/:
  OPENAI_API_KEY=sk-... MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s

Or with a local LLM + OpenAI embeddings:
  OPENAI_API_KEY=sk-... MEMORY_LLM_BASE_URL=http://localhost:8000/v1 MEMORY_LLM_MODEL=your-model \\
    MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s

Or fully local (local LLM + local embedding service):
  EMBEDDING_SERVICE_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=... \\
    MEMORY_EMBEDDING_MODEL=nomic-embed-text \\
    MEMORY_LLM_BASE_URL=http://localhost:8000/v1 MEMORY_LLM_MODEL=your-model \\
    MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s
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
os.environ.setdefault("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
os.environ.setdefault("MEMORY_LLM_BASE_URL", "http://localhost:8000/v1")
os.environ.setdefault("MEMORY_LLM_MODEL", "gpt-4o-mini")

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
    """Return True if embedding is configured (OpenAI key set) or a local service responds."""
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    embedding_key = (os.environ.get("EMBEDDING_API_KEY") or "").strip()
    embedding_url = (os.environ.get("EMBEDDING_SERVICE_URL") or "").strip()

    # Real OpenAI: just check the key is present
    if openai_key and openai_key != "sk-":
        return True
    if embedding_key:
        return True

    # Local service: try a /health or /v1/models endpoint
    if embedding_url:
        for path in ("/health", "/v1/models"):
            try:
                r = requests.get(embedding_url.rstrip("/") + path, timeout=2)
                if r.status_code == 200:
                    return True
            except Exception:
                pass

    return False


def _vllm_available() -> bool:
    """Return True if the LLM for OpenIE is reachable (or OpenAI key is set)."""
    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if openai_key and openai_key != "sk-":
        return True
    llm_url = (os.environ.get("MEMORY_LLM_BASE_URL", "http://localhost:8000/v1")).rstrip("/")
    base = llm_url.replace("/v1", "")
    for path in ("/health", "/v1/models"):
        try:
            r = requests.get(base + path, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
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

    @unittest.skipUnless(_embedding_available(), "No embedding configured (set OPENAI_API_KEY or EMBEDDING_SERVICE_URL)")
    def test_01_embedding_adapter(self):
        """OpenAI-compatible adapter returns embeddings."""
        model = os.environ.get("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
        base = f"http://127.0.0.1:{_TEST_PORT}/v1"
        r = requests.post(
            f"{base}/embeddings",
            json={"input": ["hello world"], "model": model},
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

    @unittest.skipUnless(_embedding_available(), "No embedding configured (set OPENAI_API_KEY or EMBEDDING_SERVICE_URL)")
    @unittest.skipUnless(_vllm_available(), "No LLM configured for OpenIE (set OPENAI_API_KEY or MEMORY_LLM_BASE_URL)")
    def test_02_memory_index_and_retrieve(self):
        """Create project, index docs, retrieve; HippoRAG uses configured LLM + embedding."""
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
            "The embedding model converts text into dense vector representations.",
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
            json={"queries": ["How are texts converted to vectors?"], "num_to_retrieve": 2},
            timeout=60,
        )
        self.assertEqual(r.status_code, 200, r.text)
        results = r.json().get("results", [])
        self.assertEqual(len(results), 1)
        self.assertIn("docs", results[0])
        self.assertIn("question", results[0])
        retrieved = results[0]["docs"]
        self.assertGreater(len(retrieved), 0, "Should retrieve at least one passage")
        combined = " ".join(retrieved).lower()
        self.assertTrue(
            "embedding" in combined or "vector" in combined or "text" in combined,
            f"Retrieved docs should be relevant to embeddings; got: {retrieved}",
        )


if __name__ == "__main__":
    unittest.main()
