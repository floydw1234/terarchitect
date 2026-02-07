"""
Simple client for the external embedding service (see project root embedding.md).
Uses EMBEDDING_SERVICE_URL (default http://localhost:9009) and optional EMBEDDING_API_KEY.
"""
import os
from typing import List

import requests


def _base_url() -> str:
    return os.environ.get("EMBEDDING_SERVICE_URL", "http://localhost:9009").rstrip("/")


def _headers() -> dict:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("EMBEDDING_API_KEY")
    if key:
        headers["X-API-Key"] = key
    return headers


def embed(
    texts: List[str],
    model_id: str = "mpnet-multilingual",
    normalize: bool = True,
) -> List[List[float]]:
    """
    Embed one or more texts. Returns a list of vectors (list of floats per text).
    Raises requests.HTTPError on HTTP or response errors.
    """
    if not texts:
        return []

    url = f"{_base_url()}/embed"
    payload = {"texts": texts, "model_id": model_id, "normalize": normalize}
    resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["embeddings"]


def embed_single(text: str, model_id: str = "mpnet-multilingual", normalize: bool = True) -> List[float]:
    """Convenience: embed a single string and return its vector."""
    vectors = embed([text], model_id=model_id, normalize=normalize)
    return vectors[0]
