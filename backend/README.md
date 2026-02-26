# Terarchitect Backend

Flask API + DB for the Terarchitect visual SDLC orchestrator. **The app does not run the agent in-process.** It enqueues jobs to `agent_jobs`; a separate **coordinator** claims jobs and runs the **agent image** (Docker). See [docs/RUNBOOK.md](../docs/RUNBOOK.md).

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
| `VLLM_URL` | vLLM server for Agent/Director (default: http://localhost:8000). Agent API is `{VLLM_URL}/v1/chat/completions`. |
| `AGENT_MODEL` | Model name for agent API |
| `AGENT_API_KEY` | API key (optional for vLLM) |
| `WORKER_LLM_URL` | OpenCode worker LLM API base URL (default: http://localhost:8080/v1) |
| `WORKER_MODEL` | Worker model string; leave unset to use Agent model |
| `WORKER_API_KEY` | API key for worker OpenAI-compatible provider (default: `dummy`) |
| `WORKER_TIMEOUT_SEC` | Worker run timeout in seconds (default: `3600`) |
| `MIDDLE_AGENT_DEBUG` | Set to `1` to log agent activity |
| `MEMORY_SAVE_DIR` | Directory for HippoRAG project memory (default: `/tmp/terarchitect`; not configurable via UI) |
| `MEMORY_LLM_MODEL` | LLM for HippoRAG OpenIE (default: `gpt-4o-mini`) |
| `MEMORY_EMBEDDING_MODEL` | Embedding model name (default: `text-embedding-3-small`). Any model supported by your endpoint. |
| `MEMORY_LLM_BASE_URL` | Optional LLM base URL for OpenIE (leave blank to use OpenAI directly via `OPENAI_API_KEY`). |
| `MEMORY_EMBEDDING_BASE_URL` | Optional embedding base URL (leave blank to use OpenAI directly, or set to any OpenAI-compatible endpoint). |

## Project memory (HippoRAG)

When `MEMORY_SAVE_DIR` is set, the API exposes locked read/write memory per project:

- **POST** `/api/projects/<project_id>/memory/index` — body: `{"docs": ["text1", "text2", ...]}`
- **POST** `/api/projects/<project_id>/memory/retrieve` — body: `{"queries": ["query1", ...], "num_to_retrieve": 5}`
- **POST** `/api/projects/<project_id>/memory/delete` — body: `{"docs": ["exact text to remove", ...]}`

Uses the bundled **hipporag_minimal** in `backend/hipporag_minimal` (no torch/vllm; uses your vLLM + embedding service via HTTP). Dependencies are in `requirements.txt`. One HippoRAG instance per project; a lock per project prevents concurrent writes from corrupting files.

The backend also exposes **OpenAI-compatible embeddings** at **POST /v1/embeddings**: send `{"input": ["text1", ...], "model": "text-embedding-3-small"}`; the route forwards to the configured embedding endpoint (real OpenAI or any OpenAI-compatible service) and returns `{"data": [{"embedding": [...]}, ...]}`. Set `MEMORY_EMBEDDING_BASE_URL=http://localhost:5010/v1` if you want HippoRAG to route through this backend adapter rather than calling the embedding service directly.

### Testing HippoRAG memory

Integration test uses an OpenAI-compatible LLM (for OpenIE) and an OpenAI-compatible embedding endpoint:

1. Set `OPENAI_API_KEY` (uses real OpenAI for both), or configure local endpoints (see test file docstring).
2. Start Postgres (via `docker compose up -d postgres`).
3. From `backend/` run:

   ```bash
   OPENAI_API_KEY=sk-... MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test python -m pytest tests/test_memory_hipporag.py -v -s
   ```

4. `test_01_embedding_adapter` verifies the `/v1/embeddings` adapter. `test_02_memory_index_and_retrieve` is skipped unless both embedding and LLM are reachable; it indexes docs and asserts retrieval relevance.

## Execution (coordinator + agent image)

When a ticket is moved to "In Progress" (or a PR review comment is created), the app inserts a row into `agent_jobs`. A **coordinator** process (run on the host, not in Docker) claims jobs via `POST /api/worker/jobs/start` and runs the **agent image** (`terarchitect-agent`) for each job. The container clones the **project** repo (the project’s GitHub URL), creates branch `ticket-{id}`, and runs the Director + OpenCode. Run the coordinator from the **repo root**: `PYTHONPATH=/path/to/terarchitect TERARCHITECT_API_URL=... PROJECT_ID=... python -m coordinator` (or install as a systemd service). See [docs/RUNBOOK.md](../docs/RUNBOOK.md) and [docs/PHASE1_WORKER_API.md](../docs/PHASE1_WORKER_API.md).
