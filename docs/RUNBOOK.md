# Terarchitect deployment runbook

This runbook describes how to run the Terarchitect app, coordinator, and agent image after the Docker/coordinator migration (Phases 1–6). **The app does not run the agent in-process;** execution is done by a coordinator that starts agent containers.

---

## Architecture

| Component | Role |
|-----------|------|
| **App** | Flask API + DB + frontend. Enqueues jobs to `agent_jobs` when a ticket moves to In Progress. Does **not** run the Director or worker. |
| **Coordinator** | Claims jobs via `POST /api/worker/jobs/start`, runs `docker run ... terarchitect-agent` for each job, and calls `POST .../complete` or `.../fail` when the container exits. Can run on the host or as a Docker Compose service, but must have Docker access. |
| **Agent image** | Single Docker image (`terarchitect-agent`). One container per job: materializes the selected AgentHub base leaf into an isolated workspace, runs Director + worker (OpenCode, Claude Code, or Codex), publishes an AgentHub child attempt, exits. |

**Execution mode (per project):** In the project’s execution settings in the UI you can choose **Docker** (default and normal GitHub-first path: coordinator runs agent in a container, materializes the workspace from AgentHub at runtime, no host repo mount) or **Local** (legacy/debug path: coordinator runs the agent on the host at a configured project path).

## GitHub-first onboarding + AgentHub DAG runtime

Treat the AgentHub DAG as the runtime source of truth.

Canonical lifecycle:

1. Operator creates/imports the project from a **GitHub URL + ref**.
2. AgentHub imports that repo state and creates the project's initial DAG state.
3. The project tracks an **accepted_frontier_id** that represents the accepted DAG frontier.
4. When a ticket is prepared for execution, Terarchitect records a `base_leaf_id` for that ticket/job from the accepted frontier.
5. The coordinator claims the job and starts the Docker worker.
6. The worker uses `REPO_URL`, `AGENTHUB_URL`, and `BASE_LEAF_ID`/`BASE_HASH` to materialize the requested base leaf into the workspace.
7. The worker runs Director + worker backend, then publishes a child leaf/attempt to AgentHub.
8. Terarchitect stores that result as a `TicketAttempt`.
9. Human acceptance advances the project's accepted frontier; future tickets should base from that frontier.

Operational rule: a host repo path is **not** the runtime source of truth for normal GitHub-first execution. Local paths exist only for legacy import, local-mode debugging, or recovery workflows.

---

## 0. Database schema updates (existing DBs)

If you created the database before execution mode was added, run:

```sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(50) NOT NULL DEFAULT 'docker';
```

---

## 1. Run the app (API + DB + frontend only)

The app serves the UI and API and enqueues work. It does **not** need any worker CLI on the host.

**Option A: All in Docker (recommended)**

```bash
docker compose up -d
```

This starts **postgres**, **backend** (Flask API on port 5010), and **frontend** (port 3000). The backend waits for Postgres to be healthy before starting.

**Option B: Backend on host**

```bash
docker compose up -d postgres frontend
cd backend && pip install -r requirements.txt
DATABASE_URL=postgresql://terarchitect:terarchitect@localhost:5433/terarchitect flask run --host=0.0.0.0 --port=5010
```

- **App (frontend):** http://localhost:3000  
- **API:** http://localhost:5010  

Set `DATABASE_URL`, `TERARCHITECT_WORKER_API_KEY` (optional), and backend-owned env (GitHub token, embedding, memory LLM). See `backend/README.md` and `example.env`.

---

## 2. Run the coordinator

The coordinator can run on the host or in Docker Compose. In both cases it needs Docker available so it can run `docker run ... terarchitect-agent` for each job. It claims jobs for one or more project IDs and starts agent containers.

### Option A: Run in Docker Compose (recommended for Docker-mode projects)

```bash
docker compose build agent coordinator
docker compose up -d coordinator
```

Compose defaults:
- `TERARCHITECT_API_URL=http://backend:5010`
- `AGENTHUB_URL=http://agenthub:8080`
- `DOCKER_NETWORK=terarchitect_default`
- `/var/run/docker.sock` mounted into the coordinator so it can start sibling worker containers

Provide project scope and credentials through the shell or repo `.env` before starting Compose (`PROJECT_ID`/`PROJECT_IDS`, `GITHUB_TOKEN`, `AGENTHUB_API_KEY`, Director/Worker API keys, optional `TERARCHITECT_WORKER_API_KEY`). Use the host-run option below only for `execution_mode=local` projects that need host filesystem access for legacy/debug workflows.

