from .base import EmbeddingConfig, BaseEmbeddingModel
from .OpenAI import OpenAIEmbeddingModel

from ..utils.logging_utils import get_logger

logger = get_logger(__name__)


def _get_embedding_model_class(embedding_model_name: str = "text-embedding-3-small"):
    """Minimal build uses a single OpenAI-compatible HTTP embedding adapter."""
    if isinstance(embedding_model_name, str) and embedding_model_name.strip():
        return OpenAIEmbeddingModel
    raise ValueError("Minimal HippoRAG requires a non-empty embedding model name.")
