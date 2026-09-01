"""
Tests for the pluggable memory backend interface.

These tests verify:
1. Execution readiness without embeddings configured
2. Disabled memory backend behavior
3. HippoRAG backend selection when configured
"""
import os
import sys
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)


class TestExecutionReadinessWithoutEmbeddings(unittest.TestCase):
    """Test that execution readiness does not require embedding config or GitHub token.
    
    Local/AgentHub/worktree modes should be ready without GitHub auth.
    GitHub is only required for GitHub-backed import/export paths.
    """

    def test_readiness_without_any_config(self):
        """check_execution_readiness should pass with no env vars set (local mode)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            from utils.app_settings import check_execution_readiness
            ready, missing = check_execution_readiness()
            self.assertTrue(ready, f"Should be ready without GitHub token for local mode. Missing: {missing}")
            self.assertEqual(len(missing), 0)

    def test_readiness_without_embedding_config(self):
        """check_execution_readiness should pass without embedding config."""
        with mock.patch.dict(os.environ, {
            "GITHUB_TOKEN": "ghp_testtoken",
        }, clear=True):
            from utils.app_settings import check_execution_readiness
            ready, missing = check_execution_readiness()
            self.assertTrue(ready, f"Should be ready. Missing: {missing}")
            self.assertEqual(len(missing), 0)

    def test_readiness_local_mode_no_github(self):
        """check_execution_readiness should pass for local/AgentHub mode without GitHub token."""
        with mock.patch.dict(os.environ, {
            "AGENTHUB_URL": "http://127.0.0.1:8088",
        }, clear=True):
            from utils.app_settings import check_execution_readiness
            ready, missing = check_execution_readiness()
            self.assertTrue(ready, "Local/AgentHub mode should be ready without GitHub token")


class TestNoOpMemoryBackend(unittest.TestCase):
    """Test the NoOpMemoryBackend behavior."""

    def test_noop_is_disabled(self):
        """NoOpMemoryBackend.is_enabled should return False."""
        from utils.memory_backend import NoOpMemoryBackend
        backend = NoOpMemoryBackend()
        self.assertFalse(backend.is_enabled)

    def test_noop_index_succeeds_silently(self):
        """NoOpMemoryBackend.index should succeed with no side effects."""
        from utils.memory_backend import NoOpMemoryBackend
        backend = NoOpMemoryBackend()
        project_id = uuid4()
        backend.index(project_id, ["doc1", "doc2"], "/tmp/test")

    def test_noop_retrieve_returns_empty_results(self):
        """NoOpMemoryBackend.retrieve should return empty docs for each query."""
        from utils.memory_backend import NoOpMemoryBackend
        backend = NoOpMemoryBackend()
        project_id = uuid4()
        queries = ["query1", "query2", "query3"]
        results = backend.retrieve(project_id, queries, "/tmp/test")

        self.assertEqual(len(results), 3)
        for i, result in enumerate(results):
            self.assertEqual(result["question"], queries[i])
            self.assertEqual(result["docs"], [])
            self.assertEqual(result["doc_scores"], [])

    def test_noop_delete_succeeds_silently(self):
        """NoOpMemoryBackend.delete should succeed with no side effects."""
        from utils.memory_backend import NoOpMemoryBackend
        backend = NoOpMemoryBackend()
        project_id = uuid4()
        backend.delete(project_id, ["doc1"], "/tmp/test")

    def test_noop_remove_project_memory_succeeds_silently(self):
        """NoOpMemoryBackend.remove_project_memory should succeed with no side effects."""
        from utils.memory_backend import NoOpMemoryBackend
        backend = NoOpMemoryBackend()
        project_id = uuid4()
        backend.remove_project_memory(project_id, "/tmp/test")


class TestMemoryBackendSelection(unittest.TestCase):
    """Test get_memory_backend() selects the correct backend based on env."""

    def setUp(self):
        from utils.memory_backend import clear_backend_cache
        clear_backend_cache()

    def tearDown(self):
        from utils.memory_backend import clear_backend_cache
        clear_backend_cache()

    def test_returns_noop_when_not_configured(self):
        """get_memory_backend returns NoOpMemoryBackend when memory is not configured."""
        with mock.patch.dict(os.environ, {}, clear=True):
            from utils.memory_backend import get_memory_backend, NoOpMemoryBackend, clear_backend_cache
            clear_backend_cache()
            backend = get_memory_backend()
            self.assertIsInstance(backend, NoOpMemoryBackend)
            self.assertFalse(backend.is_enabled)

    def test_returns_noop_when_embedding_model_missing(self):
        """get_memory_backend returns NoOpMemoryBackend when MEMORY_EMBEDDING_MODEL is missing."""
        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-test",
            "MEMORY_LLM_BASE_URL": "http://localhost:8000/v1",
            "MEMORY_LLM_MODEL": "gpt-4o-mini",
        }, clear=True):
            from utils.memory_backend import get_memory_backend, NoOpMemoryBackend, clear_backend_cache
            clear_backend_cache()
            backend = get_memory_backend()
            self.assertIsInstance(backend, NoOpMemoryBackend)

    def test_returns_noop_when_api_key_missing(self):
        """get_memory_backend returns NoOpMemoryBackend when embedding API key is missing."""
        with mock.patch.dict(os.environ, {
            "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
            "MEMORY_LLM_BASE_URL": "http://localhost:8000/v1",
            "MEMORY_LLM_MODEL": "gpt-4o-mini",
        }, clear=True):
            from utils.memory_backend import get_memory_backend, NoOpMemoryBackend, clear_backend_cache
            clear_backend_cache()
            backend = get_memory_backend()
            self.assertIsInstance(backend, NoOpMemoryBackend)

    def test_returns_noop_when_llm_url_missing(self):
        """get_memory_backend returns NoOpMemoryBackend when MEMORY_LLM_BASE_URL is missing."""
        with mock.patch.dict(os.environ, {
            "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
            "OPENAI_API_KEY": "sk-test",
            "MEMORY_LLM_MODEL": "gpt-4o-mini",
        }, clear=True):
            from utils.memory_backend import get_memory_backend, NoOpMemoryBackend, clear_backend_cache
            clear_backend_cache()
            backend = get_memory_backend()
            self.assertIsInstance(backend, NoOpMemoryBackend)

    def test_returns_hipporag_when_fully_configured_openai(self):
        """get_memory_backend returns HippoRAGMemoryBackend when fully configured with OpenAI."""
        with mock.patch.dict(os.environ, {
            "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
            "OPENAI_API_KEY": "sk-test",
            "MEMORY_LLM_BASE_URL": "http://localhost:8000/v1",
            "MEMORY_LLM_MODEL": "gpt-4o-mini",
        }, clear=True):
            from utils.memory_backend import get_memory_backend, HippoRAGMemoryBackend, clear_backend_cache
            clear_backend_cache()
            backend = get_memory_backend()
            self.assertIsInstance(backend, HippoRAGMemoryBackend)
            self.assertTrue(backend.is_enabled)

    def test_returns_hipporag_when_fully_configured_custom_provider(self):
        """get_memory_backend returns HippoRAGMemoryBackend when fully configured with custom provider."""
        with mock.patch.dict(os.environ, {
            "MEMORY_EMBEDDING_MODEL": "bge-large",
            "EMBEDDING_PROVIDER": "custom",
            "EMBEDDING_SERVICE_URL": "http://localhost:9009/v1",
            "EMBEDDING_API_KEY": "test-key",
            "MEMORY_LLM_BASE_URL": "http://localhost:8000/v1",
            "MEMORY_LLM_MODEL": "llama-3",
        }, clear=True):
            from utils.memory_backend import get_memory_backend, HippoRAGMemoryBackend, clear_backend_cache
            clear_backend_cache()
            backend = get_memory_backend()
            self.assertIsInstance(backend, HippoRAGMemoryBackend)
            self.assertTrue(backend.is_enabled)

    def test_caches_backend_instance(self):
        """get_memory_backend caches the backend instance."""
        with mock.patch.dict(os.environ, {}, clear=True):
            from utils.memory_backend import get_memory_backend, clear_backend_cache
            clear_backend_cache()
            backend1 = get_memory_backend()
            backend2 = get_memory_backend()
            self.assertIs(backend1, backend2)

    def test_force_refresh_rebuilds_backend(self):
        """get_memory_backend(force_refresh=True) rebuilds the backend."""
        with mock.patch.dict(os.environ, {}, clear=True):
            from utils.memory_backend import get_memory_backend, clear_backend_cache
            clear_backend_cache()
            backend1 = get_memory_backend()
            backend2 = get_memory_backend(force_refresh=True)
            self.assertIsNot(backend1, backend2)


class TestHippoRAGMemoryBackend(unittest.TestCase):
    """Test the HippoRAGMemoryBackend adapter."""

    def test_is_enabled_returns_true(self):
        """HippoRAGMemoryBackend.is_enabled should return True."""
        from utils.memory_backend import HippoRAGMemoryBackend
        backend = HippoRAGMemoryBackend({
            "llm_model_name": "test",
            "embedding_model_name": "test",
        })
        self.assertTrue(backend.is_enabled)

    def test_index_calls_memory_module(self):
        """HippoRAGMemoryBackend.index should call utils.memory.index."""
        from utils.memory_backend import HippoRAGMemoryBackend

        backend = HippoRAGMemoryBackend({
            "llm_model_name": "test",
            "embedding_model_name": "test",
        })

        project_id = uuid4()
        docs = ["doc1", "doc2"]
        base_save_dir = "/tmp/test"

        with mock.patch("utils.memory.index") as mock_index:
            backend.index(project_id, docs, base_save_dir)
            mock_index.assert_called_once_with(
                project_id, docs, base_save_dir,
                llm_model_name="test", embedding_model_name="test"
            )

    def test_retrieve_calls_memory_module(self):
        """HippoRAGMemoryBackend.retrieve should call utils.memory.retrieve."""
        from utils.memory_backend import HippoRAGMemoryBackend

        backend = HippoRAGMemoryBackend({
            "llm_model_name": "test",
            "embedding_model_name": "test",
        })

        project_id = uuid4()
        queries = ["q1", "q2"]
        base_save_dir = "/tmp/test"

        mock_results = [{"question": "q1", "docs": [], "doc_scores": []}]
        with mock.patch("utils.memory.retrieve", return_value=mock_results) as mock_retrieve:
            result = backend.retrieve(project_id, queries, base_save_dir, num_to_retrieve=5)
            mock_retrieve.assert_called_once_with(
                project_id, queries, base_save_dir,
                num_to_retrieve=5,
                llm_model_name="test", embedding_model_name="test"
            )
            self.assertEqual(result, mock_results)

    def test_delete_calls_memory_module(self):
        """HippoRAGMemoryBackend.delete should call utils.memory.delete."""
        from utils.memory_backend import HippoRAGMemoryBackend

        backend = HippoRAGMemoryBackend({
            "llm_model_name": "test",
            "embedding_model_name": "test",
        })

        project_id = uuid4()
        docs = ["doc1"]
        base_save_dir = "/tmp/test"

        with mock.patch("utils.memory.delete") as mock_delete:
            backend.delete(project_id, docs, base_save_dir)
            mock_delete.assert_called_once_with(
                project_id, docs, base_save_dir,
                llm_model_name="test", embedding_model_name="test"
            )


if __name__ == "__main__":
    unittest.main()
