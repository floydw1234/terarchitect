# Terarchitect — Session 1 Summary

## Project Overview

Terarchitect is an autonomous software engineering orchestration platform. A tech lead / VP draws an **architectural graph** (nodes = components/services, edges = dependencies/interfaces), writes project objectives in notes, and the system autonomously creates tickets, dispatches AI agents to implement them, and merges the results.

**Repo root:** `/home/william/Documents/codingProj/terarchitect`

**Stack:**
- Backend: Python/Flask + PostgreSQL (`backend/`)
- Agent: Python (`agent/`)
- CLI: Python (`cli/`) — invoked as `ta` or `python -m cli`
- Frontend: (exists, not heavily touched this session)
- Docker Compose for local dev/test

---

## Architecture

### Two execution modes
- **structured** (default): GitHub branches + PRs, one agent per ticket
- **swarm**: agenthub DAG + message board, multiple parallel agents, no PRs until merge

### Key models (`backend/models/db.py`)
- `Project` — name, description, project_path, github_url, execution_mode, git_mode ("structured"|"swarm")
- `Graph` — nodes (JSONB list), edges (JSONB list), version; nodes have {id, label, type}, edges have {id, source, target, label}
- `Ticket` — title, description, column_id ("backlog"|"in_progress"|"done"), priority, status, **associated_node_ids** (JSONB), **associated_edge_ids** (JSONB), **depends_on_ticket_ids** (JSONB)
- `Note` — content, node_id (nullable), edge_id (nullable) — notes can be attached to specific graph nodes/edges
- `AgentJob` — job dispatch tracking
- `MergeRun` *(new this session)* — id, project_id, wave_num, status ("queued"|"running"|"done"|"failed"), commit_hash, pr_url, error
- `PR` — stores commit_hash per ticket after swarm completion
- `KanbanBoard`, `ExecutionLog`, `PRReviewComment`, `RAGEmbedding`

### Agent entry points
- `python -m agent.agent_runner ticket` — process a single ticket (existing)
- `python -m agent.agent_runner review` — handle PR review (existing)
- `python -m agent.planner` — generate tickets from graph+notes via LLM *(new)*
- `python -m agent.merger` — merge a completed wave of commits *(new)*

### Key env vars for agents
```
TICKET_ID / PROJECT_ID / TERARCHITECT_API_URL
TERARCHITECT_WORKER_API_KEY
TERARCHITECT_MODE=swarm           # enables swarm code path
AGENTHUB_URL / AGENTHUB_API_KEY / AGENTHUB_BRANCH
DIRECTOR_LLM_URL / DIRECTOR_MODEL / DIRECTOR_API_KEY / DIRECTOR_PROVIDER
WORKER_MODE=stub                  # for testing (no real LLM)
MERGE_TEST_COMMAND                # shell cmd run after merging (e.g. "pytest tests/ -x")
GH_TOKEN / GITHUB_TOKEN           # for PR creation
```

---

## Work Done This Session

### 1. Phase 1 Smoke Tests — Fixed & Passing (38/38)
**File:** `tests/smoke/` (38 tests covering project/ticket/graph/kanban/notes CRUD)

**Bug found and fixed:** `POST /api/projects` was silently defaulting name to "Untitled Project".
**Fix in `backend/api/routes.py`:**
```python
if not data.get("name"):
    return jsonify({"error": "name is required"}), 400
```

### 2. Phase 4 — Stub Swarm Tests (3/3)
**File:** `tests/integration/test_swarm.py`

Uses:
- Stub LLM server (`tests/stubs/llm_server.py`) on port 8099
- Stub agenthub server (`tests/stubs/ah_server.py`) on port 8098
- Mock `ah` binary (`tests/stubs/ah`) — bash script that exits 0
- `WORKER_MODE=stub` — deterministic worker, no real LLM
- `TERARCHITECT_MODE=swarm`

**Channel name fix:** agenthub enforces 31-char max on channel names.
Fixed in `agent/middle_agent/git_backend.py`:
```python
def _ticket_channel(ticket_id: str) -> str:
    short = str(ticket_id).replace("-", "")[:24]
    return f"ticket-{short}"  # 7 + 24 = 31 chars
```

### 3. Phase 4b — Real Agenthub Tests (3/3)
**File:** `tests/integration/test_swarm_real.py`

