# Integration Test Plan

## Goal

End-to-end integration tests that spin up the full stack and verify:
1. **Structured mode** — project + graph + tickets created via CLI, agent runs to completion, PR created, ticket moves to `in_review`
2. **Swarm mode** — multiple agents work concurrently on separate tickets, commits land on the `swarm` branch in agenthub, tickets move to `done`

---

## Test Tiers

Tests are split into three tiers with increasing cost and realism.

### Tier 1 — API smoke tests (fast, no agent, no LLM)

Start only `backend` + `postgres`. Verify all CRUD routes work end-to-end via the CLI.

- Create / read / update / delete a project
- Create tickets (single and from file), list, update column
- Set and get graph
- Kanban column operations
- `/api/ready` returns expected shape
- Delete project cleans up all related data

**Infrastructure needed:** just `docker compose up backend postgres`
**Runtime:** < 30 seconds
**Can run in CI on every PR**

---

### Tier 2 — Full stack with stub LLM and mock git (no real API keys)

Start all services plus a **stub LLM server** and a **local bare git repo**.
The coordinator runs; the agent container is started but uses the stub LLM.
No GitHub account or Anthropic/OpenAI key required.

#### Stub LLM server

A small Python HTTP server (`tests/stubs/llm_server.py`) that speaks the OpenAI chat completions API shape and returns canned responses that drive the Director state machine to completion:

```
Turn 1 (research):    "I have reviewed the codebase."
Turn 2 (plan):        "Plan: update README.md to add a line."
Turn 3 (plan review): "APPROVED"
Turn 4 (execution):   "DONE: Updated README.md."
```

Set `DIRECTOR_LLM_URL=http://stub-llm:8099` and `DIRECTOR_PROVIDER=openai` in the test env.

#### Worker stub

For the Worker (the thing that actually changes files), two options:

- **Option A — Real LLM with nano task**: Use a real cheap model (claude-haiku or gpt-4o-mini) with `WORKER_TIMEOUT_SEC=120`. The task is always "add a line to README.md". Costs fractions of a cent.
- **Option B — Worker stub script**: Replace `claude-code` / `opencode` with a small shell script (`tests/stubs/worker_stub.sh`) that just writes a file and exits. Set `WORKER_MODE=stub` (requires a small agent.py code path — see §"New code needed").

Option B is preferred for offline/CI use; Option A is good for periodic "does the real LLM integration still work" checks.

#### Mock gh CLI (structured mode)

Put a fake `gh` binary on PATH inside the agent container (built into a test image variant):

```bash
#!/bin/bash
# tests/stubs/gh — mimics `gh pr create` and returns a fake PR URL
case "$1 $2" in
  "pr create") echo "https://github.com/test/repo/pull/$(date +%s)" ;;
  "pr comment") exit 0 ;;
  "api "*) echo '{"state":"open","merged":false}' ;;
  *) echo "stub gh: unhandled $*" >&2; exit 1 ;;
esac
```

#### Local bare git repo

A bare git repo served over `git://` or HTTP (via `git daemon`) inside the test network. The agent clones from it instead of GitHub.

```yaml
# in docker-compose.test.yml
git-server:
  image: alpine/git
  command: daemon --reuseaddr --base-path=/repos --export-all /repos
  volumes:
    - ./tests/fixtures/repos:/repos
```

A seed repo at `tests/fixtures/repos/test-project.git` contains a minimal project.

#### Structured mode scenario

```
1. docker compose -f docker-compose.yml -f docker-compose.test.yml up
2. python -m cli project create --name "Test Project" --github-url git://git-server/test-project.git
3. python -m cli ticket create <project_id> --title "Add greeting to README"
4. python -m cli ticket run <project_id> <ticket_id> --wait
5. Assert: ticket column == in_review
6. Assert: a PR row exists in the DB (via CLI review list)
7. python -m cli review approve <project_id> <ticket_id>
8. python -m cli review merge <project_id> <ticket_id>
9. Assert: ticket column == done
```

**Runtime:** ~2–3 minutes (dominated by agent container startup)

---

### Tier 3 — Real execution (real LLM + real GitHub, run on demand)

Same as Tier 2 but:
- `DIRECTOR_API_KEY` and `WORKER_API_KEY` point to real Anthropic/OpenAI keys
- `GITHUB_TOKEN` is a real PAT scoped to a dedicated test repo (`terarchitect-test`)
- No stubs — agent runs the full flow including real code generation

Runs manually (`make test-real`) or on a weekly schedule in CI.
A dedicated `terarchitect-test` GitHub repo is kept clean by the test teardown (deletes branches and PRs after assertions pass).

---

## Swarm Mode Scenario (Tier 2+)

Swarm tests can run without GitHub since there are no PRs — only a bare local git repo and agenthub.

```
1. docker compose -f docker-compose.yml -f docker-compose.test.yml --profile swarm up
2. python -m cli project create --name "Swarm Test" \
       --github-url git://git-server/test-project.git \
       --git-mode swarm
3. Create 3 tickets: "Add file A", "Add file B", "Add file C"
4. Run all 3 concurrently (set MAX_CONCURRENT_AGENTS=3 in test env)
5. Wait for all 3 to reach column == done
6. Assert: agenthub /api/git/leaves has 3+ new commits
7. Assert: swarm branch in local repo has all 3 files
8. Assert: 3 board posts exist in the ticket channels
```

