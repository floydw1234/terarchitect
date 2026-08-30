"""
Pluggable memory backend interface for project memory.

Memory is optional in Terarchitect. When no memory backend is configured (no embedding
model, no embedding API key), the system uses a no-op backend that returns empty results.
When HippoRAG is configured, it becomes the active backend.

Call sites should use get_memory_backend() to obtain the configured backend, then call
index/retrieve/delete on it. This decouples execution readiness from memory availability.
"""
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from uuid import UUID


class MemoryBackend(ABC):
    """Abstract interface for project memory backends."""

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Return True if this backend is configured and operational."""
        ...

    @abstractmethod
    def index(self, project_id: UUID, docs: List[str], base_save_dir: str) -> None:
        """Index documents into project memory."""
        ...

    @abstractmethod
    def retrieve(
        self,
        project_id: UUID,
        queries: List[str],
        base_save_dir: str,
        num_to_retrieve: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant passages for each query.

        Returns list of {question, docs, doc_scores} for each query.
        """
        ...

    @abstractmethod
    def delete(self, project_id: UUID, docs: List[str], base_save_dir: str) -> None:
        """Remove documents from project memory."""
        ...

    @abstractmethod
    def remove_project_memory(self, project_id: UUID, base_save_dir: str) -> None:
        """Remove all stored memory for a project (directory and cache)."""
        ...


class NoOpMemoryBackend(MemoryBackend):
    """Disabled memory backend that returns empty results.

    Used when memory/embedding is not configured. All operations succeed
    silently with no side effects.
    """

    @property
    def is_enabled(self) -> bool:
        return False

    def index(self, project_id: UUID, docs: List[str], base_save_dir: str) -> None:
        pass

    def retrieve(
        self,
        project_id: UUID,
        queries: List[str],
        base_save_dir: str,
        num_to_retrieve: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        return [
            {"question": q, "docs": [], "doc_scores": []}
            for q in queries
        ]

    def delete(self, project_id: UUID, docs: List[str], base_save_dir: str) -> None:
        pass

    def remove_project_memory(self, project_id: UUID, base_save_dir: str) -> None:
        pass


class HippoRAGMemoryBackend(MemoryBackend):
    """HippoRAG-backed memory backend.

    Wraps the existing utils.memory module which uses HippoRAG for
    knowledge graph-based retrieval.
    """

    def __init__(self, hipporag_kwargs: Dict[str, Any]):
        """Initialize with HippoRAG constructor kwargs.

        Args:
            hipporag_kwargs: Dict from get_hipporag_kwargs() containing
                llm_model_name, embedding_model_name, etc.
        """
        self._kwargs = hipporag_kwargs

    @property
    def is_enabled(self) -> bool:
        return True

    def index(self, project_id: UUID, docs: List[str], base_save_dir: str) -> None:
        from utils.memory import index as memory_index_fn
        memory_index_fn(project_id, docs, base_save_dir, **self._kwargs)

    def retrieve(
        self,
        project_id: UUID,
        queries: List[str],
        base_save_dir: str,
        num_to_retrieve: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        from utils.memory import retrieve as memory_retrieve_fn
        return memory_retrieve_fn(
            project_id, queries, base_save_dir,
            num_to_retrieve=num_to_retrieve,
            **self._kwargs,
        )

    def delete(self, project_id: UUID, docs: List[str], base_save_dir: str) -> None:
        from utils.memory import delete as memory_delete_fn
        memory_delete_fn(project_id, docs, base_save_dir, **self._kwargs)

    def remove_project_memory(self, project_id: UUID, base_save_dir: str) -> None:
        from utils.memory import remove_project_memory as memory_remove_fn
        memory_remove_fn(project_id, base_save_dir)


_cached_backend: Optional[MemoryBackend] = None


def _is_memory_configured() -> bool:
    """Check if memory/embedding env is configured.

    Memory requires:
    - MEMORY_EMBEDDING_MODEL (embedding model name)
    - Either OPENAI_API_KEY or (EMBEDDING_SERVICE_URL + EMBEDDING_API_KEY)
    - MEMORY_LLM_BASE_URL and MEMORY_LLM_MODEL for OpenIE
    """
    from utils.app_settings import get_setting_or_env

    emb_model = (get_setting_or_env("MEMORY_EMBEDDING_MODEL") or "").strip()
    if not emb_model:
        return False

    emb_provider = (get_setting_or_env("EMBEDDING_PROVIDER") or "openai").strip().lower()
    if emb_provider == "openai":
        openai_key = (
            get_setting_or_env("openai_api_key")
            or get_setting_or_env("OPENAI_API_KEY")
            or ""
        ).strip()
        if not openai_key:
            return False
    else:
        emb_url = (get_setting_or_env("EMBEDDING_SERVICE_URL") or "").strip()
        emb_key = (
            get_setting_or_env("EMBEDDING_API_KEY")
            or get_setting_or_env("openai_api_key")
            or get_setting_or_env("OPENAI_API_KEY")
            or ""
        ).strip()
        if not emb_url or not emb_key:
            return False

    llm_url = (get_setting_or_env("MEMORY_LLM_BASE_URL") or "").strip()
    llm_model = (get_setting_or_env("MEMORY_LLM_MODEL") or "").strip()
    if not llm_url or not llm_model:
        return False

    return True


def get_memory_backend(force_refresh: bool = False) -> MemoryBackend:
    """Get the configured memory backend.

    Returns HippoRAGMemoryBackend when memory is fully configured,
    NoOpMemoryBackend otherwise. The backend is cached for reuse.

    Args:
        force_refresh: If True, re-check config and rebuild backend.
    """
    global _cached_backend

    if _cached_backend is not None and not force_refresh:
        return _cached_backend

    if _is_memory_configured():
        from utils.memory import get_hipporag_kwargs
        try:
            kwargs = get_hipporag_kwargs()
            _cached_backend = HippoRAGMemoryBackend(kwargs)
        except RuntimeError:
            _cached_backend = NoOpMemoryBackend()
    else:
        _cached_backend = NoOpMemoryBackend()

    return _cached_backend


def clear_backend_cache() -> None:
    """Clear the cached backend (useful for testing)."""
    global _cached_backend
    _cached_backend = None