- Compiles real `agenthub-server` + `ah` binaries via `go build` (requires Go on PATH)
- Falls back to Docker build if `go` is absent
- Key helper: `_seed_agenthub_dag()` — seeds the empty DAG with a full bundle before incremental `ah push` calls work
- Tests: 3 agents push to real DAG, verify leaves + channel posts

**Critical insight: empty DAG problem**
`ah push` creates incremental bundles (`HEAD ^origin/HEAD`). An empty agenthub repo has no `origin/HEAD`, so the first push fails. Solution: seed the DAG by cloning origin, removing the remote (forces full bundle), then running `ah push`.

### 4. Graph-Aware Swarm Scheduling
**File:** `backend/api/routes.py`

Before dispatching a swarm job, the backend checks if any of its `associated_node_ids` / `associated_edge_ids` are currently occupied by a running job. If so, it skips that ticket and tries the next one.

```python
def _occupied_nodes_edges(project_id) -> tuple: ...
def _claim_swarm_job(project_id): ...
```

Special case: tickets with `["*"]` in `associated_node_ids` wait until ALL other jobs finish (whole-codebase changes).

### 5. Planning Agent — NEW
**Files:** `agent/planner/__init__.py`, `agent/planner/__main__.py`, `agent/planner/planner.py`
**CLI:** `cli/commands/plan.py` → `ta plan <project-id>`

**What it does:**
1. Fetches project + graph (nodes/edges) + notes from backend
2. Groups node-specific notes alongside their node in the LLM context
3. Calls LLM once with full context → JSON array of tickets
4. Each ticket has: title, description, `associated_node_ids`, `associated_edge_ids`, `depends_on_titles`, priority
5. POSTs tickets to backlog
6. Second pass: resolves `depends_on_titles` → real UUIDs via PATCH

**Usage:**
```bash
export DIRECTOR_LLM_URL=https://api.openai.com/v1/chat/completions
export DIRECTOR_API_KEY=sk-...
export DIRECTOR_MODEL=gpt-4o
ta plan <project-id> [--dry-run] [--max-tickets 20]
```

**Test:** `agent/tests/test_planner.py` (8 tests, all passing)

### 6. Wave-Based Merge Agent — NEW
**Files:** `agent/merger/__init__.py`, `agent/merger/__main__.py`, `agent/merger/merger.py`
**CLI:** `cli/commands/merge.py` → `ta merge waves|runs|trigger|run-local`
**Tests:** `agent/tests/test_waves.py` (9 tests, all passing)

**Wave strategy:** tickets are grouped into topological layers based on `depends_on_ticket_ids`. Wave 0 = no deps. Wave N = max(parent waves) + 1. Merge fires at the end of each wave.

**Wave computation (`_compute_waves`)** — BFS, handles cycles (fallback to wave 0), handles unknown dep refs (ignored).

**Auto-trigger:** After every `POST /tickets/{id}/complete` in swarm mode, backend calls `_maybe_trigger_wave_merge()`. If all tickets in the completed ticket's wave are `done` and no merge run exists yet → creates a `MergeRun(status="queued")`.

**Merge agent algorithm:**
1. Claim next queued `MergeRun` via `POST /api/worker/merge/next`
2. Get commit hashes from backend (stored in `PR` table per ticket) — falls back to agenthub leaves
3. `git bundle unbundle` any missing commits from agenthub
4. `git checkout -B wave-{N}-merge {first_hash}`
5. `git merge --no-ff` each remaining hash sequentially
6. Run `MERGE_TEST_COMMAND` if set
7. `git push -u origin wave-{N}-merge --force-with-lease`
8. `gh pr create` if `GH_TOKEN` + `github_url` are set
9. Report to `POST /api/worker/merge/{id}/done` or `/fail`

**On failure:** auto-creates a fix ticket tagged `["*"]` (whole-codebase) with test output in description, posted to backlog.

**New backend routes:**
```
GET  /api/projects/{id}/merge/waves        → wave breakdown + merge run status
POST /api/projects/{id}/merge/trigger      → manually queue merge (for "Go" button)
GET  /api/projects/{id}/merge/runs         → merge run history
POST /api/worker/merge/next                → coordinator claims next run
POST /api/worker/merge/{id}/done           → report success
POST /api/worker/merge/{id}/fail           → report failure + auto-create fix ticket
```

**New model:**
```python
class MergeRun(db.Model):
    __tablename__ = "merge_runs"
    id, project_id, wave_num, status, commit_hash, pr_url, error, created_at, updated_at
```
Auto-created by `db.create_all()` — no migration script needed.