The concurrent part is the key swarm assertion — agents overlap in real time, each picking up a different leaf and building on it.

---

## New Infrastructure Needed

### Files to create

```
tests/
  integration/
    __init__.py
    conftest.py           # pytest fixtures: start compose, health-wait, teardown
    test_api_smoke.py     # Tier 1: CRUD via CLI (no agent)
    test_structured.py    # Tier 2: full stack structured mode
    test_swarm.py         # Tier 2: full stack swarm mode
  stubs/
    llm_server.py         # OpenAI-compatible stub returning canned responses
    worker_stub.sh        # Shell worker stub (writes a file, exits 0)
    gh                    # Fake gh CLI binary
  fixtures/
    repos/
      test-project.git/   # Bare git repo (seeded with a minimal README.md)
    project_config.yaml   # Test project config for CLI tests
    tickets.json          # Sample ticket batch for CLI tests
    graph.json            # Sample graph for CLI tests
docker-compose.test.yml   # Test overrides: stub LLM, git-server, test agent image
Makefile                  # Convenience targets: test-smoke, test-full, test-swarm, test-real
```

### New code in agent (for Option B worker stub)

Add `WORKER_MODE=stub` path in `agent/middle_agent/agent.py`:

```python
if worker_mode == "stub":
    # Write a marker file and return a canned completion summary
    open(os.path.join(project_path, "stub_output.txt"), "w").write(ticket.title)
    return "Stub worker completed: wrote stub_output.txt"
```

This is ~5 lines and keeps the real agent completely unchanged.

### `docker-compose.test.yml` overrides

```yaml
services:
  stub-llm:
    build:
      context: .
      dockerfile: tests/Dockerfile.stub-llm
    ports:
      - "8099:8099"

  git-server:
    image: alpine/git
    command: daemon --reuseaddr --base-path=/repos --export-all /repos
    volumes:
      - ./tests/fixtures/repos:/repos

  backend:
    environment:
      - TESTING=1

  agent:
    build:
      context: .
      dockerfile: Dockerfile.agent
      target: test          # test stage copies in stub gh binary
    environment:
      - WORKER_MODE=stub
      - DIRECTOR_LLM_URL=http://stub-llm:8099
      - DIRECTOR_PROVIDER=openai
      - DIRECTOR_API_KEY=stub
```

---

## What to Assert

| Test | Assertion |
|------|-----------|
| Tier 1 CRUD | HTTP 200s, correct JSON shapes, IDs stable |
| Structured run | `ticket.column_id == "in_review"` after agent completes |
| Structured PR | `review list` shows PR row with `pr_state == "open"` |
| Structured merge | `ticket.column_id == "done"` after merge |
| Swarm concurrent | All 3 tickets reach `done`, timing shows overlap |
| Swarm DAG | agenthub `/api/git/leaves` count increases by ≥ 3 |
| Swarm branch | `swarm` branch in local repo contains all expected files |
| Swarm board | Ticket channels have `done:` posts from agents |

---

## CI Integration (GitHub Actions)

```yaml
# .github/workflows/integration.yml
on: [push, pull_request]

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make test-smoke        # Tier 1 only, no secrets

  full-stack:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: make test-full         # Tier 2, stubs only

  real:
    runs-on: ubuntu-latest
    if: github.event_name == 'workflow_dispatch'
    environment: integration
    steps:
      - uses: actions/checkout@v4
      - run: make test-real
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.TEST_GITHUB_TOKEN }}
```

---

## Open Questions / Risks

1. **Docker-in-Docker in CI**: The agent uses dind (privileged containers). GitHub Actions runners support `--privileged` but it may need explicit setup. Alternative: use `dood` mode for CI tests.

2. **git daemon vs HTTP**: `git daemon` serves over port 9418. The agent's `git clone` and `git push` will use this fine, but the stub `gh` binary needs to intercept `gh pr create` before it tries to reach github.com. The mock `gh` script handles this but needs to be first on PATH.

3. **agenthub `ah push` in Tier 2**: The `ah` binary in the agent container will push to the agenthub service — this should work as-is since both are in the same docker network. No stub needed for swarm mode git.

4. **Concurrent swarm test timing**: `MAX_CONCURRENT_AGENTS=3` with the stub worker should produce overlapping execution. Need to verify the coordinator actually starts all 3 quickly; may need a small sleep or explicit job pre-queuing before starting the coordinator.

5. **Test isolation**: Each test run should use a fresh postgres volume and fresh agenthub data dir. The `conftest.py` teardown should `docker compose down -v` after each test session.

---

## Phased Implementation

| Phase | What | Tier |
|-------|------|------|
| 1 | `conftest.py`, seed fixtures, `test_api_smoke.py` | 1 |
| 2 | Stub LLM server, mock `gh`, `docker-compose.test.yml`, `test_structured.py` | 2 |
| 3 | Stub worker mode in agent.py | 2 |
| 4 | `test_swarm.py` | 2 |
| 5 | Makefile targets, CI workflow | CI |
| 6 | Real-LLM test run, cleanup | 3 |
