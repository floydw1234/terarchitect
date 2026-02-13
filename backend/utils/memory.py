"""
Project memory via HippoRAG: one instance per project with a lock so concurrent
agents can read/write without corrupting the underlying files.
"""
import os
import threading
from typing import List, Any, Dict, Optional
from uuid import UUID

# Lazy import so backend can run without hipporag installed until memory is used
_hipporag = None

def _get_hipporag():
    global _hipporag
    if _hipporag is None:
        try:
            from hipporag_minimal import HippoRAG as _HippoRAG
            _hipporag = _HippoRAG
        except ImportError as e:
            raise RuntimeError(
                "Project memory requires hipporag_minimal (bundled). If you see this, the minimal HippoRAG package is missing or broken."
            ) from e
    return _hipporag

_cache: Dict[str, tuple] = {}  # project_id -> (HippoRAG instance, threading.Lock)
_cache_lock = threading.Lock()


def get_hipporag_kwargs() -> Dict[str, str]:
    """Build HippoRAG constructor kwargs from environment variables."""
    out = {
        "llm_model_name": os.environ.get("MEMORY_LLM_MODEL", "gpt-4o-mini"),
        "embedding_model_name": os.environ.get("MEMORY_EMBEDDING_MODEL", "text-embedding-mpnet"),
    }
    if os.environ.get("MEMORY_LLM_BASE_URL"):
        out["llm_base_url"] = os.environ.get("MEMORY_LLM_BASE_URL")
    if os.environ.get("MEMORY_EMBEDDING_BASE_URL"):
        out["embedding_base_url"] = os.environ.get("MEMORY_EMBEDDING_BASE_URL")
    return out


def _get_instance(project_id: UUID, base_save_dir: str, **hipporag_kwargs) -> tuple:
    """Get or create (HippoRAG, Lock) for this project. Caller must hold lock when using."""
    key = str(project_id)
    with _cache_lock:
        if key not in _cache:
            HippoRAG = _get_hipporag()
            save_dir = os.path.join(base_save_dir, key)
            os.makedirs(save_dir, exist_ok=True)
            instance = HippoRAG(save_dir=save_dir, **hipporag_kwargs)
            _cache[key] = (instance, threading.Lock())
        return _cache[key]


def index(project_id: UUID, docs: List[str], base_save_dir: str, **hipporag_kwargs) -> None:
    """Index documents into project memory. Serialized with other read/write for this project."""
    hipporag, lock = _get_instance(project_id, base_save_dir, **hipporag_kwargs)
    with lock:
        hipporag.index(docs=docs)


def retrieve(
    project_id: UUID,
    queries: List[str],
    base_save_dir: str,
    num_to_retrieve: Optional[int] = None,
    **hipporag_kwargs,
) -> List[Dict[str, Any]]:
    """Retrieve relevant passages for each query. Returns list of {question, docs, doc_scores}."""
    hipporag, lock = _get_instance(project_id, base_save_dir, **hipporag_kwargs)
    with lock:
        results = hipporag.retrieve(queries=queries, num_to_retrieve=num_to_retrieve)
    return [
        {
            "question": r.question,
            "docs": r.docs,
            "doc_scores": [float(s) for s in r.doc_scores],
        }
        for r in results
    ]


def delete(project_id: UUID, docs: List[str], base_save_dir: str, **hipporag_kwargs) -> None:
    """Remove documents from project memory."""
    hipporag, lock = _get_instance(project_id, base_save_dir, **hipporag_kwargs)
    with lock:
        hipporag.delete(docs_to_delete=docs)


def remove_project_memory(project_id: UUID, base_save_dir: str) -> None:
    """Remove all stored memory for a project (directory and cache). Call when deleting a project."""
    import shutil
    key = str(project_id)
    with _cache_lock:
        if key in _cache:
            del _cache[key]
    save_dir = os.path.join(base_save_dir, key)
    if os.path.isdir(save_dir):
        shutil.rmtree(save_dir)
