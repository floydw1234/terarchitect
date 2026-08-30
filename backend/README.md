# Terarchitect Backend

Flask API + DB for the Terarchitect visual SDLC orchestrator. **The app does not run the agent in-process.** It enqueues jobs to `agent_jobs`; a separate **coordinator** claims jobs and runs the **agent image** (Docker). See [docs/RUNBOOK.md](../docs/RUNBOOK.md).

## Setup

```bash
cd ..
./scripts/bootstrap-python-env.sh
```

This installs backend/agent/coordinator requirements into the repo-local `.venv`; do not use bare `pip install` from Hermes or another shared venv.

## Run

```bash
# Start postgres + frontend (Docker)
docker compose up -d

# Run backend on host
./backend/run.sh
```

## Environment Variables (.env in backend/)

The backend uses **only** these env vars. Director/Worker/OpenCode URLs and keys are **not** read by the backend; they belong in the coordinator (and agent) env. See `example.env` for who needs what.

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL (default: `postgresql://terarchitect:terarchitect@localhost:5433/terarchitect`, port 5433 to avoid conflict with other Postgres on 5432) |
| `github_agent_token` / `GITHUB_TOKEN` / `GH_TOKEN` | GitHub PAT for UI actions, cloning private repos, and Ship Room release/export PRs. At least one required for GitHub-backed execution readiness. |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | Git identity for agent commits (optional). |
| `TERARCHITECT_WORKER_API_KEY` | Optional. When set, worker API endpoints require Bearer token auth. |
| `MEMORY_SAVE_DIR` | Directory for HippoRAG project memory (default: `/tmp/terarchitect`). |
| `MEMORY_EMBEDDING_MODEL` | Embedding model name. **Optional** — when not set, memory is disabled and endpoints return empty results. |
| `MEMORY_LLM_MODEL` | LLM for HippoRAG OpenIE. **Optional** — required only when memory is enabled. |
| `MEMORY_LLM_BASE_URL`, `MEMORY_LLM_API_KEY` | LLM base URL and key for OpenIE. **Optional** — required only when memory is enabled. |
| `MEMORY_EMBEDDING_BASE_URL` | Optional embedding base URL (leave blank to use OpenAI or backend `/v1/embeddings`). |
| `EMBEDDING_PROVIDER` | `openai` (default) or `custom`. For `custom`, set `EMBEDDING_SERVICE_URL` and `EMBEDDING_API_KEY`. |
| `EMBEDDING_SERVICE_URL`, `EMBEDDING_API_KEY` | Used when `EMBEDDING_PROVIDER=custom` or by the `/v1/embeddings` route. |
| `openai_api_key` / `OPENAI_API_KEY` | Used for embeddings when provider is OpenAI, and for memory LLM when `MEMORY_LLM_BASE_URL` is unset. **Optional** — only required when memory is enabled. |

## Project memory (HippoRAG) — Optional

Project memory is **optional**. When memory configuration is not set (no `MEMORY_EMBEDDING_MODEL`, no embedding API key, or no `MEMORY_LLM_BASE_URL`), the system uses a no-op backend:
- Memory endpoints return success with `enabled: false` and empty results
- Execution readiness (`check_execution_readiness`) does not require embedding config
- Tickets can move to In Progress and run without memory configured

To enable HippoRAG memory, set all of: `MEMORY_EMBEDDING_MODEL`, `MEMORY_LLM_BASE_URL`, `MEMORY_LLM_MODEL`, and either `OPENAI_API_KEY` or (`EMBEDDING_SERVICE_URL` + `EMBEDDING_API_KEY`).

When `MEMORY_SAVE_DIR` is set and memory is enabled, the API exposes locked read/write memory per project:

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
   OPENAI_API_KEY=sk-... MEMORY_SAVE_DIR=/tmp/terarchitect_memory_test ../.venv/bin/python -m pytest tests/test_memory_hipporag.py -v -s
   ```

4. `test_01_embedding_adapter` verifies the `/v1/embeddings` adapter. `test_02_memory_index_and_retrieve` is skipped unless both embedding and LLM are reachable; it indexes docs and asserts retrieval relevance.

## Execution (coordinator + agent image)

When a ticket is moved to "In Progress", the app inserts a row into `agent_jobs`. In the normal GitHub-first flow, a **coordinator** claims jobs via `POST /api/worker/jobs/start` and runs the **agent image** (`terarchitect-agent`) for each job. The worker uses the queued `REPO_URL` plus explicit `BASE_LEAF_ID`/`BASE_HASH` to materialize the AgentHub DAG base in its workspace, runs the Director + worker, and publishes a child attempt without bind-mounting a host repo into the worker container. Local project paths remain legacy/debug only and are not the runtime source of truth for normal Docker execution. Run the coordinator in Compose with `docker compose build agent coordinator && docker compose up -d coordinator`, or from the **repo root** as a host process with `make setup-venv` then `TERARCHITECT_API_URL=... PROJECT_ID=... make python ARGS='-m coordinator'`. See [docs/RUNBOOK.md](../docs/RUNBOOK.md) and [docs/PHASE1_WORKER_API.md](../docs/PHASE1_WORKER_API.md).
