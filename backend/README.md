# Terarchitect Backend

Flask backend for the Terarchitect visual SDLC orchestrator.

**Runs on host** (not Docker) for access to OpenCode CLI and local project paths.

## Setup

```bash
cd backend
pip install -r requirements.txt
```

## Run

```bash
# Start postgres + frontend (Docker)
docker compose up -d

# Run backend on host
cd backend
flask run --host=0.0.0.0 --port=5010
# Or: ./run.sh
```

## Environment Variables (.env in backend/)

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL (default: `postgresql://terarchitect:terarchitect@localhost:5433/terarchitect`, port 5433 to avoid conflict with other Postgres on 5432) |
| `VLLM_URL` | vLLM server (default: http://localhost:8000) |
| `VLLM_PROXY_URL` | Proxy URL used by worker/provider defaults (default: http://localhost:8080) |
| `AGENT_API_URL` | OpenAI-compatible API for agent (default: `{VLLM_URL}/v1/chat/completions`) |
| `AGENT_MODEL` | Model name for agent API |
| `AGENT_API_KEY` | API key (optional for vLLM) |
| `OPENCODE_CMD` | Worker CLI command (default: `opencode`) |
| `OPENCODE_PROVIDER_ID` | Provider id used in generated OpenCode config (default: `terarchitect-proxy`) |
| `OPENCODE_BASE_URL` | OpenCode provider base URL (default: `{VLLM_PROXY_URL}/v1`) |
| `OPENCODE_MODEL` | OpenCode model string; defaults to `{OPENCODE_PROVIDER_ID}/{AGENT_MODEL}` |
| `OPENCODE_API_KEY` | API key for OpenCode OpenAI-compatible provider (default: `dummy`) |
| `MIDDLE_AGENT_DEBUG` | Set to `1` to log agent activity |
| `MEMORY_SAVE_DIR` | Directory for HippoRAG project memory (optional; if unset, memory routes return 503) |
| `MEMORY_LLM_MODEL` | LLM for HippoRAG OpenIE (default: `gpt-4o-mini`) |
| `MEMORY_EMBEDDING_MODEL` | Embedding model (default: `text-embedding-mpnet` for minimal HippoRAG) |
| `MEMORY_LLM_BASE_URL` | Optional LLM base URL (e.g. vLLM) |
| `MEMORY_EMBEDDING_BASE_URL` | Optional embedding service URL |

## Project memory (HippoRAG)

When `MEMORY_SAVE_DIR` is set, the API exposes locked read/write memory per project:

- **POST** `/api/projects/<project_id>/memory/index` — body: `{"docs": ["text1", "text2", ...]}`
- **POST** `/api/projects/<project_id>/memory/retrieve` — body: `{"queries": ["query1", ...], "num_to_retrieve": 5}`
- **POST** `/api/projects/<project_id>/memory/delete` — body: `{"docs": ["exact text to remove", ...]}`

Uses the bundled **hipporag_minimal** in `backend/hipporag_minimal` (no torch/vllm; uses your vLLM + embedding service via HTTP). Dependencies are in `requirements.txt`. One HippoRAG instance per project; a lock per project prevents concurrent writes from corrupting files.

The backend also exposes **OpenAI-compatible embeddings** at **POST /v1/embeddings** so HippoRAG can use the existing embedding service: send `{"input": ["text1", ...], "model": "text-embedding-mpnet"}` (or `"model": "mpnet-multilingual"`); the route forwards to the embedding service and returns OpenAI-shaped `{"data": [{"embedding": [...]}, ...]}`. Set `MEMORY_EMBEDDING_BASE_URL=http://localhost:5010/v1` when running the backend so HippoRAG calls this adapter.

### Testing HippoRAG memory

Integration test uses your vLLM (for OpenIE) and the embedding service (via the /v1/embeddings adapter):

1. Start embedding service (e.g. port 9009), vLLM (e.g. port 8000), and Postgres.
2. From `backend/` run:

   ```bash
   MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s
   ```

   Override URLs if needed: `EMBEDDING_SERVICE_URL=http://localhost:9009` `MEMORY_LLM_BASE_URL=http://localhost:8000/v1` `MEMORY_LLM_MODEL=your/vllm-model`.

3. `test_01_embedding_adapter` checks the OpenAI-compatible adapter (no external services). `test_02_memory_index_and_retrieve` is skipped unless embedding service and vLLM are reachable; it creates a project, indexes docs, retrieves, and asserts relevance.

## Middle Agent

Runs when a ticket is moved to "In Progress". Requires:
- vLLM (or OpenAI-compatible API) for agent decisions
- OpenCode CLI on PATH
- Project path set to a local directory the backend can access
