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

## Onboarding (GitHub or local import) + AgentHub DAG runtime

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
9. Candidate validation does not change the frontier by itself. Only the winner that is explicitly accepted/integrated advances the project's accepted frontier.
10. **Base selection for dispatch:** new jobs base on **`shipped_frontier`**, or on **one accepted-unshipped parent ticket's attempt base** when the ticket depends on that parent.

Operational rule: a host repo path is **not** the runtime source of truth for normal GitHub-first execution. Local paths exist only for legacy import, local-mode debugging, or recovery workflows.

---

## 0. Database schema updates (existing DBs)

If you created the database before execution mode was added, run:

```sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS execution_mode VARCHAR(50) NOT NULL DEFAULT 'docker';
```

For **auto-ship** (Alembic `024_project_auto_ship` on managed DBs):

```sql
ALTER TABLE projects ADD COLUMN IF NOT EXISTS auto_ship BOOLEAN NOT NULL DEFAULT false;
```

For **auto-ship winner audit** (Alembic `025_ticket_auto_ship_winner_decision` on managed DBs):

```sql
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS auto_ship_winner_decision JSONB;
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
make setup-venv
DATABASE_URL=postgresql://terarchitect:***@localhost:5433/terarchitect backend/run.sh
```

- **App (frontend):** http://localhost:3000  
- **API:** http://localhost:5010  

Set `DATABASE_URL`, `TERARCHITECT_WORKER_API_KEY` (optional), and backend-owned env (GitHub token, embedding, memory LLM). See `backend/README.md`.

---

## 2. Run the coordinator

The coordinator can run on the host or in Docker Compose. In both cases it needs Docker available so it can run `docker run ... terarchitect-agent` for each job. It claims jobs for one or more project IDs and starts agent containers.

### Option A: Run in Docker Compose (recommended for Docker-mode projects)

```bash
docker compose build agent coordinator
docker compose up -d coordinator
```

Compose defaults (fixed in `docker-compose.yml`, not overridden from `.env`):
- Coordinator/backend use **`TERARCHITECT_API_URL=http://backend:5010`** and **`AGENTHUB_URL=http://agenthub:8080`** on the internal network.

Host operator shell (for `ta`, local shipper, `ta ticket run --run-local`):
- **`TERARCHITECT_API_URL=http://127.0.0.1:5010`** — API on the published host port.
- **`TERARCHITECT_AGENTHUB_URL=http://127.0.0.1:8088`** — AgentHub (`8088:8080` publish).

Do **not** put host `TERARCHITECT_API_URL` or `AGENTHUB_URL` in `.env` if Compose loads that file for coordinator/backend: older compose files forwarded `${TERARCHITECT_API_URL}` into the coordinator container and broke job claims (`127.0.0.1` inside the container is not the backend service). Current compose pins in-network URLs; keep host values in your shell profile or a host-only env file that Compose does not read.

Optional **`docker-compose.local-import.example.yml`**: bind-mount a host git checkout into the backend container so `ta project import-agenthub-root` can read the repo from inside Docker. Set `TERARCHITECT_HOST_REPO` to the absolute host path, then:

```bash
export TERARCHITECT_HOST_REPO=/absolute/path/to/checkout
docker compose -f docker-compose.yml -f docker-compose.local-import.example.yml up -d backend
```

Point the project’s `project_path` at `/host-repo` (container path). The override sets `safe.directory` for that mount so git commands succeed when the directory is owned by your host user.

- `DOCKER_NETWORK=terarchitect_default`
- `/var/run/docker.sock` mounted into the coordinator so it can start sibling worker containers

Provide project scope and credentials through the shell or repo `.env` before starting Compose (`PROJECT_ID`/`PROJECT_IDS`, `GITHUB_TOKEN`, `AGENTHUB_API_KEY`, Director/Worker API keys, optional `TERARCHITECT_WORKER_API_KEY`). Use the host-run option below only for `execution_mode=local` projects that need host filesystem access for legacy/debug workflows.

### Option B: Run manually on the host (e.g. from repo root)

```bash
cd /path/to/terarchitect
make setup-venv
TERARCHITECT_API_URL=http://localhost:5010 \
PROJECT_ID=<your-project-uuid> \
GITHUB_TOKEN=<token> \
TERARCHITECT_WORKER_API_KEY=<optional-key> \
make python ARGS='-m coordinator'
```

Use the repo-local `.venv` (`make python`, `make pip`, `make pytest`, or `.venv/bin/python`) for every host-run Python command so Terarchitect dependencies never land in Hermes or another shared venv.

### Option B: Install as a Linux service (recommended for production)

Use the provided systemd unit so the coordinator runs as a daemon and survives reboots:

