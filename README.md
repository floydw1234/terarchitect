# Terarchitect

Terarchitect is an agent-first, CLI-first SDLC orchestrator: model your system as a graph, define intents, and let a **Director → Worker** agent pair publish implementation attempts to an AgentHub DAG.

- **Primary users are agents and coordinators**: the system is built around automated execution and DAG-native promotion/shipping.
- **UI and human actions stay at the review/ship boundary**: workers produce validated candidates, operators choose winners, and only accepted/integrated attempts become shippable through promotion-candidate review and `ShipRun` execution.
- **Contract being frozen in Phase 1**: agent completes work, validation creates a candidate, a human may choose a winner, only explicit acceptance/integration advances the frontier, then operators review a stable promotion candidate, create a `ShipRun`, inspect it, and ship/merge at the final boundary.
- **One container per job**: reproducible, isolated runs.
- **Coordinator-friendly**: run the coordinator on the same machine as the app, or on a completely separate machine.

If you’ve ever wanted architecture-aware agent swarms with a clear human shipping boundary, this is it.

> **Alpha status:** Terarchitect is working, dogfooded alpha software. The core loop — GitHub import, AgentHub DAG attempts, human acceptance, Ship Room composition, and GitHub export — is real, but APIs, deployment defaults, and UX details may change quickly.

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
- **Intent-driven execution**: moving an intent/ticket to *In Progress* enqueues an agent job
- **Director/Worker separation**: strategy (Director) vs code-writing execution (Worker, with Codex preferred for implementation-heavy work)
- **AgentHub-native workflow**: agents publish attempts/leaves; validated candidates are reviewed, accepted/integrated `TicketAttempt`s become promotion candidates, and `ShipRun`s compose/release them
- **Runs anywhere Docker runs**: single-box dev or two-box production

---

## Technology (what’s under the hood)

- **Backend API**: Python 3.11 + Flask + SQLAlchemy
- **Database**: Postgres (with `pgvector/pgvector` image for vector search support)
- **Frontend**: Node 20 + React (served from Docker Compose)
- **Coordinator**: Python (host process) + `requests`
- **Agent image**: Python runner + **OpenCode** (server mode) + **Claude Code** (headless CLI) + **Codex** (autonomous coding) + Node 20 (for `npm test` in target repos) + Docker daemon (full DinD — each container has its own isolated daemon)

LLM endpoints are configured via env (Director via `DIRECTOR_LLM_URL`, Worker via `WORKER_LLM_URL`, etc., set in coordinator/agent env). See `example.env` and `docs/RUNBOOK.md`.

---

## Memory system (HippoRAG) — Optional

Terarchitect includes an **optional** file-backed project memory system built on **HippoRAG** (bundled as `backend/hipporag_minimal`). Memory is enabled when embedding/LLM env vars are configured; otherwise a no-op backend returns empty results.

**Memory endpoints** (locked per project, Auth: Bearer when `TERARCHITECT_WORKER_API_KEY` set):
- `POST /api/projects/<project_id>/memory/index` — body: `{"docs": ["text1", ...]}`
- `POST /api/projects/<project_id>/memory/retrieve` — body: `{"queries": ["q1", ...], "num_to_retrieve": 5}`
- `POST /api/projects/<project_id>/memory/delete` — body: `{"docs": ["exact text to remove", ...]}`

**Operational notes:**
- Memory is **optional** — tickets can move to In Progress and execute without embedding/memory configured.
- When disabled, memory endpoints return `{"enabled": false, ...}` with empty results (no 500s).
- To enable: set `MEMORY_EMBEDDING_MODEL`, `MEMORY_LLM_BASE_URL`, `MEMORY_LLM_MODEL`, and an embedding API key.
- Memory is stored under `MEMORY_SAVE_DIR` (default `/tmp/terarchitect`).
- HippoRAG uses your configured LLM + embedding service via HTTP (no heavyweight local ML dependencies in the backend).
- The backend also exposes an OpenAI-compatible embeddings adapter at `POST /v1/embeddings`.

Details: `backend/README.md` (Memory section).

---

## Worker modes + API integration

Terarchitect supports three worker backends, selectable via **WORKER_MODE** in the coordinator/agent environment (`codex`, `opencode`, or `claude-code`):