### Option B: Run manually on the host (e.g. from repo root)

```bash
cd /path/to/terarchitect
pip install -r coordinator/requirements.txt   # or use a venv
TERARCHITECT_API_URL=http://localhost:5010 \
PROJECT_ID=<your-project-uuid> \
GITHUB_TOKEN=<token> \
[TERARCHITECT_WORKER_API_KEY=<key>] \
python -m coordinator
```

Set `PYTHONPATH` to the repo root if needed so `python -m coordinator` finds the package (e.g. `PYTHONPATH=/path/to/terarchitect`).

### Option B: Install as a Linux service (recommended for production)

Use the provided systemd unit so the coordinator runs as a daemon and survives reboots:

1. Copy the repo to the host (e.g. `/opt/terarchitect`).
2. Create a venv and install deps:  
   `cd /opt/terarchitect && python3 -m venv .venv && .venv/bin/pip install -r coordinator/requirements.txt`
3. Build the agent image on that host:  
   `docker build -f Dockerfile.agent -t terarchitect-agent .`
4. Copy the service file:  
   `sudo cp coordinator/terarchitect-coordinator.service /etc/systemd/system/`
5. Create env file:  
   `sudo mkdir -p /etc/terarchitect`  
   `sudo tee /etc/terarchitect/coordinator.env` with `TERARCHITECT_API_URL`, `PROJECT_ID`, `GITHUB_TOKEN`, and optionally `TERARCHITECT_WORKER_API_KEY`, `AGENT_IMAGE`, `MAX_CONCURRENT_AGENTS`.
6. If the repo is not at `/opt/terarchitect`, override the path:  
   `sudo systemctl edit terarchitect-coordinator` and set `WorkingDirectory`, `Environment=PYTHONPATH=...`, `ExecStart=...` to your install path.
7. Enable and start:  
   `sudo systemctl daemon-reload && sudo systemctl enable --now terarchitect-coordinator`

See comments in `coordinator/terarchitect-coordinator.service` for details.

### Coordinator env

- **TERARCHITECT_API_URL** — App base URL. Compose coordinator default: `http://backend:5010`. Host coordinator on the same machine as the app: use `http://host.docker.internal:5010` so the coordinator passes a container-reachable URL into each worker (on Linux the coordinator adds `--add-host=host.docker.internal:host-gateway` when the URL contains `host.docker.internal`).
- **PROJECT_ID** or **PROJECT_IDS** — Optional. Comma-separated UUIDs to restrict which projects this coordinator serves. If unset, the coordinator fetches all project IDs from `GET /api/worker/projects` at startup (or claims from any project if the fetch fails).
- **GITHUB_TOKEN** — Passed to the container for GitHub clone access and for Ship Room release/export PR creation when using GitHub as the export boundary.
- **AGENT_IMAGE** — Default `terarchitect-agent`. Override if you use a different tag.
- **MAX_CONCURRENT_AGENTS** — Default 1. Increase to run multiple jobs in parallel.
- **POLL_INTERVAL_SEC** — Default 10.
- **AGENT_CACHE_VOLUME** — Default `terarchitect-agent-cache`. Named volume mounted at `/cache` in the agent so pip and npm reuse packages across runs. Set to empty to disable.
- **AGENT_DOCKER_MODE** — Default `dind`. `dind`: each agent container runs its own isolated Docker daemon (requires kernel support for nested containers; coordinator adds `--privileged`). `dood`: mount host socket (legacy, shared daemon, unsafe for parallel jobs).
- **DOCKER_NETWORK** — Optional Docker network for worker containers. Compose coordinator defaults this to `terarchitect_default` so workers can reach `backend` and `agenthub` by service name.
- **COORDINATOR_STATE_DIR** — Default `~/.terarchitect/coordinator`. Holds `project_images.json` (project_id → image tag). When a Docker run succeeds for a project, that image is saved so the next job for that project uses it.
- **COORDINATOR_REPO_ROOT** — Repo root path (for direct agent run when Docker fails). Default: parent of coordinator package. Set if you install elsewhere (e.g. systemd override).

**Fallback when Docker fails:** If `docker run` for a job fails (e.g. image not found, container exits on start), the coordinator runs the ticket agent **on the host** (`python -m agent.agent_runner ticket`) with the same job env and passes the Docker error in `TERARCHITECT_DOCKER_RUN_ERROR`. The agent logs that error to the ticket so the run can continue or you can fix the image. For fallback to work, install agent deps in the same venv: `pip install -r agent/requirements.txt`.

