# Phase 1: Worker-facing API contract

Used by the standalone runner (Phase 2) and coordinator (Phase 4). All endpoints below accept **Bearer token** auth when `TERARCHITECT_WORKER_API_KEY` is set in the **backend** environment. If unset, no auth is required (dev).

**Header:** `Authorization: Bearer <TERARCHITECT_WORKER_API_KEY>`

Set `TERARCHITECT_WORKER_API_KEY` in the backend’s `.env` (or process env) when you want to protect worker API endpoints.

---

## Agent endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects/<project_id>/tickets/<ticket_id>/worker-context` | Full context (project, graph, ticket, notes, backlog/in_progress/done) + `repo_url`. No `project_path`. |
| POST | `/api/projects/<project_id>/tickets/<ticket_id>/logs` | Append log. Body: `session_id`, `step`, `summary`, `raw_output` (optional). |
| POST | `/api/projects/<project_id>/tickets/<ticket_id>/complete` | Mark ticket execution complete and create a `TicketAttempt`. Body: `summary`, `agenthub_commit_hash`, optional `base_hash`. |
| GET | `/api/projects/<project_id>/tickets/<ticket_id>/cancel-requested` | Poll: `{"cancel_requested": true\|false}`. |

**Memory** (same as app): `POST /api/projects/<project_id>/memory/retrieve` (body: `queries`, optional `num_to_retrieve`), `POST /api/projects/<project_id>/memory/index` (body: `docs`). Use same Bearer token when `TERARCHITECT_WORKER_API_KEY` is set.

---

## Queue endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/worker/projects` | List all projects (id, name) for coordinator discovery. Use when PROJECT_ID/PROJECT_IDS is unset so the coordinator can fetch IDs at startup. |
| POST | `/api/worker/jobs/start` | Claim one pending job. Body: optional `{"project_id": "<uuid>"}`. If omitted, claim next job from any project. Returns 200 + job JSON or 204 No Content. |
| POST | `/api/worker/jobs/<job_id>/complete` | Mark job completed (container exited successfully). |
| POST | `/api/worker/jobs/<job_id>/fail` | Mark job failed (container exited with failure). |

**Job response (200):** `job_id`, `ticket_id`, `project_id`, `kind` (`ticket`), `repo_url`, `git_mode`, `base_hash`, and `shipped_frontier`. `base_hash` is the AgentHub commit the worker should build on when present.

---

## Testing Phase 1

1. Start the app; `agent_jobs` table is created on startup.
2. Insert a job by hand: `INSERT INTO agent_jobs (id, ticket_id, project_id, kind, status) VALUES (gen_random_uuid(), '<ticket_uuid>', '<project_uuid>', 'ticket', 'pending');`
3. Call `POST /api/worker/jobs/start` with body `{"project_id": "<project_uuid>"}` and (if set) `Authorization: Bearer <token>`. Expect 200 + job payload.
4. Call `GET /api/projects/<id>/tickets/<id>/worker-context` to verify context payload.
5. Call `POST .../complete` and `POST /api/worker/jobs/<job_id>/complete` to close the job.

The app **enqueues** to `agent_jobs` when a ticket moves to In Progress (Phase 4 app side).

---

## Phase 2: Standalone runner

Run one ticket from the CLI using the Phase 1 API only (no Flask/DB in the runner):

```bash
cd backend
TICKET_ID=<uuid> PROJECT_ID=<uuid> TERARCHITECT_API_URL=http://localhost:5000 REPO_URL=https://github.com/owner/repo [GITHUB_TOKEN=...] [TERARCHITECT_WORKER_API_KEY=...] python -m agent_runner ticket
```

The runner clones or opens the repo, checks out the provided AgentHub `BASE_HASH` when present, and runs the Director + worker via `MiddleAgent(backend=HttpAgentBackend(...))`. Agent config (Director/Worker LLM URL, keys, etc.) comes from the runner’s environment (e.g. set in coordinator env and forwarded into the container).

---

## Phase 3: Agent Docker image

Build and run the agent in a container (Director + runner + OpenCode; no Flask/DB in image):

```bash
# Build from repo root
docker build -f Dockerfile.agent -t terarchitect-agent .

# Run one ticket (required env from coordinator or manual)
docker run --rm \
  -e TICKET_ID=<uuid> \
  -e PROJECT_ID=<uuid> \
  -e TERARCHITECT_API_URL=http://host.docker.internal:5010 \
  -e REPO_URL=https://github.com/owner/repo \
  -e GITHUB_TOKEN=... \
  -e TERARCHITECT_WORKER_API_KEY=... \
  terarchitect-agent
```