| Mode | How it works |
|------|-------------|
| **Codex** (default) | The Director keeps the orchestration lane and routes implementation-heavy code writing through `codex exec --json --sandbox workspace-write ...`, capturing a `thread_id` on the first turn and resuming the same Codex thread on follow-up turns. This is the preferred coding path for the research → planning → plan review → execution loop. Requires `WORKER_API_KEY` set to an OpenAI API key. |
| **OpenCode** | The agent entrypoint starts `opencode serve` (HTTP API). The Director sends prompts over HTTP (session create → message turns → summarize every 30 turns). Requires `WORKER_LLM_URL` pointing at an OpenAI-compatible LLM. |
| **Claude Code** | The Director invokes `claude -p "..."` (headless CLI) for each prompt. No LLM URL needed — just set `WORKER_API_KEY` to your Anthropic API key. |

At the app boundary, the coordinator/agent use a small “worker API” surface (Bearer-authenticated when `TERARCHITECT_WORKER_API_KEY` is set):

**Context + logs**
- `GET /api/projects/<project_id>/tickets/<ticket_id>/worker-context` (ticket/project context only)
- `POST /api/projects/<project_id>/tickets/<ticket_id>/logs` (append execution logs)
- `POST /api/projects/<project_id>/tickets/<ticket_id>/complete` (mark ticket complete)

**Queue**
- `POST /api/worker/jobs/start` (claim next job)
- `POST /api/worker/jobs/<job_id>/complete`
- `POST /api/worker/jobs/<job_id>/fail`

Details: `docs/PHASE1_WORKER_API.md`.

For explicit competing attempts, operators should see five pre-selected strategies: `minimal-patch`, `root-cause-debugger`, `test-first`, `refactor-forward`, and `systems-explorer`. Workers should receive attempt metadata such as `ATTEMPT_SLOT`, `ATTEMPT_INDEX`, `ATTEMPT_COUNT`, plus strategy metadata once sibling lanes finish wiring it end-to-end.

---

## Configurable workflows

Terarchitect supports per-project custom workflow definitions. A workflow file (JSON or YAML) defines the stages a ticket goes through: `worker_prompt`, `plan_review`, `execution`, and `finalize`, each with optional conditions controlling per-ticket execution.

```bash
# Create a project with a custom workflow
ta project create my-project --workflow-file .terarchitect/workflow.yaml

# Update an existing project
ta project update my-project --workflow-file .terarchitect/custom.yaml
```

If no `--workflow-file` is set, Terarchitect checks for `.terarchitect/workflow.yaml` or `.terarchitect/workflow.json` in the project root automatically. If neither is found, the built-in default 6-stage workflow is used.

Full reference: [`docs/workflow-definition.md`](docs/workflow-definition.md)

## System architecture (app + coordinator + agent)

| Component | What it does | Where it runs |
|-----------|--------------|---------------|
| **App** | Flask API + Postgres + React frontend. Stores projects/graph/tickets/logs and enqueues jobs. Does **not** execute the agent. | **Docker Compose** (`postgres`, `backend`, `frontend`) |
| **Coordinator** | Claims jobs from the API and starts one agent container per job. | **Docker Compose** or **host process** |
| **Agent image** | Director + Worker (OpenCode, Claude Code, or Codex). Materializes the selected AgentHub base leaf, implements the ticket, publishes an AgentHub attempt, exits. | **Docker container** started by the coordinator |

High-level flow: **GitHub URL/ref import → AgentHub DAG project → accepted frontier selects `base_leaf_id` → UI enqueue → coordinator claims → agent container materializes the base leaf → AgentHub attempt created → validated candidate → operator-chosen winner → accepted/integrated `TicketAttempt` → promotion candidate review → `ShipRun` compose/ship → shipped frontier advance**.

The UI is an operator surface, not the primary execution surface. Agents and coordinators do the work; humans review validated candidates, choose winners, integrate accepted work, and ship at the promotion boundary. The operator workflow is promotion-candidate review followed by `ShipRun` compose/ship.

Attempt inspection is already first-class: normal worker completions create `TicketAttempt` rows that can be listed, inspected, diffed, accepted, or rejected. Explicit competing attempts are a narrower opt-in rerun flow for one ticket from the same current frontier; they still materialize as ordinary `TicketAttempt`s rather than a separate review object. The real lifecycle is candidate validation -> winner choice -> accepted/integrated frontier advance -> promotion candidate -> `ShipRun`. See `docs/COMPETING_ATTEMPTS.md`.

---

## Quick start (local dev)

### 1) Start the app (API + DB + UI)

```bash
docker compose up -d
```