1. Copy the repo to the host (e.g. `/opt/terarchitect`).
2. Create/update the repo-local venv and install deps:
   `cd /opt/terarchitect && ./scripts/bootstrap-python-env.sh`
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
- **GITHUB_TOKEN** — Only needed for GitHub import/clone or when `ship_target=github` (release/export PR path). Not required for AgentHub-default shipping.
- **AGENT_IMAGE** — Default `terarchitect-agent`. Override if you use a different tag.
- **MAX_CONCURRENT_AGENTS** — Default 1. Global worker cap across all tickets and all same-ticket competing attempts. Same-ticket fan-out may consume multiple slots inside this cap; unrelated graph-conflicting tickets still remain blocked.
- **POLL_INTERVAL_SEC** — Default 10.
- **AGENT_CACHE_VOLUME** — Default `terarchitect-agent-cache`. Named volume mounted at `/cache` in the agent so pip and npm reuse packages across runs. Set to empty to disable.
- **AGENT_DOCKER_MODE** — Default `dind`. `dind`: each agent container runs its own isolated Docker daemon (requires kernel support for nested containers; coordinator adds `--privileged`). `dood`: mount host socket (legacy, shared daemon, unsafe for parallel jobs).
- **DOCKER_NETWORK** — Optional Docker network for worker containers. Compose coordinator defaults this to `terarchitect_default` so workers can reach `backend` and `agenthub` by service name.
- **COORDINATOR_STATE_DIR** — Default `~/.terarchitect/coordinator`. Holds `project_images.json` (project_id → image tag). When a Docker run succeeds for a project, that image is saved so the next job for that project uses it.
- **COORDINATOR_REPO_ROOT** — Repo root path (for direct agent run when Docker fails). Default: parent of coordinator package. Set if you install elsewhere (e.g. systemd override).

**Fallback when Docker fails:** If `docker run` for a job fails (e.g. image not found, container exits on start), the coordinator runs the ticket agent **on the host** (`.venv/bin/python -m agent.agent_runner ticket`) with the same job env and passes the Docker error in `TERARCHITECT_DOCKER_RUN_ERROR`. The agent logs that error to the ticket so the run can continue or you can fix the image. For fallback to work, install all host-run deps in the repo-local venv with `./scripts/bootstrap-python-env.sh`.

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

For competing attempts, also expect attempt metadata such as `ATTEMPT_SLOT`, `ATTEMPT_INDEX`, and `ATTEMPT_COUNT`. Target-design strategy metadata should map one of five operator-visible strategies into both job metadata and worker env:

1. `minimal-patch`
2. `root-cause-debugger`
3. `test-first`
4. `refactor-forward`
5. `systems-explorer`

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
4. **Review candidates, then choose a winner:** worker completions create validated candidates. Operators compare sibling attempts, choose a winner, and may still leave that winner unintegrated temporarily.
5. **Accept/integrate winner:** only the chosen winner that advances `accepted_frontier_id` unblocks downstream dependencies. Ticket-level PR review is not part of swarm mode.
6. **Promotion candidate review:** the target operator concept is a stable promotion candidate built from accepted/integrated attempts whose dependency closure is valid against `shipped_frontier`.
7. **Inspect ShipRun:** A `ShipRun` should be created from that stable candidate set, then reviewed for composed commit, test output, and ship readiness.
8. **Ship final boundary:** When the `ShipRun` is `ready_to_ship`, shipping advances `shipped_frontier`. By default projects use **`ship_target=agenthub`** (no GitHub release PR). Opt in to GitHub publishing with `ta project set-ship-target <project> github` (requires `github_url`); merged tips are imported into AgentHub automatically.

No in-process agent runs in the app; all execution is in containers started by the coordinator.

## Attempt inspection vs. competing attempts

Normal execution already gives you inspectable `TicketAttempt` records through the ticket/project attempt APIs and the attempt detail UI. Explicit competing attempts are a separate operator choice for one ticket: rerun from the current frontier, usually with the product default of `3` attempts, inspect each sibling candidate, choose one winner, and only then accept/integrate that winner if you want to unblock dependents. See `docs/COMPETING_ATTEMPTS.md` for the request body, limits, lifecycle, concurrency rules, and caveats.

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

**Default:** agents (or automation) run the promotion loop via CLI/API. Humans set direction (projects, tickets, protected areas — see [`plans/ROADMAP.md`](../plans/ROADMAP.md)) and may override or revert any step until full autonomy lands.

Agent-run spine (optional human override at each step):

1. Agent completes work → validated `TicketAttempt`(s).
2. Evaluate / choose winner → `ta ticket choose-winner …`
3. Accept / integrate winner → `ta ticket accept-winner …`
4. Create promotion candidate → `ta ship create-candidate …`
5. Compose and inspect `ShipRun` → `ta ship compose-candidate …`, `ta ship run …`
6. Ship (AgentHub default) → `ta ship ship-run …` or `ta ship ship-candidate …`

One command for the full decomposed path: **`ta ship operator-loop <project_id> <ticket_id> <attempt_id>`**.

End-to-end from a goal string: **`ta run <project_id> "<goal or ticket title>"`** (creates the ticket, dispatches attempts, waits, then ships or exits non-zero on failure).

