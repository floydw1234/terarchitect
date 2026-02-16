# Docker Agent Transition Plan

This document describes the migration from the current in-process agent (Director + worker running inside the Flask app) to a **modular execution layer**: the agent (Director + worker) runs in **ephemeral Docker containers**, one per job. The Terarchitect app (API + DB + frontend) stays separate and only enqueues work and exposes APIs for the agent to consume.

---

## Goals

- **Separate app from execution**: Backend + DB + frontend do not run the agent or host the LLM. They are the source of truth and job coordinator only.
- **Ephemeral agent containers**: One container per job (one ticket or one PR review). Container clones the repo, runs Director + worker, pushes PR, reports back, exits. No persistent host `project_path`.
- **Multiple agent flavors**: Same Director logic; different worker implementations. **One agent Docker image** with multiple coding agents installed. **Only CLI-callable workers** are supported: each worker must be invokable from the command line (or subprocess) with a prompt and return output—like OpenCode—so the Director can drive it headlessly in a container. No IDE-only or interactive-approval-only tools. **Env variables** (`AGENT_WORKER` plus worker-specific keys) determine which worker runs. One image to build and ship; coordinator always uses the same image and passes different env per job.
- **Concurrency**: Multiple jobs = multiple containers. Orchestrator/coordinator starts containers and tracks completion; no single global lock.
- **Configurable topology**: App (web + DB + frontend) can run on one computer and only enqueue work; agent containers run on a **different computer** where a **coordinator** pulls from the queue and allocates agents based on that machine’s **headroom** (e.g. max N containers or CPU/RAM/GPU).

---

## Current State (Pre-Migration)

