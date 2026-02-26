"""
OpenAI-compatible embeddings endpoint — forwards to the configured embedding service.
POST /v1/embeddings with {"input": ["text1", ...], "model": "text-embedding-3-small"}
-> calls the embedding client (which speaks OpenAI format), returns {"data": [{"embedding": [...]}, ...]}.

Configure via Settings UI or env:
  EMBEDDING_SERVICE_URL  — base URL (e.g. https://api.openai.com/v1 or a local vLLM endpoint)
  EMBEDDING_API_KEY      — Bearer token / API key
  MEMORY_EMBEDDING_MODEL — default model when none is specified in the request
"""
from flask import Blueprint, request, jsonify

from utils.embedding_client import embed, _default_model

embedding_bp = Blueprint("embedding_openai", __name__, url_prefix="/v1")


@embedding_bp.route("/embeddings", methods=["POST"])
def embeddings():
    """OpenAI-compatible: input (str or list[str]), model -> data[].embedding."""
    data = request.json or {}
    inp = data.get("input")
    model = (data.get("model") or "").strip() or _default_model()

    if inp is None:
        return jsonify({"error": "input is required"}), 400
    if isinstance(inp, str):
        inp = [inp]
    if not isinstance(inp, list) or not all(isinstance(t, str) for t in inp):
        return jsonify({"error": "input must be a string or list of strings"}), 400

    try:
        vectors = embed(texts=inp, model_id=model)
    except Exception as e:
        return jsonify({"error": "Embedding service error", "detail": str(e)}), 503

    return jsonify({
        "object": "list",
        "data": [{"object": "embedding", "embedding": vec, "index": i} for i, vec in enumerate(vectors)],
        "model": model,
    })
