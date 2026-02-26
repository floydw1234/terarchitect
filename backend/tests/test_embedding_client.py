"""
Unit tests for the OpenAI-compatible embedding_client.
Mocks the OpenAI client — no network required.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _mock_openai_response(vectors):
    """Build a mock openai.embeddings.create response."""
    response = MagicMock()
    response.data = [MagicMock(embedding=v) for v in vectors]
    return response


class TestEmbedClient(unittest.TestCase):
    def _patch_client(self, vectors):
        """Context manager: patch _get_client() so .embeddings.create returns vectors."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _mock_openai_response(vectors)
        return patch("utils.embedding_client._get_client", return_value=mock_client)

    def test_embed_returns_vectors(self):
        vecs = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        with self._patch_client(vecs):
            from utils.embedding_client import embed
            result = embed(["hello", "world"], model_id="text-embedding-3-small")
        self.assertEqual(result, vecs)

    def test_embed_empty_returns_empty(self):
        with self._patch_client([]):
            from utils.embedding_client import embed
            result = embed([])
        self.assertEqual(result, [])

    def test_embed_single(self):
        vec = [0.1, 0.2]
        with self._patch_client([vec]):
            from utils.embedding_client import embed_single
            result = embed_single("hello", model_id="text-embedding-3-small")
        self.assertEqual(result, vec)

    def test_get_client_uses_embedding_service_url(self):
        """When EMBEDDING_SERVICE_URL is set, the OpenAI client is built with that base_url."""
        import utils.embedding_client as ec
        with patch.dict(os.environ, {"EMBEDDING_SERVICE_URL": "http://localhost:11434/v1", "EMBEDDING_API_KEY": "test-key"}, clear=False):
            with patch.object(ec, "OpenAI") as mock_openai_cls:
                mock_openai_cls.return_value = MagicMock()
                ec._get_client()
                call_kwargs = mock_openai_cls.call_args.kwargs
                self.assertEqual(call_kwargs["base_url"], "http://localhost:11434/v1")
                self.assertEqual(call_kwargs["api_key"], "test-key")

    def test_get_client_no_url_uses_none(self):
        """When EMBEDDING_SERVICE_URL is unset, base_url=None so OpenAI SDK uses its default."""
        import utils.embedding_client as ec
        env_patch = {k: v for k, v in os.environ.items()}
        env_patch.pop("EMBEDDING_SERVICE_URL", None)
        env_patch.pop("EMBEDDING_API_KEY", None)
        with patch.dict(os.environ, env_patch, clear=True):
            with patch.object(ec, "OpenAI") as mock_openai_cls:
                mock_openai_cls.return_value = MagicMock()
                ec._get_client()
                call_kwargs = mock_openai_cls.call_args.kwargs
                self.assertIsNone(call_kwargs["base_url"])

    def test_model_passed_to_create(self):
        """The model name is forwarded to client.embeddings.create unchanged."""
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _mock_openai_response([[0.1]])
        with patch("utils.embedding_client._get_client", return_value=mock_client):
            from utils.embedding_client import embed
            embed(["test"], model_id="text-embedding-3-small")
            mock_client.embeddings.create.assert_called_once_with(
                input=["test"], model="text-embedding-3-small"
            )

    def test_default_model_falls_back_to_text_embedding_3_small(self):
        """When no model_id is given and MEMORY_EMBEDDING_MODEL is unset, use text-embedding-3-small."""
        import utils.embedding_client as ec
        env_patch = {k: v for k, v in os.environ.items()}
        env_patch.pop("MEMORY_EMBEDDING_MODEL", None)
        mock_client = MagicMock()
        mock_client.embeddings.create.return_value = _mock_openai_response([[0.1]])
        with patch.dict(os.environ, env_patch, clear=True):
            with patch.object(ec, "_get_client", return_value=mock_client):
                with patch.object(ec, "_default_model", return_value="text-embedding-3-small"):
                    ec.embed(["test"])
                    call_kwargs = mock_client.embeddings.create.call_args.kwargs
                    self.assertEqual(call_kwargs["model"], "text-embedding-3-small")


if __name__ == "__main__":
    unittest.main()
