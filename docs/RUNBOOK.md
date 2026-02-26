# Terarchitect deployment runbook

This runbook describes how to run the Terarchitect app, coordinator, and agent image after the Docker/coordinator migration (Phases 1–6). **The app does not run the agent in-process;** execution is done by a coordinator that starts agent containers.

---

## Architecture

| Component | Role |
|-----------|------|
| **App** | Flask API + DB + frontend. Enqueues jobs to `agent_jobs` when a ticket moves to In Progress or a PR review comment is created. Does **not** run the Director or worker. |
| **Coordinator** | **Host-side Python app** (not a Docker container). It claims jobs via `POST /api/worker/jobs/start`, runs `docker run ... terarchitect-agent` for each job, and calls `POST .../complete` or `.../fail` when the container exits. Must run on a host that has Docker. |
| **Agent image** | Single Docker image (`terarchitect-agent`). One container per job: clones repo, runs Director + worker (OpenCode, Aider, Claude Code, Gemini, or Codex), pushes and opens PR, exits. |

**Execution mode (per project):** In project settings you can choose **Docker** (default: coordinator runs agent in a container, repo is cloned at runtime) or **Local** (coordinator runs the agent on the host at a configured project path; no clone).

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

Set `DATABASE_URL`, `TERARCHITECT_WORKER_API_KEY` (optional), and any memory/embedding env as needed. See `backend/README.md` for full env list.

---

## 2. Run the coordinator

The coordinator is a **Python app that runs on the host** (not in Docker). It needs Docker available so it can run `docker run ... terarchitect-agent` for each job. It claims jobs for one or more project IDs and starts agent containers.

### Option A: Run manually (e.g. from repo root)

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

- **TERARCHITECT_API_URL** — App base URL. When the coordinator runs on the same host as the app, use `http://localhost:5010`. Agent **containers** must reach the app: set `TERARCHITECT_API_URL=http://host.docker.internal:5010` so the coordinator passes that into each container (on Linux the coordinator adds `--add-host=host.docker.internal:host-gateway` when the URL contains `host.docker.internal`).
- **PROJECT_ID** or **PROJECT_IDS** — Comma-separated UUIDs of projects this coordinator should claim jobs for.
- **GITHUB_TOKEN** — Passed to the container for git clone/push and `gh pr create`.
- **AGENT_IMAGE** — Default `terarchitect-agent`. Override if you use a different tag.
- **MAX_CONCURRENT_AGENTS** — Default 1. Increase to run multiple jobs in parallel.
- **POLL_INTERVAL_SEC** — Default 10.
- **AGENT_CACHE_VOLUME** — Default `terarchitect-agent-cache`. Named volume mounted at `/cache` in the agent so pip and npm reuse packages across runs. Set to empty to disable.
- **AGENT_DOCKER_MODE** — Default `dind`. `dind`: each agent container runs its own isolated Docker daemon (requires kernel support for nested containers; coordinator adds `--privileged`). `dood`: mount host socket (legacy, shared daemon, unsafe for parallel jobs).
- **COORDINATOR_STATE_DIR** — Default `~/.terarchitect/coordinator`. Holds `project_images.json` (project_id → image tag). When a Docker run succeeds for a project, that image is saved so the next job for that project uses it.
- **COORDINATOR_REPO_ROOT** — Repo root path (for direct agent run when Docker fails). Default: parent of coordinator package. Set if you install elsewhere (e.g. systemd override).

**Fallback when Docker fails:** If `docker run` for a job fails (e.g. image not found, container exits on start), the coordinator runs the agent **on the host** (`python -m agent.agent_runner ticket` or `review`) with the same job env and passes the Docker error in `TERARCHITECT_DOCKER_RUN_ERROR`. The agent logs that error to the ticket so the run can continue or you can fix the image. For fallback to work, install agent deps in the same venv: `pip install -r agent/requirements.txt`.

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

OpenCode worker env (`WORKER_LLM_URL`, `WORKER_MODEL`, `WORKER_API_KEY`) can be set in the app Settings (sent via worker-context) or passed by the coordinator into the container.

---

## 4. Single-box vs two-box

**Single-box (dev / small deploy)**  
- App, coordinator, and Docker on the same machine.  
- Run app and coordinator as above. Set `TERARCHITECT_API_URL=http://host.docker.internal:5010` so containers can reach the app.  
- On Linux, the coordinator adds `--add-host=host.docker.internal:host-gateway` when the URL contains `host.docker.internal`.

**Two-box (production)**  
- **Machine A:** App only (API + DB + frontend). No Docker, no coordinator.  
- **Machine B:** Coordinator + Docker. Set `TERARCHITECT_API_URL=https://machine-a.example.com` (or the app’s public URL). Coordinator claims jobs and runs containers on Machine B.  
- Agent containers need network access to the app (for worker-context, logs, complete, memory) and to GitHub (clone/push). They do not need access to the DB.

---

## 5. Worker types and env

See **docs/PHASE1_WORKER_API.md** → Phase 5 for OpenCode and required env. The app sends agent settings (from Settings UI) in the worker-context response; the container can override with env.

---

## 6. Quick verification

1. **App:** Open http://localhost:3000, create a project, add a ticket, move it to In Progress. A row should appear in `agent_jobs` with `status=pending`.
2. **Coordinator:** Run the coordinator with that project’s `PROJECT_ID`. It should claim the job, start a container, and after the run call complete or fail.
3. **Logs:** Ticket logs and PR appear in the UI via the API; the agent posts logs and completion through the worker API.

No in-process agent runs in the app; all execution is in containers started by the coordinator.
