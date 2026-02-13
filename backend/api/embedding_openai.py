"""
OpenAI-compatible embeddings endpoint so HippoRAG (and other clients) can use
the existing embedding service via base_url + /embeddings.
POST /v1/embeddings with {"input": ["text1", ...], "model": "mpnet-multilingual"}
-> calls embedding service, returns {"data": [{"embedding": [...]}, ...]}.
"""
from flask import Blueprint, request, jsonify

from utils.embedding_client import embed

embedding_bp = Blueprint("embedding_openai", __name__, url_prefix="/v1")


@embedding_bp.route("/embeddings", methods=["POST"])
def embeddings():
    """OpenAI-compatible: input (str or list[str]), model -> data[].embedding."""
    data = request.json or {}
    inp = data.get("input")
    model = data.get("model", "mpnet-multilingual")
    # HippoRAG uses OpenAI client with model name like "text-embedding-mpnet"; map to our service's model_id
    if model.startswith("text-embedding-"):
        model = "mpnet-multilingual"
    if inp is None:
        return jsonify({"error": "input is required"}), 400
    if isinstance(inp, str):
        inp = [inp]
    if not isinstance(inp, list) or not all(isinstance(t, str) for t in inp):
        return jsonify({"error": "input must be a string or list of strings"}), 400
    try:
        vectors = embed(texts=inp, model_id=model, normalize=True)
    except Exception as e:
        return jsonify({"error": "Embedding service error", "detail": str(e)}), 503
    return jsonify({
        "object": "list",
        "data": [{"object": "embedding", "embedding": vec, "index": i} for i, vec in enumerate(vectors)],
        "model": model,
    })