| Component | Location | Role |
|-----------|----------|------|
| Director + worker loop | `backend/middle_agent/agent.py` | Imported by Flask; runs in a background thread. **Ticket flow:** research → planning (worker creates `plan/<ticket_id>_task_plan.md`) → plan-review loop (agent judges plan; `plan_approved` / `approved_plan_text`) → agent context reset → execution (plan always injected; assess until complete). Worker session is continuous across all phases; only the Director side resets before execution. **Commits:** one per execution step; commit message is LLM-generated from the diff (imperative), not "Agent: step N". **After complete:** one more worker turn produces a test-summary JSON; then finalize (commit, push, PR create, post PR comment with test-summary in `<!-- terarchitect-tests -->` for the UI). |
| Worker abstraction | `backend/middle_agent/agent.py` | `_send_to_worker()` dispatches by `WORKER_TYPE` (env/settings) to worker implementation (e.g. OpenCode). Worker type and CLI are derived from `WORKER_TYPE` (opencode, aider, claude_code, gemini, codex); only opencode is implemented. Enables multiple workers (Phase 5) via same interface. |
| Trigger | `backend/api/routes.py` | Ticket → In Progress enqueues `("ticket", (ticket_id,))`. |
| Runner | Same process | `_run_agent_and_poll_loop` pops queue and runs `agent.process_ticket(ticket_id)` under `_agent_run_lock` (one job at a time). |
| Repo | Host filesystem | `Project.project_path`; agent runs worker subprocess with `cwd=project_path`. `_read_task_plan(project_path, ticket_id)` reads `plan/<ticket_id>_task_plan.md` (ticket_id required); used for plan-review and execution. |
| Context / logs / DB | Direct | `_load_context(ticket)` uses `Project`, `Graph`, `Note`, `Ticket` via SQLAlchemy. `_log()` writes to `ExecutionLog`. `_finalize()` updates `Ticket`, `PR`, `db.session.commit()`. |
| Memory (HippoRAG) | App | `get_hipporag_kwargs()`, `retrieve()`, `index()` from `utils.memory`; agent uses them for RAG passages. Memory LLM/embedding default to Agent URL/model; `MEMORY_SAVE_DIR` is fixed at `/tmp/terarchitect` (not configurable via UI). |
| Prompts | `middle_agent/prompts.json` | Required: `agent_system_prompt`, `worker_first_prompt_prefix`, `worker_review_prompt_prefix`. Optional (planning): `worker_research_prompt_prefix`, `worker_plan_prompt_prefix`, `agent_plan_review_instructions`. Optional: `worker_test_summary_prompt` (used after execution to produce test-summary JSON for the PR comment). |
| Config / secrets | App DB + env | Settings stored in app DB (`AppSetting`); sensitive keys encrypted with `TERARCHITECT_SECRET_KEY`. Agent/worker use `get_setting_or_env()` for VLLM_URL, AGENT_MODEL, AGENT_API_KEY, WORKER_TYPE, WORKER_LLM_URL (default http://localhost:8080/v1), WORKER_MODEL, WORKER_API_KEY, memory/embedding URLs and keys. No VLLM_PROXY_URL, AGENT_API_URL, or WORKER_PROVIDER_ID (removed). GitHub: `github_user_token` (UI), `github_agent_token` (agent). |

---

## Target State (Post-Migration)

| Component | Location | Role |
|-----------|----------|------|
| Terarchitect app | Same repo; deploy separately | API + DB + frontend. Enqueues jobs. Exposes **worker-facing API** (context, logs, complete). No agent code in the app process. |
| Orchestrator | Part of app or small sidecar | When ticket moves to In Progress (or review job enqueued), starts agent container with env. When container exits, marks job done. Can run N containers in parallel. |
| Agent image | **One** Docker image (all five workers installed) | Director (Python) + all five workers (Claude Code, Gemini, Codex, OpenCode, Aider) + git. Entrypoint reads `AGENT_WORKER` (and worker-specific env) → fetch context from API → clone repo → run Director loop with the selected worker → push PR → call API to complete → exit. |
| Repo | Inside container | Clone from `REPO_URL` at job start into `/workspace` (or similar). No host path. |
| Context / logs / completion | HTTP only | Agent container calls Terarchitect API: GET context, POST logs, POST complete (update ticket, PR URL). No DB connection from container. |
| Memory | API | App exposes memory retrieve/index endpoints; agent container calls them instead of using HippoRAG directly. |

---

## Distributed Deployment: App Machine vs Agent Machine

The design is **configurable** so the web app (DB + frontend) can run on **one computer** and agent containers on **another**. The app never starts Docker; it only enqueues work. A **coordinator** on the agent machine pulls from the queue and starts containers based on that machine’s **headroom**.

### Topology

```
┌─────────────────────────────────────────────────────────────────┐
│  APP MACHINE (Computer A)                                        │
│  • Web app (API) + Database + Frontend                           │
│  • On "ticket → In Progress": writes job to QUEUE (no Docker)     │
│  • Exposes: API (context, logs, complete), queue (see below)      │
│  • Config: AGENT_QUEUE_MODE=remote (never start agents locally)  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                    Queue in app DB; claim via POST /api/worker/jobs/start (scope: project_id)
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│  AGENT MACHINE (Computer B)                                      │
│  • COORDINATOR process: poll queue → check headroom → run agents │
│  • Docker: one agent image (env selects worker: Claude Code, Gemini, Codex, OpenCode, Aider) │
│  • Containers need: network to App (API URL), GitHub, LLM        │
└─────────────────────────────────────────────────────────────────┘
```

### Queue: lives on the web app (HTTP), scoped by project_id only

The **queue lives on the web app**. Job state is stored in the app DB in an `agent_jobs` table. **Scope is by `project_id` only**: project IDs are globally unique, so a coordinator that passes a single `project_id` when claiming work will only receive jobs for that project. No separate client_id is required. Multiple clients can self-host agents by running a coordinator configured with the **project_id(s)** they own; the queue only returns jobs for those projects.

- **App (enqueue):** When a ticket moves to In Progress (or a review job is created), the app inserts a job with status `pending` and the job’s `project_id` (from the ticket’s project). Do not enqueue a second job for the same ticket if that ticket already has a job in `pending` or `running` (per-ticket deduplication).
- **Coordinator (claim/start):** Calls **`POST /api/worker/jobs/start`** with **`project_id`** in the request body (e.g. `{"project_id": "<uuid>"}`). App selects one pending job for that project_id, sets status to `running`, returns it; if none, 204 No Content. Response (200): (or “claimed”) Response: `job_id`, `ticket_id`, `project_id`, `kind`, `repo_url`, and for review jobs `pr_number`, `comment_body`, etc. Request body must include `project_id` only (no client_id). For `kind=review`, response also includes `pr_number`, `comment_body`, `github_comment_id`.
- **Coordinator (done/fail):** When the container exits, coordinator calls e.g. **`POST /api/worker/jobs/<job_id>/complete`** or **`/fail`** so the app can update job state and, if the container didn’t already call the ticket complete endpoint, update the ticket as defined in the API contract (e.g. leave in progress or mark failed).

No Redis or separate queue service. Coordinator only needs **app API URL** and auth (e.g. API key or Bearer token). Coordinators that serve multiple projects call start once per project_id (e.g. in rotation).

### Coordinator / Scheduler (on the worker machine)

A **coordinator** (scheduler) runs on the machine where agent containers will execute. It pulls jobs from the queue and starts containers **only when this machine has resources available**. It can be packaged as a **scheduler Docker image** that loops: check system usage → if headroom, claim one job → run agent container → on exit, mark job done and repeat.

**Critical: check headroom before claiming.** Never call `POST /api/worker/jobs/start` unless this machine has capacity to run a container. If the scheduler claimed first and then checked resources, it would take the job off the queue even when it cannot run it—other worker machines would never see that job. Order: (1) check headroom, (2) if none, sleep and retry; (3) only if headroom available, call start to claim; (4) if 204, no work; (5) if 200, start container; (6) on container exit, call complete/fail and loop to (1).

1. **Check headroom (before claiming)** – Decide if this machine can run one more agent container:
   - **Simple:** “Max N concurrent agent containers.” Count currently running containers (or jobs in `running` state); if count < N, allow one more.
   - **Richer:** Use CPU, RAM, or GPU (e.g. `nvidia-smi`, `docker stats`, or `/proc`). Only start a new container if e.g. free RAM > threshold or GPU memory available. Config: `MAX_CONCURRENT_AGENTS`, optional `MIN_FREE_RAM_MB`, `MAX_GPU_UTILIZATION`, etc.
2. **Claim a job only when headroom OK** – Call **`POST /api/worker/jobs/start`** with body **`{"project_id": "<uuid>"}`**. App atomically selects one pending job, sets status to `running`, returns it; if no pending job, 204. Do not call start until headroom is available so the job is never removed from the queue while this machine cannot run it.
3. **Start an agent container** – If 200, `docker run` (or Docker SDK) with the agent image; env from the job (`TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`, `GITHUB_TOKEN`, `AGENT_WORKER`, etc.). Container gets context from the app via API.
4. **Track run and mark done** – When the container exits, coordinator calls **`POST /api/worker/jobs/<job_id>/complete`** or **`/fail`**. Then loop back to (1).

Coordinator config (env or file) on the agent machine typically includes:

- **App API URL:** Base URL of the Terarchitect app. Used to claim jobs and as `TERARCHITECT_API_URL` for containers. Must be reachable from the agent machine and from inside containers.
- **Scope (project_id only):** **`PROJECT_ID`** — the coordinator passes this when calling `POST /api/worker/jobs/start`. The queue returns only pending jobs for that project. For multiple projects, the coordinator is configured with a list of project IDs and polls for each (or one coordinator per project).
- **Headroom:** `MAX_CONCURRENT_AGENTS`, optional resource thresholds.
- **Agent image:** One image (e.g. `terarchitect-agent`); env `AGENT_WORKER` (and worker-specific keys) selects which of the five workers runs. Coordinator passes the same image and sets env per job (e.g. `AGENT_WORKER=opencode`, plus GITHUB_TOKEN and that worker’s API key/URL).

### Configurability (app side)

- **Single-box (dev / small deploy):** App and coordinator run on the same host; app enqueues (writes to queue in DB), coordinator polls `POST /api/worker/jobs/start` and runs containers.
- **Two-box (production):** App machine has no Docker, no coordinator; queue lives in the app DB. Only the agent machine runs the coordinator and Docker. Coordinator is configured with the app’s API URL and uses the same HTTP endpoint to claim jobs.

**Multi-client / self-hosted agents:** When you have multiple clients, each can host their own agents. Their coordinator is configured with the **project_id(s)** for the projects they own. Because the queue is scoped by project_id only, they only ever receive jobs for those projects. The app remains the single source of truth; only who runs the coordinator and which project_id(s) they pass changes.

---

## Migration Phases

**Flow summary:** Phase 1 adds the HTTP API (context, logs, complete, memory, cancel + queue) so the agent and coordinator never touch the DB. Phase 2 extracts the Director + worker into a standalone runner that uses that API and clones the repo locally. Phase 3 packages the runner in a single Docker image (one worker wired; all five worker CLIs installed). Phase 4 switches the app to enqueue-only and adds the coordinator that claims jobs and starts containers. Phase 5 wires the remaining four workers into the same image. Phase 6 cleans up the old in-process path and documents deployment.

**Note:** The current agent already implements a planning-stage ticket flow (research → planning → plan-review → execution) as described in `tasks.txt`. This plan assumes that flow is preserved through all migration phases; the standalone runner and Docker image run the same phases with a cloned repo. The plan file is **`plan/<ticket_id>_task_plan.md`** (not root `task_plan.md`); `_read_task_plan(project_path, ticket_id)` requires `ticket_id`. After execution completes, the agent runs one more worker turn to produce a test-summary JSON, then finalizes (commit, push, PR create, post test-summary as a PR comment for the UI).

### Overview: phase order and coherence

Implement in this order so each phase has what it needs and can be tested before the next.

| Phase | What you do | Depends on | Test before next |
|-------|----------------|------------|-------------------|
| **1** | Add worker-facing HTTP API (context, logs, complete, memory, cancel) and queue (table `agent_jobs` + `jobs/start`, `jobs/complete`, `jobs/fail`). App **does not** enqueue or stop in-process agent yet. | Nothing | Script that calls new endpoints and (e.g. manual INSERT) claims a job. |
| **2** | Introduce `AgentBackend`; add `HttpAgentBackend` and standalone runner. Runner uses Phase 1 API only; clones repo, runs Director + one worker (OpenCode), pushes, calls complete. No Flask/DB in runner. | Phase 1 | Run one ticket from CLI with env; logs and completion visible in app. |
| **3** | Build single agent Docker image: Director + runner + all five worker CLIs; only OpenCode adapter wired. Entrypoint: read env → fetch context → clone → run Director → complete. | Phase 2 | `docker run` with OpenCode env; one ticket runs in container. |
| **4** | App: on “In Progress” (and review) **write to `agent_jobs`** and **stop** running the agent in-process. Add scheduler: check headroom → claim via Phase 1 API → start Phase 3 image → on exit, complete/fail. | Phase 1, Phase 3 | Move ticket to In Progress; job enqueued; scheduler claims and runs container; UI shows logs and PR. |
| **5** | In the **same** agent image, add adapters for Claude Code, Gemini, Codex, Aider. `AGENT_WORKER` selects which worker runs. No new image. | Phase 2 (pluggable worker), Phase 3 | Run jobs with different `AGENT_WORKER` values. |
| **6** | Remove or gate in-process agent path; document app, scheduler, and agent image deployment. | Phase 4 (or 5) stable | Runbook and README; no in-process agent in production. |

**Dependency chain:** 1 → 2 → 3 → 4 (must be in order). Phase 5 can be done after Phase 3 (before or after Phase 4). Phase 6 last.

**Coherence:** Phase 1 defines the API and queue contract so Phase 2 (runner) and Phase 4 (scheduler) never touch the DB. Phase 2 refactors the agent to use a backend abstraction so the same Director code runs in-process (Flask backend) or in Docker (HTTP backend). Phase 3 produces the artifact (image) that Phase 4 runs. Phase 4 is the only place the app stops running the agent and starts enqueueing; the scheduler uses Phase 1 queue API and Phase 3 image. Phase 5 only adds worker adapters inside the existing image.

---

### Phase 1: Worker-facing API (app side)

**Goal:** The app can serve an agent that has no DB access. All data the agent needs comes from HTTP. This phase defines the full API surface that **Phase 2** (standalone runner) and **Phase 4** (coordinator) will use.

1. **Context endpoint**
   - Add `GET /api/projects/<project_id>/tickets/<ticket_id>/worker-context` (or similar).
   - Returns the same structure as `_load_context(ticket)` today: project name/description, `github_url`, current ticket, graph (full + relevant slice), notes, backlog/in_progress/done ticket summaries.
   - Do **not** include `project_path` in the response; the agent will use `REPO_URL` and clone path inside the container. Optionally include `repo_url` derived from `project.github_url` for convenience.
   - Auth: API key or existing auth; document for agent image.

2. **Log endpoint**
   - Ensure `POST /api/projects/<project_id>/tickets/<ticket_id>/logs` exists (or add it) so the agent can append execution log entries (step, summary, raw_output). Match current `ExecutionLog` shape.

3. **Complete / finalize endpoint**
   - Add `POST /api/projects/<project_id>/tickets/<ticket_id>/complete` (or similar).
   - Body: `pr_url`, `pr_number`, `summary` (completion summary), optional `review_comment_body` for review jobs.
   - Server creates/updates `PR` record, sets ticket `column_id` to `in_review`, updates `status`, commits. No git or `gh` on the app; the agent does that inside the container.

4. **Memory endpoints**
   - Add (or expose) endpoints for:
     - **Retrieve**: e.g. `POST /api/projects/<project_id>/memory/retrieve` with `queries` (and optional ticket/session scope). Returns passages the agent can use in the Director prompt.
     - **Index**: e.g. `POST /api/projects/<project_id>/memory/index` with completion summary so project memory stays updated after a ticket is done.
   - Implementation can delegate to existing `utils.memory` (HippoRAG) inside the app.

5. **Cancel**
   - Current cancel is in-process (`_active_sessions[ticket_id]["cancel"]`). For containers, either:
     - Agent polls `GET /api/projects/.../tickets/.../cancel-requested` and exits if true; app sets this when user clicks “Stop agent”, or
     - Orchestrator sends `docker stop` and the container exits; then app marks job as cancelled. Document chosen behavior.

6. **Queue: simple HTTP “start job”**
   - Queue lives on the web app (DB: table `agent_jobs` with at least `id`, `ticket_id`, `project_id`, `kind`, `status`, `created_at`; for review jobs add `pr_number`, `comment_body`, `github_comment_id`). When a ticket moves to In Progress (or review is enqueued), app inserts a row with `status=pending` and the job’s `project_id` . Do not enqueue if that ticket already has a job in `pending` or `running`.
   - Add **`POST /api/worker/jobs/start`**. Request body **must** include **`project_id`** only (e.g. `{"project_id": "<uuid>"}`). App selects one pending job for that project_id, sets status to `running`, returns it; if none, 204 No Content. Response (200): `job_id`, `ticket_id`, `project_id`, `kind`, `repo_url` (from project.github_url), and for `kind=review`: `pr_number`, `comment_body`, `github_comment_id`. Auth required (e.g. Bearer token or API key header); document for coordinator and agent.
   - Add **`POST /api/worker/jobs/<job_id>/complete`** and **`POST /api/worker/jobs/<job_id>/fail`** so the coordinator can mark the job done when the container exits (success or failure). App updates job state; if the container already called the ticket complete endpoint, this is just cleanup.

**Deliverable:** (1) **Agent endpoints** (context, logs, complete, memory, cancel) so a standalone client can run a full ticket without DB access. (2) **Queue** table and **queue endpoints** (jobs/start, jobs/complete, jobs/fail) so a coordinator can claim and complete jobs. The app does not enqueue on In Progress until Phase 4; test Phase 1 with a script and e.g. manual INSERT into `agent_jobs`. Document auth and contract.

---

### Phase 2: Extract agent into a standalone runnable

**Goal:** The same Director + worker logic can be run from the command line with env only (no Flask, no DB). It talks to the app only via the **agent endpoints** from Phase 1 (context, logs, complete, memory). The runner is invoked with job params in env (e.g. `TICKET_ID`, `PROJECT_ID`, `REPO_URL`); it does not use the queue—Phase 4 will run this runner inside containers and pass env from the job.

**Current agent flow (preserve as-is):** Research → planning (worker creates `task_plan.md`) → plan-review loop → agent context reset → execution (plan injected; assess until complete). Worker session is continuous; `_read_task_plan(project_path)` reads from the cloned repo. The runner must clone the repo and pass `project_path` so this flow works unchanged.

1. **Agent client / runner module**
   - New package or script (e.g. `agent_runner` or `middle_agent/standalone.py`) that:
     - Reads env: `TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`, `GITHUB_TOKEN` (agent token), and LLM/worker config: `VLLM_URL`, `AGENT_MODEL`, `AGENT_API_KEY`, `WORKER_TYPE` (or `AGENT_WORKER`), `WORKER_LLM_URL`, `WORKER_MODEL`, `WORKER_API_KEY`, plus memory/embedding URLs and keys if needed. (No `AGENT_API_URL`, `VLLM_PROXY_URL`, or `WORKER_PROVIDER_ID`; agent URL is `{VLLM_URL}/v1/chat/completions`, worker URL defaults to http://localhost:8080/v1.)
     - Clones repo into a local path and passes it as `project_path` so the full flow and `_read_task_plan(project_path, ticket_id)` work as today (plan file: `plan/<ticket_id>_task_plan.md`).
     - Calls `GET .../worker-context` to get context (replacing `_load_context`).
     - Calls memory retrieve/index via API (replacing direct `utils.memory` and `current_app.config`).
     - Writes logs via `POST .../logs` (replacing `_log` → ExecutionLog).
     - On completion, runs test-summary worker turn, then git commit/push, `gh pr create`, posts test-summary PR comment (`<!-- terarchitect-tests -->`), and calls `POST .../complete` (replacing `_finalize` DB/PR updates). Git and `gh` stay in this runner, not in the app.
   - Keep the existing Director and worker **logic** (research/plan/plan-review/execution phases, _send_to_worker, assess, _read_task_plan, _generate_commit_message, test-summary turn) in `middle_agent/agent.py`, but:
     - Replace every DB/app dependency with an HTTP call or an injected “client” that the runner provides (e.g. `context_provider`, `log_sink`, `memory_client`, `complete_callback`). This may mean introducing an abstraction (e.g. `AgentEnv` or `AgentBackend`) that has two implementations: “Flask/DB” (current) and “HTTP” (for standalone/Docker).

2. **Refactor MiddleAgent to use an abstract backend**
   - `MiddleAgent(backend: AgentBackend)` where `AgentBackend` provides:
     - `get_context(ticket_id) -> dict`
     - `log(project_id, ticket_id, session_id, step, summary, raw_output=None)`
     - `retrieve_memory(project_id, queries, ...) -> list[str]`
     - `index_memory(project_id, doc, ...)`
     - `complete(ticket_id, pr_url, pr_number, summary, ...)`
   - Current in-process code uses `FlaskAgentBackend` (wraps DB + `current_app` + `utils.memory`).
   - Standalone/Docker uses `HttpAgentBackend` (calls the worker-facing API). Runner script builds `HttpAgentBackend(TERARCHITECT_API_URL, ...)` and passes it to `MiddleAgent`.

3. **Repo handling in the runner**
   - Runner (standalone script) is responsible for:
     - Cloning repo from `REPO_URL` into a local path (e.g. `./workspace` or `/workspace` in Docker).
     - Creating/checking out branch `ticket-{ticket_id}`.
     - Passing that path as `project_path` into the existing agent flow so `_ensure_ticket_branch`, `_send_to_worker`, `_read_task_plan(project_path, ticket_id)` (for `plan/<ticket_id>_task_plan.md`), and `_finalize` git/PR steps all run in that directory.
   - In Docker, clone happens inside the container; no host path. The planning phase writes `plan/<ticket_id>_task_plan.md` into this clone; execution phase reads it via `_read_task_plan` (or uses approved_plan_text from the agent).

4. **No Flask in the runner**
   - Runner must not import `flask` or `models.db`. All context, logging, memory, and completion go through the backend abstraction (HTTP in Docker). Optional: keep the in-process runner (Flask backend) for local dev so you can still run “one ticket” from the Flask app during migration.

**Deliverable:** A CLI entrypoint (e.g. `python -m agent_runner ticket --ticket-id=...`) that runs one ticket to completion using only env and the worker-facing API, with repo cloned in a local directory. **Phase 3** will package this runner in a Docker image.

---

### Phase 3: Single agent Docker image (one worker wired; all five CLIs in image)

**Goal:** **One** Docker image that contains the Director and the standalone runner from Phase 2, plus **all five** worker CLIs installed (Claude Code, Gemini, Codex, OpenCode, Aider). In this phase, **only one worker** (e.g. OpenCode) is wired in the runner—i.e. the entrypoint supports `AGENT_WORKER=opencode` and invokes that CLI. **Phase 5** will add the other four worker adapters so `AGENT_WORKER` can be any of the five. One image to build, tag, and ship.

1. **Dockerfile.agent** (single image)
   - Base: Python image (same version as backend).
   - Install: Director deps (`requests`, `tiktoken`; no Flask/SQLAlchemy for HTTP-only runner). **Workers**: only include tools that are **CLI-callable** (invokable from command line with a prompt, returning output)—e.g. OpenCode CLI, Claude Code CLI, Gemini CLI, Codex CLI, Aider CLI. Each must support being driven by the Director in a headless container (no interactive prompts). Git, `gh` CLI.
   - Copy: `middle_agent/`, `agent_runner/` (or equivalent), prompts, feedback_example.
   - Env: **`AGENT_WORKER`** or **`WORKER_TYPE`** (e.g. `opencode`, `claudecode`, `gemini`, `codex`, `aider`). Plus common: `TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`, `GITHUB_TOKEN` (agent token). Plus Director: `VLLM_URL`, `AGENT_MODEL`, `AGENT_API_KEY`. Plus worker: `WORKER_LLM_URL` (default http://localhost:8080/v1), `WORKER_MODEL`, `WORKER_API_KEY`. No `AGENT_API_URL`, `VLLM_PROXY_URL`, or `WORKER_PROVIDER_ID`. Document which env each worker needs.
   - Entrypoint: run the standalone runner; it reads `AGENT_WORKER`/`WORKER_TYPE`, selects the correct worker adapter (this phase: only OpenCode adapter implemented), fetches context from API, clones repo, runs Director loop with that worker (including test-summary turn and PR comment), pushes, calls complete, exits.

2. **Clone and branch inside container**
   - Same as before: `git clone $REPO_URL /workspace`, checkout `ticket-${TICKET_ID}`, run agent with `project_path=/workspace`. Use `GITHUB_TOKEN` for private repos. The planning phase writes `plan/<ticket_id>_task_plan.md` into this clone; the agent reads it via `_read_task_plan(project_path, ticket_id)` for plan-review and execution.

3. **Push and PR**
   - Runner/agent does git add/commit/push (per-step commits use LLM-generated messages), `gh pr create`, posts test-summary as a PR comment (`<!-- terarchitect-tests -->` + JSON), then `POST .../complete`. `gh` authenticated via `GITHUB_TOKEN` (agent token).

4. **Testing**
   - Run the image with `AGENT_WORKER=opencode` (and OpenCode env); confirm context, logs, and completion work. Other `AGENT_WORKER` values will work after Phase 5.

5. **Repos that use Docker**
   - Use **Option C** (see **Running Docker from the agent container**): the agent image does **not** include the Docker CLI; the coordinator does not mount the Docker socket. Unit tests are sufficient; the agent only edits code. Running the repo’s Docker is left to the user or CI.

**Deliverable:** One image (e.g. `terarchitect-agent`) runnable as `docker run -e AGENT_WORKER=opencode -e TICKET_ID=... -e PROJECT_ID=... -e TERARCHITECT_API_URL=... -e REPO_URL=... -e GITHUB_TOKEN=... ... terarchitect-agent`. **Phase 4** will use this image; **Phase 5** will wire the remaining four workers into the same image.

---

### Phase 4: Queue + coordinator (start containers)

**Goal:** When a ticket is moved to In Progress, the app enqueues a job (no in-process agent). A **coordinator** (on the same host or on a separate agent machine) uses the **queue API from Phase 1** to claim jobs and starts containers using the **agent image from Phase 3**. The coordinator passes the job response as env to the container (`TICKET_ID`, `PROJECT_ID`, `REPO_URL`, etc.). When a container exits, the job is marked done. See **Distributed Deployment** above for the two-machine (coordinator + headroom) design.

1. **App: enqueue only; coordinator uses Phase 1 queue API**
   - In `routes.py`, keep “enqueue on In Progress” behavior, but **do not** run the agent in-process. This is when the app **starts** writing to the queue (Phase 1 only added the table and endpoints). Insert a row into `agent_jobs` with `status=pending` and **remove** the in-process agent (no background thread). App never starts Docker; it only enqueues.
   - The coordinator claims work via **`POST /api/worker/jobs/start`** with body **`{"project_id": "<uuid>"}`**: app returns one pending job **for that project** and marks it `running`. Response includes `job_id`, `ticket_id`, `project_id`, `kind`, `repo_url`, and for review jobs the extra fields. Coordinator passes these as env to the container (e.g. `TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`, `GITHUB_TOKEN`, `AGENT_WORKER`). When the container exits, coordinator calls `POST /api/worker/jobs/<job_id>/complete` or `/fail`.

2. **Scheduler / coordinator (single-box or agent machine)**
   - The coordinator can be a **scheduler Docker image** (or a process): it checks system usage (headroom) first; only when resources are available does it call `POST /api/worker/jobs/start` to claim a job. If it claimed before checking, the job would be taken off the queue even when this machine cannot run it—other worker machines would never see it. So: check headroom → if OK, claim → run container → on exit, complete/fail and repeat.
   - **Single-box:** Scheduler runs on the app machine; checks headroom, then claims via app API, runs `docker run` with env from the job. Good for dev or small deploys.
   - **Two-box:** Scheduler runs only on the agent machine. Same loop: check headroom (max N containers, or CPU/RAM/GPU), then call app API to claim; start container; on exit, complete/fail. No Docker on the app machine.

3. **Concurrency and headroom**
   - Remove the single global `_agent_run_lock` in the app (app no longer runs agents). Concurrency is in the coordinator: allow multiple containers up to **headroom** (e.g. `MAX_CONCURRENT_AGENTS`, or CPU/RAM/GPU thresholds). Keep per-ticket deduplication so the same ticket is not claimed twice.

4. **PR review jobs**
   - Same pattern: review job enqueued with kind=review and extra payload; coordinator starts container with env for review mode. Container calls complete with `review_comment_body`; app marks comment addressed.

**Deliverable:** App only enqueues; moving a ticket to In Progress adds a job to the queue. A coordinator (on the same or a different machine) pulls jobs via Phase 1 API, respects headroom, starts the Phase 3 agent image with job env, and marks jobs done. Logs and completion still appear in the UI via the API. After this phase, the app no longer runs the agent in-process.

---

### Phase 5: Wire remaining four workers into the same agent image

**Goal:** The **same single agent image** from Phase 3 has all five worker CLIs installed but only one worker (OpenCode) wired. Phase 5 adds the **four remaining worker adapters** (Claude Code, Gemini, Codex, Aider) so that when `AGENT_WORKER=claudecode` (or `gemini`, `codex`, `aider`) and the right env are set, the Director uses that worker. No new image; one image, five selectable workers via env.

1. **Pluggable worker interface**
   - The agent’s “worker” call (currently `_send_to_worker`, which dispatches to `_send_to_worker_opencode`) is a pluggable interface: prompt in → output out. The entrypoint or runner reads `AGENT_WORKER` and invokes the correct adapter (OpenCode, Claude Code, Gemini, Codex, or Aider). Director loop is unchanged; only the adapter invoked each turn differs.

2. **Adapters for Claude Code, Gemini, Codex, Aider**
   - Each adapter **invokes the worker via CLI or subprocess** (same pattern as OpenCode): Director’s next prompt → run the worker’s CLI with that prompt (e.g. `claude ...`, `aider ...`) → capture stdout/output → return to Director. No workers that are IDE-only or require interactive approval.
   - **Claude Code:** Adapter runs Claude Code CLI with prompt; returns response. Env: `CLAUDE_API_KEY` (or Anthropic); Director may use same or separate LLM URL.
   - **Gemini:** Adapter runs Gemini CLI. Env: Gemini API key and URL.
   - **Codex:** Adapter runs Codex CLI. Env: OpenAI API key and URL.
   - **Aider:** Adapter runs Aider CLI (prompt in, output out each turn). Env: Aider-compatible API URL and key.
   - All four are **inside the same image**; no new Dockerfile. Document required env and **CLI invocation** per `AGENT_WORKER` value.

3. **Orchestrator / coordinator**
   - Coordinator always uses the **same image** (e.g. `terarchitect-agent`). Per job, it sets `AGENT_WORKER` (from project setting or job payload) and the relevant API keys/URLs for that worker. No need to choose among five different image names.

**Deliverable:** One agent image with all five workers usable; coordinator passes `AGENT_WORKER` and worker-specific env. Simpler than five images: one build, one tag, one distribution.

---

### Phase 6: Cleanup and docs

**Goal:** Finalize the migration: remove or gate the in-process agent path and document how to run the app and the coordinator (from Phase 4) with the single agent image (from Phase 3/5).

- **Deprecate in-process agent path**: Once container-based execution is default and stable, remove or gate the old “run agent in Flask thread” path (or keep it only for local dev without Docker).
- **Document deployment**:
  - **App**: Docker Compose (or PaaS) for API + DB + frontend only. No agent code running in the app process.
  - **Execution**: User runs the **single** agent image via a coordinator that starts containers on job claim; env `AGENT_WORKER` (and worker-specific keys) selects which of the five workers runs. Document required env per worker, network (app URL, LLM/API URL, GitHub), and optional multi-worker concurrency.
- **README / RUNBOOK**: How to run Terarchitect app; how to run the coordinator and the one agent image (setting `AGENT_WORKER` and keys per job); how to point the app at the coordinator when it is on a separate machine.

---

## Dependency Summary

- **Phase 1** — No dependencies. Add agent endpoints and queue table + endpoints. App does not enqueue yet. Test with a script (context, logs, complete, jobs/start with manual INSERT).
- **Phase 2** — Depends on Phase 1. Add `AgentBackend`, `HttpAgentBackend`, standalone runner. Test: run one ticket from CLI with env; repo cloned on host.
- **Phase 3** — Depends on Phase 2. Build single agent image (Director + runner + all five CLIs; OpenCode wired). Test: `docker run` with OpenCode env.
- **Phase 4** — Depends on Phase 1 and Phase 3. App **starts** enqueueing to `agent_jobs` on In Progress and **stops** running the agent in-process. Add scheduler (check headroom → claim → run container → complete/fail). End-to-end with one worker.
- **Phase 5** — Depends on Phase 2 (worker interface) and Phase 3. Add Claude Code, Gemini, Codex, Aider adapters in the same image. Can follow Phase 3 or Phase 4.
- **Phase 6** — After Phase 4 (or 5) is stable. Deprecate in-process path; document deployment.

**Recommended order:** 1 → 2 → 3 → 4 → 5 → 6. Strict order for 1–4; Phase 5 after 3; Phase 6 last.

---

## Explicit decisions (no ambiguity)

| Decision | Choice |
|----------|--------|
| **Queue scope** | **project_id only.** Project IDs are globally unique; no client_id or tenant id. Coordinator passes exactly one `project_id` per request; app returns next pending job for that project only. Multiple projects = coordinator calls start once per project_id (e.g. in rotation). |
| **Queue location** | Queue lives in the app DB (`agent_jobs` table). No Redis or external queue. Coordinator never touches the DB; it uses only `POST /api/worker/jobs/start` (body: `{"project_id": "<uuid>"}`) and `POST .../complete`, `.../fail`. |
| **Start request** | `POST /api/worker/jobs/start`. Body: `{"project_id": "<uuid>"}`. Response: 200 + JSON job payload, or 204 No Content if no pending job for that project. App atomically selects one row WHERE project_id = ? AND status = 'pending', sets status = 'running', returns it. |
| **Claim only when headroom** | The scheduler must **check system usage (headroom) before** calling start. If it has no capacity, it must not call start—the job stays pending so other worker machines can claim it. Never take a job off the queue unless this machine will run it. |
| **Job table fields** | At least: `id`, `ticket_id`, `project_id`, `kind` (`ticket` \| `review`), `status` (`pending` \| `running` \| `completed` \| `failed`), `created_at`. For `kind=review`: store `pr_number`, `comment_body`, `github_comment_id` (in table or payload). |
| **Per-ticket deduplication** | When enqueueing, do not insert a new job if the same ticket already has a job in `pending` or `running`. Only one job per ticket at a time. |
| **Auth** | All worker-facing endpoints (context, logs, complete, jobs/start, jobs/complete, jobs/fail) require auth. Use the same mechanism for coordinator and agent containers (e.g. Bearer token or API key header). Document in Phase 1. |
| **repo_url in job response** | App derives `repo_url` from the project’s `github_url` and includes it in the job response so the coordinator can pass it to the container without a second API call. |
| **Coordinator vs agent image** | **Package together.** The coordinator and the **single** agent Docker image are always shipped and versioned together—same repo, same release, one “agent runtime” bundle. Do not ship the coordinator separately. |
| **Single image vs five images** | **One image with all five workers installed.** Env `AGENT_WORKER` (e.g. `opencode`, `claudecode`, `gemini`, `codex`, `aider`) plus worker-specific keys determine which coding agent runs. One image to build and maintain; coordinator passes the same image and different env per job. Simpler than five separate images. |
| **Workers must be CLI-callable** | Every supported coding agent must be **invokable from the command line** (or subprocess) with a prompt and return output—like OpenCode—so the Director can drive it headlessly in a container. IDE-only tools or tools that require interactive approval for every step are not supported. When adding new workers, verify they have a CLI (or scriptable API) that accepts a prompt and returns agent output. |
| **Repos that use Docker** | **Option C:** Agent does **not** run the repo’s Docker. No Docker CLI in the agent image; no socket mount. Unit tests (and non-Docker tests) are enough; running the repo’s Docker is left to the user or CI. See **Running Docker from the agent container**. |

---

## Running Docker from the agent container (repos that use Docker)

Many repos define Docker images (Dockerfile, docker-compose). The agent runs inside a container; if the worker or the repo’s scripts need to run `docker build` or `docker compose up`, we need a strategy. **Can a Docker container run other Docker images?** Yes, in two main ways:

### Option A: Docker socket mount (recommended for “run the repo’s Docker”)

The **coordinator** starts the agent container with the host’s Docker socket mounted:

```bash
docker run -v /var/run/docker.sock:/var/run/docker.sock ... terarchitect-agent
```

The agent image includes the **Docker CLI** (not the daemon). Commands like `docker build` or `docker compose up` run inside the agent container but talk to the **host’s** Docker daemon via the socket. So “other” containers are created on the **host**, not inside the agent container. This is often called “Docker-outside-of-Docker” (DooD).

- **Pros:** No privileged mode, no second daemon; reuses host Docker, simple.
- **Cons:** The agent container can control the host’s Docker (security); the **agent machine** must have Docker installed and the socket available. Good for a dedicated agent host you control.

Use this when the ticket or the repo’s tests/scripts expect to run Docker (e.g. “run the app with docker-compose”). Document in Phase 3/4: optional `-v /var/run/docker.sock:/var/run/docker.sock` when the coordinator starts the agent container for jobs that need it.

### Option B: Docker-in-Docker (DinD)

Run a **Docker daemon inside** the agent container (e.g. sidecar or `docker:dind`). Then `docker run` from the agent uses that inner daemon; containers run as siblings of the agent process, still inside the same “outer” container or a dedicated DinD container.

- **Pros:** Isolated from the host; no host Docker required on the agent machine in the same way.
- **Cons:** Typically needs privileged mode or special config; more resource and storage use; more complex. Use when you need strict isolation and cannot use the host’s Docker.

### Option C: Agent does not run the repo’s Docker (default)

The agent **only edits code** (including Dockerfiles and docker-compose.yml). It does not run `docker build` or `docker compose up` for the repo. The user or CI runs those on the host or in a separate pipeline after the PR. The agent container does **not** need Docker installed.

- **Pros:** Simplest; no socket or DinD; works everywhere.
- **Cons:** The agent cannot “test” the repo’s Docker setup during the run; that happens later (human or CI).

**Decision: Option C.** The project will use **Option C** only: the agent does not run the repo’s Docker. Unit tests (and any other tests that don’t require Docker) are sufficient; the agent image does not include the Docker CLI and the coordinator does not mount the host’s Docker socket. Running the repo’s Docker (e.g. docker-compose) is left to the user or to CI after the PR. This keeps the agent image and coordinator simple.

---

## Remaining uncertainties (decide before or during implementation)

| Item | Options | Recommendation |
|------|---------|-----------------|
| **Cancel** | (A) Agent container polls `GET .../tickets/.../cancel-requested` and exits if true; (B) Coordinator receives a signal (e.g. from app or user) and runs `docker stop`; (C) Both. | Pick one and document in Phase 1. |
| **Job response when container already called /complete** | If the container successfully called the ticket complete endpoint, `POST .../jobs/<job_id>/complete` is just marking the job row; ticket is already updated. Define whether complete endpoint body can be empty or must echo pr_url/summary for idempotency. | Define in Phase 1 API contract. |
| **Review job enqueue payload** | Stored in `agent_jobs` or in a separate table keyed by job id. Fields: ticket_id, project_id, kind=review, pr_number, comment_body, github_comment_id. | Store in same row (columns or JSON blob). |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Large refactor in Phase 2 (DB → HTTP) | Introduce `AgentBackend` early; implement Flask backend first so behavior is unchanged, then add HTTP backend. |
| Memory (HippoRAG) API shape | Design retrieve/index API in Phase 1 and keep it simple; agent only needs “queries → passages” and “index this doc”. |
| Cancel behavior | Define whether cancel = “orchestrator stops container” or “agent polls API and exits”; implement one and document. |
| Secrets (GITHUB_TOKEN, API keys) in env | Document that orchestrator must pass them into `docker run -e`; consider secrets management (e.g. Docker secrets or env file) in deployment docs. |
| One image with five workers | Single image is larger (all five runtimes/CLIs) but one build, one tag. Share Director and runner; only the worker adapter invoked at runtime differs via `AGENT_WORKER`. Dependency conflicts between workers are possible; if severe, can split to separate images later. |

---

## File / Layout Sketch (Post-Migration)

- `backend/` – Flask app, DB, worker-facing API. Queue lives in app DB; **`POST /api/worker/jobs/start`** (and `.../complete`, `.../fail`) is the simple HTTP contract for the coordinator to claim and finish jobs. No in-process agent loop (or gated for dev only).
- `agent_runner/` or `middle_agent/standalone.py` – Standalone entrypoint + `HttpAgentBackend`; used by the single agent image.
- `middle_agent/agent.py` – Director with research → planning → plan-review → execution phases; `_send_to_worker` (dispatches by `WORKER_TYPE`); `_read_task_plan(project_path, ticket_id)` for `plan/<ticket_id>_task_plan.md`; `_generate_commit_message` for LLM-based commit messages; test-summary worker turn before finalize. Post Phase 2: takes `AgentBackend`; worker selected by `AGENT_WORKER`/`WORKER_TYPE` (opencode, claudecode, gemini, codex, aider). `prompts.json` includes optional planning keys and `worker_test_summary_prompt`.
- **One agent Dockerfile:** `Dockerfile.agent` – single image with Director + all five workers installed; entrypoint reads `AGENT_WORKER` and invokes the correct worker. One image (e.g. `terarchitect-agent`).
- **Coordinator** (CLI or small service): Runs on the **agent machine**; **packaged together** with the single agent image (same repo/release). Uses the **same image** for every job; passes `AGENT_WORKER` and worker-specific env. Polls `POST /api/worker/jobs/start`; checks headroom; starts containers; marks jobs done via `POST .../complete` or `.../fail`. One distribution = coordinator + one agent image.
- `docker-compose.yml` – App only (postgres, backend, frontend). Optional `docker-compose.agent.yml` or docs for “how to run the coordinator + agent containers” on the agent machine (coordinator and single agent image from the same bundle).

This plan keeps the Director central and makes the agent a Docker image that gets spun up and down when needed, with the app and execution fully modular.

---

## Agent image: one image, multiple coding agents (env selects which one)

A **single Docker image** contains the Director and multiple coding agents. **Requirement: every worker must be CLI-callable**—invokable from the command line (or subprocess) with a prompt and returning output, like OpenCode—so the Director can drive it headlessly in a container. IDE-only tools or tools that require interactive approval for every step cannot be used. **Env variable `AGENT_WORKER`** (and worker-specific keys) determines which worker runs. One image to build, tag, and distribute; the coordinator always uses the same image and passes different env per job. Install each worker’s CLI/runtime in the image; entrypoint branches on `AGENT_WORKER` and invokes the corresponding CLI (e.g. `opencode`, `claude`, `aider`) with the Director’s prompt.

| AGENT_WORKER value | Worker       | Env / keys (summary) |
|--------------------|--------------|----------------------|
| `claudecode`       | **Claude Code** | `CLAUDE_API_KEY` (or Anthropic); Director may use same or separate LLM URL. |
| `gemini`           | **Gemini**      | Gemini API key / URL (Google). |
| `codex`            | **Codex**       | OpenAI API key / URL (Codex). |
| `opencode`         | **OpenCode**    | LLM URL + API key; OpenCode provider config. |
| `aider`            | **Aider**       | Aider-compatible API URL and key. |

Common env for all: `TICKET_ID`, `PROJECT_ID`, `TERARCHITECT_API_URL`, `REPO_URL`, `GITHUB_TOKEN`. Entrypoint: read `AGENT_WORKER` → fetch context from API → clone repo → run Director loop with the selected worker → push branch, create PR, call app complete endpoint. **The coordinator is packaged and released together with this single agent image**—one “agent runtime” bundle.

**Adding more workers:** Only include tools that expose a **CLI or subprocess-callable interface** (prompt in, output out). When evaluating new coding agents, verify they can be run non-interactively from a script or container; if they are IDE-only or require per-step human approval, they do not fit this design.

---

## CLI-callable status (Tembo comparison tools)

Tools from the [Tembo 2026 coding CLI comparison](https://www.tembo.io/blog/coding-cli-tools-comparison) that are **confirmed CLI-callable**—invokable from the command line (or subprocess) with a prompt and return output for headless/scripted use. Only these are in scope as workers. Verified as of early 2026; re-check official docs before adding any as a worker.

| Tool | Notes |
|------|-------|
| **Claude Code** | `claude -p "prompt"`; headless mode, `--output-format json`, `--allowedTools` for unattended. [Headless docs](https://code.claude.com/docs/en/headless) |
| **Codex** | `codex exec` for non-interactive; runs task and exits; stdout for piping. [Non-interactive docs](https://developers.openai.com/codex/noninteractive) |
| **Gemini CLI** | `gemini --prompt "..."`; headless; `--output-format json`; stdin pipe. [Headless](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html) |
| **Aider** | `aider --message "instruction"` (or `-m`); single instruction then exit; `--yes` to skip confirmations. [Scripting](https://aider.chat/docs/scripting.html) |
| **Augment CLI (Auggie)** | `auggie --print "instruction"` for CI; `--quiet`, `--output-format json`. [CLI reference](https://docs.augmentcode.com/cli/reference) |
| **Droid** | “Droid Exec (Headless)” for CI/CD; scripting and automation. [Factory CLI](https://docs.factory.ai/cli/configuration/cli-reference) |
| **OpenCode** | `opencode run "prompt"`; `--prompt` with auto-submit; headless/CI. [CLI](https://open-code.ai/docs/en/cli) |
| **Cline** | Cline CLI with `-y` for headless/autonomous; CI/CD, pipe in/out. [Cline CLI](https://cline.bot/cline-cli) |
