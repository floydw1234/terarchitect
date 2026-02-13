from .base import EmbeddingConfig, BaseEmbeddingModel
from .OpenAI import OpenAIEmbeddingModel

from ..utils.logging_utils import get_logger

logger = get_logger(__name__)


def _get_embedding_model_class(embedding_model_name: str = "text-embedding-mpnet"):
    """Minimal: only OpenAI-compatible embedding (e.g. your embedding service via /v1/embeddings)."""
    if "text-embedding" in embedding_model_name:
        return OpenAIEmbeddingModel
    raise ValueError(
        f"Minimal HippoRAG only supports text-embedding-* (OpenAI-compatible). Got: {embedding_model_name}"
    )