- **UI**: `http://localhost:3000`
- **API**: `http://localhost:5010`
- **Postgres**: host port `5433` 

### 2) Build the agent and coordinator images (once)

```bash
docker compose build agent coordinator
```

### 3) Run the coordinator (so tickets actually execute)

Docker-first path:

```bash
docker compose up -d coordinator
```

The compose coordinator launches sibling worker containers onto `terarchitect_default` and forwards `TERARCHITECT_API_URL=http://backend:5010`, `AGENTHUB_URL=http://agenthub:8080`, `AGENT_IMAGE=terarchitect-agent`, and the Director/Worker env you provide via shell or repo `.env`. Worker containers materialize AgentHub workspaces per job; they do not bind-mount your host repo.

Host-run path remains available for local-mode projects or for deployments that keep the coordinator outside Compose:

```bash
make setup-venv
TERARCHITECT_API_URL=http://localhost:5010 \
PROJECT_ID=<your-project-uuid> \
GITHUB_TOKEN=<token> \
TERARCHITECT_WORKER_API_KEY=<optional-worker-api-key> \
make python ARGS='-m coordinator'
```

Always use the repo-local `.venv` (`make python`, `make pip`, `make pytest`, or `.venv/bin/python`) for host-run Python. Do not install Terarchitect requirements into a shared/Hermes virtualenv.

**Concurrency note:** the coordinator defaults to `MAX_CONCURRENT_AGENTS=1` but parallel runs are now safe. This is a global cap across all workers. Same-ticket competing attempts may run concurrently inside that cap, while unrelated ticket graph conflicts still block as usual. Each agent container runs its own isolated Docker daemon (DinD via `--privileged`), so concurrent jobs never conflict on container names, networks, or ports. Increase `MAX_CONCURRENT_AGENTS` to run multiple jobs in parallel; see the TODO section for tuning guidance.

---

## Deployments that scale

### Single-box (dev / small deploy)

- Run the app: `docker compose up -d`
- Run the coordinator in compose (`docker compose up -d coordinator`) or on the same host
- Compose coordinator default: `TERARCHITECT_API_URL=http://backend:5010`, `AGENTHUB_URL=http://agenthub:8080`, `DOCKER_NETWORK=terarchitect_default`
- Host coordinator: set `TERARCHITECT_API_URL=http://host.docker.internal:5010` so agent containers can reach the app
  - On Linux, the coordinator automatically adds `--add-host=host.docker.internal:host-gateway`

### Two-box (production)

- **Machine A**: app only (docker compose). No coordinator required here.
- **Machine B**: coordinator + Docker. Build the agent image here. Run the coordinator here (host process or coordinator container).
- Set `TERARCHITECT_API_URL=https://machine-a.example.com` (or the public URL of Machine A)

Agent containers only need:
- network access to the app (worker-context, logs, complete/fail)
- network access to GitHub when cloning from GitHub or creating a release/export PR
- network access to AgentHub when publishing/fetching attempts

They do **not** need direct DB access.

Full ops notes (systemd, env, verification): see `docs/RUNBOOK.md`.

---

## How execution works

### GitHub-first onboarding + AgentHub DAG runtime

Normal execution is **GitHub-first** and **DAG-first**:

1. Import a project from a **GitHub repo URL and ref**. AgentHub ingests that repo state into the project DAG.
2. The project stores the imported DAG state and tracks the current **accepted frontier**.
3. When a ticket is queued, Terarchitect records the selected DAG parent as the ticket/job `base_leaf_id` (and compatible `base_hash`).
4. Moving a ticket to **In Progress** enqueues a job.
5. The coordinator claims the job and runs:
   - `docker run ... -e REPO_URL=... -e TICKET_ID=... terarchitect-agent`
6. Inside the container, the agent:
   - imports or fetches the GitHub source needed for the requested ref/base
   - materializes the selected AgentHub `base_leaf_id` into an isolated worker workspace
   - runs Director + Worker (OpenCode, Claude Code, or Codex) to implement
   - publishes a child attempt/leaf back to AgentHub
   - exits
7. Terarchitect records the attempt as a `TicketAttempt`.
8. Validation makes each attempt a reviewable candidate. Operators may choose a winner and deliberately leave it unintegrated.
9. Only explicit acceptance/integration advances the project's **accepted frontier**, which becomes the source of truth for later tickets and shipping.
10. The Ship Room flow is: review a stable promotion candidate built from accepted/integrated attempts whose dependency closure is valid, create a `ShipRun` from that candidate set, then advance the shipped frontier when shipped.