---

## 3. Build and use the agent image

Build from repo root:

```bash
docker build -f Dockerfile.agent -t terarchitect-agent .
```

The image includes the Director, standalone runner, OpenCode (HTTP server started by entrypoint), Claude Code CLI, Node.js 20 (for `npm install` / `npm test` in project repos), and the full **Docker daemon + CLI** (for `docker build`, `docker compose`, and integration tests inside each agent container).

**Docker isolation mode (`AGENT_DOCKER_MODE`):**

| Mode | How it works | When to use |
|------|-------------|-------------|
| `dind` (**default**) | Each agent container runs its own isolated `dockerd` (started by the entrypoint). The coordinator adds `--privileged` to `docker run`. Concurrent agents never conflict on container names, ports, or networks. | Recommended for all new deployments. Requires a host kernel that supports nested overlay2 (standard Linux ≥ 4.0). |
| `dood` | Mounts the host Docker socket (`/var/run/docker.sock`) — all agents share one daemon. Set `AGENT_MOUNT_DOCKER_SOCKET=0` together with `DOCKER_HOST` to point to an external sidecar. | Legacy / hosts where `--privileged` is not allowed. Only safe with `MAX_CONCURRENT_AGENTS=1`. |

Set `AGENT_DOCKER_MODE=dood` on the coordinator to revert to the old socket-mount behaviour.

Director env is also set in the **coordinator** environment and forwarded into the agent runtime. The default Director configuration is:

```bash
DIRECTOR_PROVIDER=custom
DIRECTOR_LLM_URL=https://openrouter.ai/api
DIRECTOR_MODEL=google/gemini-2.5-flash-lite
OPENROUTER_API_KEY=...
```

If you keep that default OpenRouter setup, `DIRECTOR_API_KEY` can stay blank; the agent resolves it from `OPENROUTER_API_KEY`. Do not put real secrets in tracked files.

OpenCode worker env (`WORKER_LLM_URL`, `WORKER_MODEL`, `WORKER_API_KEY`) must be set in the **coordinator** environment; the coordinator forwards them into the container.

---

## 4. Single-box vs two-box

**Single-box (dev / small deploy)**  
- App, coordinator, and Docker on the same machine.  
- Recommended: run app + coordinator in Compose. Workers join `terarchitect_default` and reach `backend`/`agenthub` by service name.  
- Host coordinator remains available; if you use it, set `TERARCHITECT_API_URL=http://host.docker.internal:5010` so worker containers can reach the app.  
- On Linux, the coordinator adds `--add-host=host.docker.internal:host-gateway` when the URL contains `host.docker.internal`.

**Two-box (production)**  
- **Machine A:** App only (API + DB + frontend). No Docker, no coordinator.  
- **Machine B:** Coordinator + Docker. Set `TERARCHITECT_API_URL=https://machine-a.example.com` (or the app’s public URL). Coordinator claims jobs and runs containers on Machine B.  
- Agent containers need network access to the app (for worker-context, logs, complete, memory) and to GitHub (clone/push). They do not need access to the DB.

---

## 5. Worker types and env

See **docs/PHASE1_WORKER_API.md** → Phase 5 for OpenCode and required env. Agent config is not sent by the app; set it in the coordinator env so it is forwarded to the worker container. Docker-mode worker contract includes `TERARCHITECT_API_URL`, `AGENTHUB_URL`, `AGENTHUB_API_KEY` or `AGENTHUB_API_KEY_PATH`, explicit `BASE_LEAF_ID`/`BASE_HASH`, and the Director/Worker/Codex env required for the selected backend.

### Environment/config by component

Backend/app:
- `DATABASE_URL`
- `TERARCHITECT_WORKER_API_KEY` if worker API auth is enabled
- GitHub token (`github_agent_token` or `GITHUB_TOKEN`/`GH_TOKEN`) for UI/server-side GitHub actions
- memory/embedding env from `backend/README.md`