**Required env:** `TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`. Optional: `GITHUB_TOKEN`, `TERARCHITECT_WORKER_API_KEY`. Agent config (DIRECTOR_LLM_URL, WORKER_LLM_URL, WORKER_MODEL, etc.) must be set in the environment (e.g. coordinator passes them into the container).

Workspace in container: `/workspace` (clone and run happen there). Exit 0 = success; non-zero = failure (coordinator uses this to call jobs/complete or jobs/fail). PR-review jobs have been removed; human feedback now flows through AgentHub channels and Ship Room/Workspace actions.

---

## Phase 4: Coordinator

Long-running process that claims jobs from the queue and runs the Phase 3 agent image. Run on the same host as Docker (or on a dedicated agent machine with Docker).

```bash
# From repo root (coordinator is top-level)
PYTHONPATH=/path/to/terarchitect pip install -r coordinator/requirements.txt
TERARCHITECT_API_URL=http://localhost:5010 \
PROJECT_ID=<uuid> \
[TERARCHITECT_WORKER_API_KEY=...] \
[GITHUB_TOKEN=...] \
[AGENT_IMAGE=terarchitect-agent] \
[MAX_CONCURRENT_AGENTS=1] \
python -m coordinator
```

**Required env**
- **TERARCHITECT_API_URL** — App base URL (used by the coordinator to claim jobs and by the container if you pass it through; see below).
- **PROJECT_ID** or **PROJECT_IDS** — One UUID or comma-separated list. Coordinator only claims jobs for these project(s).

**Optional env**
- **TERARCHITECT_WORKER_API_KEY** — Bearer token for worker API (claim, complete, fail).
- **GITHUB_TOKEN** — Passed to the container for GitHub clone access and for Ship Room release/export PR creation when GitHub is used as the export boundary.
- **AGENT_IMAGE** — Docker image to run (default `terarchitect-agent`).
- **MAX_CONCURRENT_AGENTS** — Max containers at once (default 1).
- **POLL_INTERVAL_SEC** — Seconds between claim attempts when no capacity or no job (default 10).

**Container reachability:** The coordinator passes its env (including `TERARCHITECT_API_URL`) to each container. If the app is on the host and the coordinator runs on the same host, set `TERARCHITECT_API_URL=http://host.docker.internal:5010` (or the host’s IP) so the container can reach the app. On Linux without Docker Desktop you may need `--add-host=host.docker.internal:host-gateway` when running the coordinator’s `docker run` (the coordinator does not add this by default).

---

## Phase 5: OpenCode

The agent uses **OpenCode** only. The container entrypoint starts `opencode serve` and sets `OPENCODE_SERVER_URL`. The Director talks to OpenCode via HTTP: `POST /session`, `POST /session/<id>/message`, and `/summarize` every 30 turns.

**Required env (set in coordinator env and forwarded to container):** `WORKER_LLM_URL`, `WORKER_MODEL`, `WORKER_API_KEY`. **Timeout:** `WORKER_TIMEOUT_SEC` (default 3600).

---

## Phase 6: Codex CLI

The agent uses **Codex CLI** (`@openai/codex`) when `WORKER_MODE=codex`. Terarchitect starts sessions with Codex JSONL output and resumes follow-up turns using the captured Codex thread ID.

**Required env:**
- `WORKER_MODE=codex`
- `WORKER_API_KEY` — OpenAI API key
- `WORKER_MODEL` — optional, model name (e.g. `o4`, `gpt-4o`)

**Optional env:**
- `CODEX_EXTRA_FLAGS` — comma-separated additional flags (e.g. `--max-turns,50`)
- `WORKER_TIMEOUT_SEC` — max seconds per invocation (default 3600)

**Invocation:**
```bash
# First turn
codex exec --json --sandbox workspace-write "<prompt>"

# Follow-up turns
codex exec resume <thread_id> --json "<prompt>"
```

Terarchitect captures `thread_id` from `thread.started` JSONL events and stores it in `_worker_sessions`. Output is parsed from `item.completed` events where `item.type == "agent_message"`; fallback is raw stdout. Codex persists sessions under `~/.codex/sessions/` unless `--ephemeral` is used.

**Dockerfile.agent:** Codex CLI is installed via `npm install -g @openai/codex`.
