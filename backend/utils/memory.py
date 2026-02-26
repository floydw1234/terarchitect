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


def get_hipporag_kwargs() -> Dict[str, Any]:
    """Build HippoRAG constructor kwargs from app settings and environment. Memory defaults to Agent LLM URL and model when not set.
    Includes _memory_api_key (popped before passing to HippoRAG) for cache key and env when creating instance."""
    try:
        from utils.app_settings import get_setting_or_env
    except ImportError:
        get_setting_or_env = lambda k, d=None: os.environ.get(k, d)
    # Memory LLM: default to Agent's URL and model when not set. OpenAI client expects base_url to include /v1.
    llm_url = (get_setting_or_env("MEMORY_LLM_BASE_URL") or get_setting_or_env("VLLM_URL") or "").strip().rstrip("/")
    if llm_url and not llm_url.endswith("/v1"):
        llm_url = f"{llm_url}/v1"
    llm_model = get_setting_or_env("MEMORY_LLM_MODEL") or get_setting_or_env("AGENT_MODEL") or "gpt-4o-mini"
    emb_model = get_setting_or_env("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small") or "text-embedding-3-small"
    emb_url = (get_setting_or_env("MEMORY_EMBEDDING_BASE_URL") or get_setting_or_env("EMBEDDING_SERVICE_URL") or "").strip().rstrip("/")
    memory_api_key = (
        get_setting_or_env("MEMORY_LLM_API_KEY")
        or get_setting_or_env("AGENT_API_KEY")
        or get_setting_or_env("openai_api_key")
        or ""
    ).strip() or ""
    out = {
        "llm_model_name": llm_model,
        "embedding_model_name": emb_model,
        "_memory_api_key": memory_api_key,
    }
    if llm_url:
        out["llm_base_url"] = llm_url
    if emb_url:
        out["embedding_base_url"] = emb_url
    return out


def _get_instance(project_id: UUID, base_save_dir: str, **hipporag_kwargs) -> tuple:
    """Get or create (HippoRAG, Lock) for this project. Caller must hold lock when using."""
    memory_api_key = hipporag_kwargs.pop("_memory_api_key", "") or ""
    cache_key = (str(project_id), memory_api_key)
    with _cache_lock:
        if cache_key not in _cache:
            HippoRAG = _get_hipporag()
            save_dir = os.path.join(base_save_dir, str(project_id))
            os.makedirs(save_dir, exist_ok=True)
            old_key = os.environ.get("OPENAI_API_KEY")
            if memory_api_key:
                os.environ["OPENAI_API_KEY"] = memory_api_key
            else:
                os.environ.setdefault("OPENAI_API_KEY", "sk-")
            try:
                instance = HippoRAG(save_dir=save_dir, **hipporag_kwargs)
            finally:
                if memory_api_key and old_key is not None:
                    os.environ["OPENAI_API_KEY"] = old_key
                elif memory_api_key:
                    os.environ.pop("OPENAI_API_KEY", None)
                elif not old_key:
                    os.environ.pop("OPENAI_API_KEY", None)
            _cache[cache_key] = (instance, threading.Lock())
        return _cache[cache_key]


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
    pid = str(project_id)
    with _cache_lock:
        for cache_key in list(_cache.keys()):
            if cache_key[0] == pid:
                del _cache[cache_key]
    save_dir = os.path.join(base_save_dir, pid)
    if os.path.isdir(save_dir):
        shutil.rmtree(save_dir)