---

## Test Suite State

| Tier | File | Count | Status |
|------|------|-------|--------|
| Phase 1 (smoke) | `tests/smoke/` | 38 | ✓ all passing |
| Phase 2+3 (structured) | `tests/integration/test_agent.py` | 3 | ✓ all passing |
| Phase 4 (stub swarm) | `tests/integration/test_swarm.py` | 3 | ✓ all passing |
| Phase 4b (real swarm) | `tests/integration/test_swarm_real.py` | 3 | ✓ all passing |
| Unit: waves | `agent/tests/test_waves.py` | 9 | ✓ all passing |
| Unit: planner | `agent/tests/test_planner.py` | 8 | ✓ all passing |

Run all unit tests: `python -m pytest agent/tests/ -v`
Run smoke tests: `python -m pytest tests/smoke/ -q --no-compose`
Run swarm tests: `python -m pytest tests/integration/test_swarm.py -m swarm -v --no-compose`

---

## "VP Clicks Go" — Remaining Gaps

The end-to-end flow now works as:
1. VP draws architectural graph in UI
2. VP writes objectives in project notes (can be tied to specific nodes)
3. `ta plan <project-id>` → tickets appear in backlog with node assignments + dependencies
4. VP reviews tickets, moves to `in_progress` → swarm agents pick up
5. Graph-aware scheduler prevents conflicts between parallel agents
6. Wave-complete hook auto-queues `MergeRun` → merger agent fires
7. Tests pass → PR created against main; failure → fix ticket auto-created

**Remaining gaps to full autonomy:**
1. **"Go" button on frontend** — calls `POST /projects/{id}/merge/trigger` (or better: a new `POST /projects/{id}/start` that moves backlog tickets to in_progress)
2. **Coordinator dispatch of merge runs** — coordinator currently only polls `AgentJob`; needs to also poll `GET /api/worker/merge/next` and dispatch `agent.merger` subprocesses
3. **Phase 5: CI workflow** — `.github/workflows/integration.yml` to run all test tiers on PRs

---

## Key Files Changed This Session

```
backend/models/db.py               — added MergeRun model + relationship on Project
backend/api/routes.py              — name validation, swarm scheduling, wave computation,
                                     auto-trigger, all merge routes
agent/middle_agent/git_backend.py  — _ticket_channel() for 31-char agenthub limit
agent/planner/__init__.py          — new
agent/planner/__main__.py          — new
agent/planner/planner.py           — new (planning agent)
agent/merger/__init__.py           — new
agent/merger/__main__.py           — new
agent/merger/merger.py             — new (merge agent)
agent/tests/test_planner.py        — new (8 unit tests)
agent/tests/test_waves.py          — new (9 unit tests)
cli/__main__.py                    — added plan + merge commands
cli/commands/plan.py               — new (ta plan)
cli/commands/merge.py              — new (ta merge waves|runs|trigger|run-local)
tests/stubs/ah_server.py           — new (stub agenthub HTTP server)
tests/stubs/ah                     — new (mock ah binary)
tests/integration/conftest.py      — stub_agenthub fixture, agenthub_real fixture,
                                     make_local_git_repo helper
tests/integration/test_swarm.py    — new (Phase 4: 3 stub swarm tests)
tests/integration/test_swarm_real.py — new (Phase 4b: 3 real agenthub tests)
agenthub/Dockerfile                — new (for Docker fallback in tests)
pytest.ini                         — added swarm, swarm_real markers
Makefile                           — added test-swarm-real, test-swarm-real-live targets
```

---

## Agenthub Architecture Notes

Agenthub is a **pure DAG store** — it stores git commit objects as DAG nodes, not a merge server. Key facts:
- `ah push` creates a git bundle and POSTs it to agenthub as a new DAG node
- Multiple agents pushing = multiple leaves in the DAG (diverging history)
- Agenthub does NOT auto-merge — that's the merge agent's job
- Value: single merge point (vs N PRs), parallelism, message board for coordination
- Channel names: max 31 chars, lowercase alphanumeric/dash/underscore

**Why terarchitect differs from ruflo (github.com/ruvnet/ruflo):**
Ruflo wraps Claude Code with a 259-tool MCP server + Raft consensus + Q-Learning router. Terarchitect's unique value is the **architectural graph as the coordination primitive** — tickets are spatially mapped to graph nodes, enabling conflict-free parallel dispatch that ruflo can't do without architectural awareness.