Coordinator:
- `TERARCHITECT_API_URL`
- `PROJECT_ID` or `PROJECT_IDS`
- `GITHUB_TOKEN`
- `AGENT_IMAGE`
- `AGENTHUB_URL`
- `AGENTHUB_API_KEY` or `AGENTHUB_API_KEY_PATH`
- `AGENTHUB_AGENT_ID` when required by your AgentHub deployment
- Director env: `DIRECTOR_PROVIDER`, `DIRECTOR_LLM_URL`, `DIRECTOR_MODEL`, `DIRECTOR_API_KEY` or path, optional `OPENROUTER_API_KEY`
- Worker env for selected backend: `WORKER_MODE`, `WORKER_LLM_URL`, `WORKER_MODEL`, `WORKER_API_KEY` or path, `CODEX_EXTRA_FLAGS`, `CODEX_SANDBOX`, `CLAUDE_CODE_EXTRA_TOOLS`

Worker container:
- receives the coordinator env above
- requires job-scoped `REPO_URL`, `BASE_LEAF_ID`/`BASE_HASH`, `PROJECT_ID`, `TICKET_ID`, `TERARCHITECT_API_URL`
- materializes the DAG base in `/workspace`; it should not require a host repo mount for normal Docker/GitHub-first runs

---

## 6. Quick verification

1. **App:** Open http://localhost:3000, create a project, add a ticket, move it to In Progress. A row should appear in `agent_jobs` with `status=pending`.
2. **Coordinator:** Run the coordinator with that project’s `PROJECT_ID`. It should claim the job, start a container, and after the run call complete or fail.
3. **Logs and attempts:** Ticket logs and the resulting AgentHub attempt appear in the UI via the API; the agent posts logs and completion through the worker API.
4. **Accept attempt:** A human accepts the `TicketAttempt` that should move forward. Ticket-level PR review is not part of swarm mode.
5. **Promotion candidate review:** The target operator concept is a stable promotion candidate built from accepted attempts whose dependency closure is valid against `shipped_frontier`.
6. **Inspect ShipRun:** A `ShipRun` should be created from that stable candidate set, then reviewed for composed commit, test output, and ship readiness.
7. **Ship/merge final boundary:** When the `ShipRun` is `ready_to_ship`, shipping advances `shipped_frontier`.

No in-process agent runs in the app; all execution is in containers started by the coordinator.

## Attempt inspection vs. competing attempts

Normal execution already gives you inspectable `TicketAttempt` records through the ticket/project attempt APIs and the attempt detail UI. Explicit competing attempts are a separate operator choice for one ticket: rerun from the current frontier with `attempt_count > 1`, inspect each sibling attempt, then accept one verified winner. See `docs/COMPETING_ATTEMPTS.md` for the request body, limits, lifecycle, and caveats.

## Troubleshooting checklist

Missing frontier / wrong base selected:
- confirm the project was imported from the intended GitHub URL and ref
- confirm the project has a current accepted frontier before queueing the ticket
- inspect the queued job payload for the expected `base_leaf_id` / `base_hash`

Stale attempt / ticket based on old work:
- verify the latest accepted attempt actually advanced the project's accepted frontier
- requeue the ticket only after confirming the desired parent leaf is in the accepted frontier
- avoid treating an old local checkout as authoritative; inspect AgentHub leaf ancestry instead

Missing AgentHub key:
- verify `AGENTHUB_API_KEY` or `AGENTHUB_API_KEY_PATH` is set in coordinator env
- if using Compose, confirm the variable is present in the shell or `.env` that started `docker compose`
- verify the worker container inherited the key/path from the coordinator

Docker worker cannot materialize base leaf:
- verify `AGENTHUB_URL` resolves from the worker network
- confirm the requested `BASE_LEAF_ID` exists in AgentHub and belongs to the expected project DAG
- confirm the worker has GitHub access for the referenced repo/ref when import/fetch is required
- check for mismatched `REPO_URL`, `BASE_HASH`, or project import state

General execution drift back to local-path workflow:
- if docs, scripts, or operator habits assume branch sync on a host checkout, treat that as legacy
- for normal runs, re-center on: GitHub import -> AgentHub DAG -> accepted frontier -> worker materialization -> publish child -> accept advances frontier

## Operator flow

Keep operators on one path for swarm projects:

1. agent completes work
2. human accepts the `TicketAttempt`
3. review a stable promotion candidate
4. create and inspect the `ShipRun`
5. ship/merge at the final boundary

Agents and coordinators are the primary users of the system. The UI remains a review/ship boundary for humans, and the long-term CLI/API contract is candidate review plus `ShipRun` execution.

Current live code still keeps some legacy wave-keyed backend routes for compatibility and test coverage. Treat them as implementation details, not operator commands; the operator path is `ta ship candidates`, `ta ship candidate`, `ta ship compose-candidate`, `ta ship run`, `ta ship ship-run`, and `ta ship ship-candidate`.
