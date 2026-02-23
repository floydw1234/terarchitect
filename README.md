# Terarchitect

Terarchitect is a visual-first SDLC orchestrator: model your system as a graph, write tickets on a Kanban board, and let a **Director → Worker** agent pair implement changes in your repo and open PRs.

- **You stay in control**: every change ships as a PR you review.
- **One container per job**: reproducible, isolated runs.
- **Coordinator-friendly**: run the coordinator on the same machine as the app, or on a completely separate machine.

If you’ve ever wanted “Kanban → PRs” with guardrails, this is it.

<p align="center">
  <img src="pictures/project_view.png" alt="Terarchitect UI (project view)" width="960" />
</p>

## Screenshots

| Architecture graph | Kanban execution |
|---|---|
| <img src="pictures/graph.png" alt="Architecture graph" width="460" /> | <img src="pictures/kanban.png" alt="Kanban board" width="460" /> |
| <img src="pictures/projects.png" alt="Projects list" width="460" /> | <img src="pictures/ticket.png" alt="Ticket details" width="460" /> |

---

## What you get

- **Architecture graph**: encode components + interfaces, not just TODO lists
- **Kanban-driven execution**: moving a ticket to *In Progress* enqueues an agent job
- **Director/Worker separation**: strategy (Director) vs local execution + tools (Worker/OpenCode)
- **PR-first workflow**: branch per ticket (`ticket-{id}`), PR opened automatically
- **Runs anywhere Docker runs**: single-box dev or two-box production

---

## System architecture (app + coordinator + agent)

| Component | What it does | Where it runs |
|-----------|--------------|---------------|
| **App** | Flask API + Postgres + React frontend. Stores projects/graph/tickets/logs and enqueues jobs. Does **not** execute the agent. | **Docker Compose** (`postgres`, `backend`, `frontend`) |
| **Coordinator** | Claims jobs from the API and starts one agent container per job. | **Host process** (can be a different machine with Docker) |
| **Agent image** | Director + OpenCode. Clones the project repo, implements the ticket, pushes, opens PR, exits. | **Docker container** started by the coordinator |

High-level flow: **UI → enqueue → coordinator claims → agent container runs → PR created → human reviews**.

---

## Quick start (local dev)

### 1) Start the app (API + DB + UI)

```bash
docker compose up -d
```

- **UI**: `http://localhost:3000`
- **API**: `http://localhost:5010`
- **Postgres**: host port `5433` 

### 2) Build the agent image (once)

```bash
docker build -f Dockerfile.agent -t terarchitect-agent .
```

### 3) Run the coordinator (so tickets actually execute)

The coordinator is not part of docker compose. Run it on any host with Docker.

```bash
pip install -r coordinator/requirements.txt
TERARCHITECT_API_URL=http://localhost:5010 \
PROJECT_ID=<your-project-uuid> \
GITHUB_TOKEN=<token> \
TERARCHITECT_WORKER_API_KEY=<optional-worker-api-key> \
python -m coordinator
```

Tip: set `PYTHONPATH=/path/to/terarchitect` if your environment needs it.

**Concurrency note (important):** for now, the coordinator should run **one agent job at a time** (default `MAX_CONCURRENT_AGENTS=1`).
Even though jobs run in separate agent containers, the agent containers typically use **the host Docker daemon** (Docker-out-of-Docker via `/var/run/docker.sock`) for `docker compose` and integration tests, which can collide under parallel runs.
We’ll revisit safe parallelism once we switch to real Docker-in-Docker (see TODO below).

---

## Deployments that scale

### Single-box (dev / small deploy)

- Run the app: `docker compose up -d`
- Run the coordinator on the same host
- Set `TERARCHITECT_API_URL=http://host.docker.internal:5010` so agent containers can reach the app
  - On Linux, the coordinator automatically adds `--add-host=host.docker.internal:host-gateway`

### Two-box (production)

- **Machine A**: app only (docker compose). No coordinator required here.
- **Machine B**: coordinator + Docker. Build the agent image here. Run the coordinator here.
- Set `TERARCHITECT_API_URL=https://machine-a.example.com` (or the public URL of Machine A)

Agent containers only need:
- network access to the app (worker-context, logs, complete/fail)
- network access to GitHub (clone/push/PR)

They do **not** need direct DB access.

Full ops notes (systemd, env, verification): see `docs/RUNBOOK.md`.

---

## How execution works

1. You create a project (with a GitHub repo URL), then add tickets.
2. Moving a ticket to **In Progress** enqueues a job.
3. The coordinator claims the job and runs:
   - `docker run ... -e REPO_URL=... -e TICKET_ID=... terarchitect-agent`
4. Inside the container, the agent:
   - clones your repo
   - creates branch `ticket-{id}`
   - runs Director + OpenCode to implement
   - pushes branch and opens a PR
   - exits

No mixing with your project’s Dockerfile. The agent image is built once and reused.

---

## Repo layout

| Path | Role |
|------|------|
| `backend/` | Flask API (served by docker compose). Stores graph/tickets/logs; enqueues jobs only. |
| `frontend/` | React UI (served by docker compose). |
| `coordinator/` | Host-side Python process. Claims jobs and starts agent containers. |
| `agent/` | Director + runner + OpenCode wiring. Packaged into the agent image. |

---

## Docs

- `docs/RUNBOOK.md`: deployments, coordinator env, systemd, verification
- `docs/PHASE1_WORKER_API.md`: worker API contract and behavior

---

## TODO / Roadmap

- **Docker-in-Docker for safe parallelism**: move from host-socket Docker-out-of-Docker to per-job DinD (sidecar daemon) so multiple tickets can run concurrently without `docker compose` collisions. After that, we can raise `MAX_CONCURRENT_AGENTS` beyond 1 safely.

---

## Contributing

PRs welcome. Keep changes focused and verifiable (tests where possible). If you’re shipping a behavior change, include a short “why” in the PR description.