If one ticket needs deliberate alternatives, explicit competing attempts rerun that same ticket from the current accepted frontier and create multiple sibling `TicketAttempt`s for later comparison. The product default is `3` attempts per ticket, with rerun overrides available when you want fewer or more. That does not change the promotion model: operators inspect the sibling candidates, choose one winner, optionally leave it unintegrated, and only unblock downstream work after explicit acceptance/integration. See `docs/COMPETING_ATTEMPTS.md`.

Local project paths still exist only as a **legacy import/debug path**:

- use them to seed/import a repo when GitHub is unavailable
- use them for local execution-mode debugging on a host
- do not treat a host checkout or branch-sync workflow as the runtime source of truth for normal swarm execution

For normal Docker/GitHub-first runs, the worker runtime source of truth is the **AgentHub DAG**, not a persistent local branch checkout.

Ticket-level PR review is not part of the swarm-mode MVP path. The human review point is attempt acceptance, and the shipping object is a candidate-backed `ShipRun`.

No mixing with your project’s Dockerfile. The agent image is built once and reused.

---

## Repo layout

| Path | Role |
|------|------|
| `backend/` | Flask API (served by docker compose). Stores graph/tickets/logs; enqueues jobs only. |
| `frontend/` | React UI (served by docker compose). |
| `coordinator/` | Docker/host coordinator. Claims jobs and starts agent containers. |
| `agent/` | Director + runner + worker wiring (Codex, OpenCode, and Claude Code). Packaged into the agent image. |

---

## Docs

- `CLI_GUIDE.md`: shared CLI conventions for output, errors, receipts, and adding commands
- `docs/RUNBOOK.md`: deployments, coordinator env, systemd, verification
- `docs/PHASE1_WORKER_API.md`: worker API contract and behavior
- `plans/`: product and architecture planning notes

---

## Highlights

- **Ship Room**: review accepted/integrated AgentHub attempts as future promotion candidates, compose them into a `ShipRun`, and advance the shipped frontier.
- **Promotion-boundary review**: validate candidates first, choose a winner, integrate it when ready, then inspect one `ShipRun` created from a stable candidate set before the final ship/merge step.
- **Cancelable runs**: worker-facing cancel flag + polling endpoint so you can stop a run cleanly.
- **Per-project execution mode**: run jobs in Docker (clone in container) or Local (run at a configured host path).
- **Env-only config**: each service (backend, coordinator) uses a simple `.env` that fits its needs; no shared settings store. See `.env.example`.
- **Vector search + safety**: pgvector-backed embeddings with an ORM-safe approach (avoids accidentally selecting vector columns).
- **Operator-friendly debugging**: scripts for requeueing tickets, dumping logs/memory, and smoke-testing OpenCode server/CLI.

---

## Alpha limitations

Terarchitect is ready for builders who are comfortable operating alpha infrastructure, not for unreviewed production automation.

- Public APIs and UI flows may change between alpha releases.
- GitHub App integration and hardened secret management are still future work; today, deployments are environment/token driven.
- Multi-tenant SaaS isolation is intentionally out of scope for now. Use single-tenant deployments when working with private/customer repositories.
- Worker containers may require Docker privileges depending on execution mode.
- Some recovery paths are operator/runbook driven instead of fully polished UI flows.

---

## Managed single-tenant installs

The preferred commercial path is **open source + managed single-tenant installs**, not a shared multi-tenant SaaS on day one.

For teams that want Terarchitect without operating the stack themselves, the practical deployment model is:

- one isolated Terarchitect app per customer/team
- one Postgres database
- one AgentHub instance or namespace
- one worker/coordinator runtime
- repo-scoped GitHub credentials
- customer-specific backups, monitoring, and support

This keeps source code, AgentHub DAG state, logs, and credentials isolated while the project matures.

---

## TODO / Roadmap

- **Raise `MAX_CONCURRENT_AGENTS`**: DinD is now the default (each agent container runs its own isolated `dockerd` via `--privileged`), so `docker compose` collisions no longer occur. Increase `MAX_CONCURRENT_AGENTS` to run multiple tickets in parallel. Monitor host resource usage (RAM, CPU) and tune accordingly.

---

## Contributing

PRs welcome. Keep changes focused and verifiable (tests where possible). If you’re shipping a behavior change, include a short “why” in the PR description.
