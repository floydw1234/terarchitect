# Phase 1: Worker-facing API contract

Used by the standalone runner (Phase 2) and coordinator (Phase 4). All endpoints below accept **Bearer token** auth when `TERARCHITECT_WORKER_API_KEY` is set (Settings UI or env). If unset, no auth is required (dev).

**Header:** `Authorization: Bearer <TERARCHITECT_WORKER_API_KEY>`

The key can be set in **Settings** (Worker API section) or via the `TERARCHITECT_WORKER_API_KEY` environment variable. Settings take precedence when the app reads the value.

---

## Agent endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/projects/<project_id>/tickets/<ticket_id>/worker-context` | Full context (project, graph, ticket, notes, backlog/in_progress/done) + `repo_url` + `agent_settings`. No `project_path`. |
| POST | `/api/projects/<project_id>/tickets/<ticket_id>/logs` | Append log. Body: `session_id`, `step`, `summary`, `raw_output` (optional). |
| POST | `/api/projects/<project_id>/tickets/<ticket_id>/complete` | Mark ticket complete. Body: `pr_url`, `pr_number`, `summary`; optional `review_comment_body`. |
| GET | `/api/projects/<project_id>/tickets/<ticket_id>/cancel-requested` | Poll: `{"cancel_requested": true\|false}`. |

**Memory** (same as app): `POST /api/projects/<project_id>/memory/retrieve` (body: `queries`, optional `num_to_retrieve`), `POST /api/projects/<project_id>/memory/index` (body: `docs`). Use same Bearer token when `TERARCHITECT_WORKER_API_KEY` is set.

---

## Queue endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/worker/jobs/start` | Claim one pending job. Body: `{"project_id": "<uuid>"}`. Returns 200 + job JSON or 204 No Content. |
| POST | `/api/worker/jobs/<job_id>/complete` | Mark job completed (container exited successfully). |
| POST | `/api/worker/jobs/<job_id>/fail` | Mark job failed (container exited with failure). |

**Job response (200):** `job_id`, `ticket_id`, `project_id`, `kind` (`ticket` \| `review`), `repo_url`. For `kind=review`: `pr_number`, `comment_body`, `github_comment_id`.

---

## Testing Phase 1

1. Start the app; `agent_jobs` table is created on startup.
2. Insert a job by hand: `INSERT INTO agent_jobs (id, ticket_id, project_id, kind, status) VALUES (gen_random_uuid(), '<ticket_uuid>', '<project_uuid>', 'ticket', 'pending');`
3. Call `POST /api/worker/jobs/start` with body `{"project_id": "<project_uuid>"}` and (if set) `Authorization: Bearer <token>`. Expect 200 + job payload.
4. Call `GET /api/projects/<id>/tickets/<id>/worker-context` to verify context + agent_settings.
5. Call `POST .../complete` and `POST /api/worker/jobs/<job_id>/complete` to close the job.

The app **enqueues** to `agent_jobs` when a ticket moves to In Progress (Phase 4 app side).

---

## Phase 2: Standalone runner

Run one ticket from the CLI using the Phase 1 API only (no Flask/DB in the runner):

```bash
cd backend
TICKET_ID=<uuid> PROJECT_ID=<uuid> TERARCHITECT_API_URL=http://localhost:5000 REPO_URL=https://github.com/owner/repo [GITHUB_TOKEN=...] [TERARCHITECT_WORKER_API_KEY=...] python -m agent_runner ticket
```

The runner clones the repo, checks out `ticket-{TICKET_ID}`, and runs the Director + worker via `MiddleAgent(backend=HttpAgentBackend(...))`. Agent settings (LLM URL, keys, etc.) come from the worker-context response; override with env vars if needed.

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

**Required env:** `TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`. Optional: `GITHUB_TOKEN` (for private repos and `gh pr create`), `TERARCHITECT_WORKER_API_KEY` (when app has worker API auth). Agent settings (AGENT_LLM_URL, WORKER_LLM_URL, WORKER_MODEL, etc.) are supplied by the worker-context response; override via env if needed.

Workspace in container: `/workspace` (clone and run happen there). Exit 0 = success; non-zero = failure (coordinator uses this to call jobs/complete or jobs/fail). For **review** jobs the container receives `JOB_KIND=review`, `PR_NUMBER`, `COMMENT_BODY` (and optionally `GITHUB_COMMENT_ID`) and runs `agent_runner review`.

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
- **GITHUB_TOKEN** — Passed to the container for clone/push and `gh pr create`.
- **AGENT_IMAGE** — Docker image to run (default `terarchitect-agent`).
- **MAX_CONCURRENT_AGENTS** — Max containers at once (default 1).
- **POLL_INTERVAL_SEC** — Seconds between claim attempts when no capacity or no job (default 10).

**Container reachability:** The coordinator passes its env (including `TERARCHITECT_API_URL`) to each container. If the app is on the host and the coordinator runs on the same host, set `TERARCHITECT_API_URL=http://host.docker.internal:5010` (or the host’s IP) so the container can reach the app. On Linux without Docker Desktop you may need `--add-host=host.docker.internal:host-gateway` when running the coordinator’s `docker run` (the coordinator does not add this by default).

---

## Phase 5: OpenCode

The agent uses **OpenCode** only. The container entrypoint starts `opencode serve` and sets `OPENCODE_SERVER_URL`. The Director talks to OpenCode via HTTP: `POST /session`, `POST /session/<id>/message`, and `/summarize` every 30 turns.

**Required env (from worker-context or env):** `WORKER_LLM_URL`, `WORKER_MODEL`, `WORKER_API_KEY`. **Timeout:** `WORKER_TIMEOUT_SEC` (default 3600).