**Auto-ship (opt-in):** `ta project set-auto-ship <project_id> on|off`. When `on`, after the last attempt in a ticket batch validates, Terarchitect picks a winner (LLM judge when multiple eligible attempts share the same ticket context, with the legacy test-pass / lowest-`attempt_num` rule as fallback), then runs choose-winner → accept → candidate → compose → ship (`agenthub` target). For `github` target, automation stops after the release PR is opened (compose completes).

The judge uses the same backend LLM config as graph generation and other server features: `FRONTEND_LLM_*`, falling back to `DIRECTOR_*` (`get_frontend_llm_settings`). With exactly one eligible attempt it skips the LLM (`judged_by: single`). On any judge error it falls back (`judged_by: fallback`) and logs the reason.

The decision is stored on the ticket as `auto_ship_winner_decision` (winner id, rationale, `judged_by`, model name, optional per-attempt notes) and echoed on the ticket-complete API response under `auto_ship.winner_pick` when auto-ship runs.

**In-compose auto-ship runtime:** The backend container must expose in-network `AGENTHUB_URL` (for example `http://agenthub:8080`) and `AGENTHUB_API_KEY` (unless `AGENTHUB_AUTH_DISABLED=1` on AgentHub). When the backend enforces worker API auth (`TERARCHITECT_WORKER_API_KEY` or `TERARCHITECT_WORKER_API_KEY_PATH`), the in-process shipper must resolve the same token; when worker auth is unset (typical local/spark dev), no Bearer token is required. `WORKER_API_KEY` is only the worker LLM provider key (OpenCode/Codex), not backend worker auth. Host-only `TERARCHITECT_AGENTHUB_URL` is for shell `ta` / local shipper on the operator machine, not for the backend service. Set `TERARCHITECT_IN_CONTAINER=1` on backend/coordinator services (see `docker-compose.yml`) so in-process auto-ship keeps in-network `AGENTHUB_URL`. Misconfiguration is logged at backend startup and blocks in-process compose with a clear error.

**Revert:** `ta ship revert <project_id>` moves `shipped_frontier` back to the previous shipped run’s `base_main_hash` (recorded as a revert `ShipRun`; commits are not deleted). Use `--to <ship_run_id>` to revert to a specific shipped run’s base.

Other ship CLI: `ta ship candidates`, `ta ship candidate`, `ta ship compose-candidate`, `ta ship compose-run`, `ta ship run`, `ta ship ship-run`, `ta ship ship-candidate`, `ta ship feedback`, `ta ship revert`. Project ship mode: **`ta project set-ship-target <project_id> agenthub|github`**.

---

## Headless CLI dogfood (spark)

**Authorized local checkout for dogfood/testing:** `/home/william/Documents/codingProj/terarchitect` on William's Ubuntu host **spark**.

Spark is **headless** (no screen or desktop). Do **not** use the browser, open `localhost:3000`, or take UI screenshots to verify the MVP loop. Prove behavior with the **`ta` CLI** and HTTP APIs against the running backend on that checkout.

**Execution path:** Local AgentHub + OpenCode remains the preferred runtime. GitHub release PRs are **opt-in per project** (`ship_target=github`); default **`ship_target=agenthub`** ships entirely inside AgentHub with no PR volume.

**MVP spine to verify (CLI-first):**

Preferred one command:

```bash
ta ship operator-loop <project_id> <ticket_id> <attempt_id>
```

Stepwise equivalent:

1. After worker attempts complete: `ta ticket choose-winner …` then `ta ticket accept-winner …`
2. `ta ship create-candidate <project_id> …` (when not folded into operator-loop)
3. Ship Room — stepwise (no coordinator container required when using `--sync` / `compose-run`):
   - `ta ship candidates <project_id>`
   - `ta ship compose-candidate <project_id> <candidate_id> --sync` (queues then runs `python -m agent.shipper` locally)
   - Or, if a run is already queued: `ta ship compose-run <project_id> <run_id>`
   - `ta ship run <project_id> <run_id>` (inspect)
   - `ta ship ship-run <project_id> <run_id>` or `ta ship ship-candidate <project_id> <candidate_id>`
4. Confirm `shipped_frontier` advanced (API or `ta ship candidates --json`).

Legacy API shortcut `ta ship happy-path` still exists; prefer **`ta ship operator-loop`** for dogfood.

Use `--output json` / `--json` when scripting. No UI required for verification on spark.

**AgentHub URL on spark:** Host-side `ta ship … --sync` and `ta ticket run --run-local` must reach AgentHub at `http://127.0.0.1:8088`. Set **`TERARCHITECT_AGENTHUB_URL=http://127.0.0.1:8088`** in the host shell (not in a `.env` file that Compose passes into containers). The CLI shipper and local ticket runner prefer `TERARCHITECT_AGENTHUB_URL`, then fall back to `AGENTHUB_URL` with docker-hostname remapping when needed.

**AgentHub-only ship frontier shape:** Compose merges accepted attempt commits onto `shipped_frontier` so the **shipped composed commit’s parent is the prior frontier**, not the worker’s intermediate step commit. The next ticket bases from the new `shipped_frontier` tip.
