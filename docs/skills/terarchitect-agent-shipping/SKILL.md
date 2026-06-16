---
name: terarchitect-agent-shipping
description: "Operate Terarchitect end-to-end: create projects/tickets, run local agents, inspect attempts, accept/reject work, and ship waves through Ship Room with real verification."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [terarchitect, agenthub, ship-room, agent-workflows, release]
    related_skills: [parallel-codex-worktrees, codex, github-pr-workflow]
---

# Terarchitect Agent Shipping

Use this when William asks to exercise Terarchitect itself: create a project, add intents/tickets, run agents, inspect attempts, accept/reject results, compose with Ship Room, merge into the stable branch, and push.

This is an operations skill, not an implementation-plan skill. The deliverable is a working project/ship flow backed by tool output.

## Repo mirror

Keep a mirrored copy of this skill in the Terarchitect repository at:

```text
<terarchitect-repo>/docs/skills/terarchitect-agent-shipping/SKILL.md
```

When updating this skill, update the repo mirror in the same turn. Treat the user-local skill as the runtime copy Hermes loads, and the repo mirror as the project-owned source William can inspect, diff, and version.

## Default local setup

William's normal Terarchitect checkout is:

```bash
cd <terarchitect-repo>
```

Default service topology:

- Frontend UI: `http://localhost:3000`
- Backend API: `http://localhost:5010`
- Postgres host port: `5433` mapped to container port `5432`
- AgentHub host port: `8088` mapped to container port `8080`
- Docker Compose network: `terarchitect_default`
- Agent image: `terarchitect-agent`
- Backend container: `terarchitect-backend`
- Coordinator container: `terarchitect-coordinator`
- AgentHub container: `terarchitect-agenthub`

Default env/config values from `example.env` / `docker-compose.yml`:

```bash
DIRECTOR_PROVIDER=custom
DIRECTOR_LLM_URL=https://openrouter.ai/api
DIRECTOR_MODEL=google/gemini-2.5-flash-lite
WORKER_MODE=codex
WORKER_TIMEOUT_SEC=3600
MAX_CONCURRENT_AGENTS=1
AGENT_DOCKER_MODE=dind
DOCKER_NETWORK=terarchitect_default
AGENTHUB_URL=http://agenthub:8080
AGENTHUB_BRANCH=swarm
MIDDLE_AGENT_DEBUG=1
```

Secrets must stay in the ignored repo `.env`, not in tracked docs or examples. Required secrets usually include:

- `github_agent_token` or `GITHUB_TOKEN`/`GH_TOKEN`/`GITHUB_AGENT_TOKEN` with repo scope for GitHub import/push/Ship Room actions
- `AGENTHUB_API_KEY` for backend/coordinator service calls to AgentHub
- `OPENROUTER_API_KEY` for the default Director setup, unless `DIRECTOR_API_KEY` is set directly
- Worker credentials only when the selected worker mode needs them; Codex normally uses a mounted Codex config directory in the coordinator/agent flow

## Starting and health-checking the system

Start/rebuild from the repo root:

```bash
docker compose up -d
# after agent/coordinator/runtime changes:
docker compose build agent coordinator backend
docker compose up -d backend agenthub frontend coordinator
```

Verify before creating or running tickets:

```bash
docker compose ps
curl -fsS http://localhost:5010/api/health || curl -fsS http://localhost:5010/health
curl -fsS http://localhost:8088/api/health || true
python -m cli --help
python -m cli --api-url http://localhost:5010 --output json project list
```

Verify auth in the runtime that will perform the operation, not just on the host:

```bash
gh auth status
docker exec terarchitect-backend gh auth status
docker exec terarchitect-backend gh auth setup-git
```

Backend tests from repo root need:

```bash
PYTHONPATH=$(pwd)/backend:$(pwd) pytest -q backend/tests/<test_file>.py
```

## Human approval boundaries

Terarchitect should not silently promote code across trust boundaries.

- Moving a ticket to In Progress enqueues work; the coordinator/agent may implement and publish an attempt.
- An implementation attempt is only proposed until a human accepts it.
- Acceptance advances the project's accepted AgentHub frontier; it does not mean GitHub/main has been updated.
- Publishing to GitHub is a separate explicit step. Dry-run first; use `--push` only when William asks to push or the task explicitly calls for downstream publication.
- Use `--force` only with explicit user approval and after proving why fast-forward is impossible.

## Core principles

1. **Verify the live runtime first.** Check backend health, AgentHub availability, CLI help, target repo git cleanliness, and auth before creating work.
2. **Use the actual target repo path and remote.** For local execution projects, set `--project-path` to the real checkout and record its current `git rev-parse HEAD` as the project frontier.
3. **Create low-risk, testable tickets.** Pick tasks with clear acceptance criteria and narrow file ownership so the Ship Room can compose them safely.
4. **Do not trust process completion alone.** Inspect ticket logs, attempts, attempt diffs/files, and test outputs before accepting anything.
5. **Ship only after composition and final repo verification.** Review wave, dry-compose/diff, compose, then run the target repo’s real tests and inspect git state before push.

## Standard end-to-end flow

### 1. Start and verify services

- Start or rebuild the Terarchitect services from the current checkout when the code just changed.
- Verify backend health before calling the CLI.
- Verify AgentHub is reachable and the `ah` CLI is on PATH for local agent finalization.
- Verify the target repo is clean and synced enough for the requested operation.

2. **Create/link the project**

For DAG-source-of-truth projects, do not treat the local checkout HEAD as the ongoing source of truth. Use the local repo only as an explicit import source or cache:

1. create/link the project with the GitHub URL, execution mode, project path, and existing repo flag
2. explicitly import the current repo into AgentHub
3. store the returned/imported leaf as `project.accepted_frontier_id`
4. create each ticket with `ticket.base_leaf_id` copied from that frontier unless the operator selects a different AgentHub leaf
5. verify migration/status surfaces before running workers

Legacy `shipped_frontier`/local HEAD checks can still be useful for compatibility, but the worker base must be an AgentHub leaf.

### 3. Seed minimal project graph when needed

Terarchitect can reject moving tickets to `in_progress` when the project graph has no nodes. For smoke projects or CLI-only test projects, add a minimal graph node representing the target area, then associate tickets with that node when moving/running them.

Do not treat this as a product failure; it is a workflow prerequisite for current Terarchitect execution semantics.

### 4. Create tickets with agent-readable constraints

Each ticket should include:

- title
- description
- rationale
- acceptance criteria with exact commands/observable outputs
- constraints/non-goals
- dependencies if any

Keep tasks independent for wave 0 unless the user explicitly wants dependency ordering.

### 5. Run and monitor tickets

For local runs, provide a known-good worker/director environment. Watch both:

- process status
- `ta ticket logs ... --raw`

Some local agent runners produce little parent-process stdout while activity streams into Terarchitect logs.

If a run completes implementation but finalization fails, inspect the runner worktree directly before deciding whether to recover manually.

### 6. Publish/complete attempts safely

Normal path: the agent finalizes through AgentHub and calls the ticket completion endpoint.

In AgentHub-first projects, verify the publish path creates an incremental child bundle from the explicit DAG base. Worker environments should carry `BASE_LEAF_ID`/`BASE_HASH`/`AGENTHUB_ROOT_HASH`; `ah push` should prefer those refs before `origin/*`. If a materialized worker repo has no origin refs and `ah push` uploads a full bundle, large existing repos can fail with HTTP 413 even though the code change is tiny.

Recovery path if local finalization fails after a real commit exists:

1. inspect the runner worktree `git status`, `git log`, and changed files
2. run the ticket’s focused tests in that runner
3. ensure `ah` is configured/joined and on PATH
4. publish the attempt commit to AgentHub
5. complete the ticket with the real AgentHub commit hash and real base hash

If AgentHub rejects a push because it lacks prerequisite commits, seed it with a full bundle from a clone/worktree that contains the base lineage, or use a controlled full-bundle workaround. Restore remotes afterward and verify the push was indexed.

### 7. Inspect and decide attempts

Use the agent-friendly attempt surfaces before accepting:

- list attempts
- show metadata
- inspect files
- inspect diff
- run focused tests

Accept only attempts with a clean, understood diff and credible test evidence. Reject or leave proposed if the attempt is incomplete, too broad, or untestable.

### 8. Ship through Ship Room

Use the Ship Room surfaces in order:

1. `waves --explain`
2. wave `show`/`review`
3. `dry-compose`
4. wave `diff`
5. compose/ship
6. final target repo test suite
7. push the stable branch only after verification

After shipping, verify GitHub or remote state, not just local git.

See `references/local-runner-recovery.md` for concrete recovery recipes for local runner finalize failures, AgentHub lineage seeding, and completion-state errors.

See `references/local-codex-and-director-debugging.md` for Codex sandbox override patterns, `codex exec resume` flag pitfalls, and Director malformed-JSON recovery/debugging steps.

See `references/openrouter-director-config.md` for configuring the Director through OpenRouter, including the correct base URL shape (`https://openrouter.ai/api`), Gemini model IDs, and shell/dotenv API-key expansion pitfalls.

See `references/product-hardening-verification.md` for the integrated verification bundle and operator-contract probes to run after changing Terarchitect's CLI, AgentHub, Ship Room, Director, or coordinator surfaces.

See `references/live-ticket-smoke.md` for a safe disposable live-stack ticket recipe, including public dispatch prerequisites, AgentHub lineage seeding, artifact checks, and the `PYTHONPATH=backend:agent` backend-test invocation.

See `references/dag-source-of-truth-onboarding.md` for existing-repo onboarding under the AgentHub-first DAG model, including import/frontier/ticket-base verification, AgentHub lineage probes, and known pitfalls around host-path mounts, `ah push` incremental bundles, local-run attempt recording, and idempotent accept.

See `references/github-first-agenthub-import-ops.md` for recreating a project from GitHub via AgentHub import, including backend/AgentHub service env, registered API key vs admin key, expected import payload fields, Docker build-context hygiene, and verification commands.

See `references/runtime-ticket-smoke-debugging.md` for live ticket execution debugging after a project is already DAG-seeded, including stale pending job starvation, runtime env bridging, coordinator AgentHub auth, and coordinator import-path fixes.

See `references/containerized-worker-runtime-contract.md` for fully containerized coordinator/agent smoke tests, including Codex auth/config mounts, agent-image test tooling, multi-commit AgentHub publish ancestry checks, and clean retry sequencing after runtime fixes.

See `references/github-seeded-rerun-final-verification.md` for resetting/rerunning a GitHub-seeded AgentHub project, probing both agent/coordinator runtimes, and verifying the final accepted attempt with `ah receipt`, `ah lineage`, ticket attempts, and focused tests.

See `references/coordinator-local-fallback-finalization.md` for recovering tickets that implemented and tested successfully but failed during finalization because the coordinator/local fallback runtime lacked publication or test prerequisites.

See `references/agenthub-lineage-and-director-json-recovery.md` for recovering local worker commits when AgentHub rejects bundles due missing prerequisite ancestry, and for reporting/retrying Director malformed-JSON failures without overstating completion.

## Operator command flow

Use JSON output when automation or exact verification matters:

```bash
python -m cli --api-url http://localhost:5010 --output json <command>
```

Typical lifecycle:

1. **Create/import project** from GitHub URL/ref and verify `accepted_frontier_id` is set to an AgentHub leaf.
2. **Create narrow tickets** with acceptance criteria and focused test commands.
3. **Move/run ticket** through the UI or CLI so the coordinator claims a queued job.
4. **Monitor logs** from both Docker and Terarchitect:
   ```bash
   docker logs -f terarchitect-coordinator
   python -m cli ticket logs <project_id> <ticket_id> --raw
   ```
5. **Inspect attempts** before acceptance:
   ```bash
   python -m cli --output json attempt list <project_id>
   python -m cli --output json attempt show <project_id> <attempt_id>
   python -m cli --output json attempt diff <project_id> <attempt_id>
   ```
6. **Accept only verified attempts.** Acceptance updates the AgentHub accepted frontier for follow-on work.
7. **Ship/publish only after review.** For Ship Room candidate flow, prefer:
   ```bash
   python -m cli ship candidates <project_id>
   python -m cli ship candidate <project_id> <candidate_id>
   python -m cli ship compose-candidate <project_id> <candidate_id>
   python -m cli ship run <project_id> <ship_run_id>
   python -m cli ship ship-run <project_id> <ship_run_id>
   ```
   If the project is using the explicit downstream publish path, use the publish commands below.

CLI names can drift during active development; always confirm with:

```bash
python -m cli --help
python -m cli attempt --help
python -m cli ticket --help
python -m cli ship --help
python -m cli publish --help
```

## Environment and setup checks

Local Terarchitect execution may need:

- `ah` CLI available on PATH
- AgentHub URL/API key or saved `ah join` config
- worker mode/model configuration
- a healthy OpenAI-compatible director endpoint; for William's local smoke tests prefer `DIRECTOR_LLM_URL=http://localhost:8081` with `DIRECTOR_MODEL=latest` when that model endpoint is available
- for OpenRouter Director runs, set `DIRECTOR_PROVIDER=custom`, `DIRECTOR_LLM_URL=https://openrouter.ai/api`, `DIRECTOR_MODEL=<openrouter model id>` such as `google/gemini-2.5-flash-lite`, and ensure `DIRECTOR_API_KEY` resolves from `OPENROUTER_API_KEY`; verify with a real `/v1/chat/completions` smoke call before running tickets. For Director decisions, prefer strict `response_format.type=json_schema` with `strict: true` and `require_parameters: true` provider routing; plain `json_object` mode only guarantees parseable JSON, not exact keys/types.
- configurable Codex sandbox policy for local smoke tests; default to `workspace-write`, but use an explicit `CODEX_SANDBOX=danger-full-access` style override only when the host/container cannot run Codex bubblewrap and the environment is externally trusted
- target repo dependencies available for tests

When launching long-running local ticket runs as background processes, set completion notification or actively poll/wait until exit. Silent background runs are easy to lose while agent activity streams to logs.

Do not save a transient failure as a rule. Save the recovery pattern: verify endpoint health, configure the CLI, seed AgentHub lineage when needed, and rerun from the last safe state.

## Director / Worker and AgentHub messageboard inspection

When William asks how Terarchitect agents coordinate, explain and verify the live split instead of guessing:

- **Director / Middle Agent** is configured by `DIRECTOR_PROVIDER`, `DIRECTOR_LLM_URL`, `DIRECTOR_MODEL`, and `DIRECTOR_API_KEY`. It assesses worker output, reviews plans, decides next prompts, and marks completion; it should not write implementation code directly.
- **Worker lane** is configured by `WORKER_MODE` plus worker-specific env. Current supported modes are `codex`, `opencode`, `claude-code`, and `stub`; `codex` is the default/preferred implementation lane.
- The normal ticket loop is research → planning → Director plan-review → execution turns → Director completion assessment. Code checkpoints: `agent/middle_agent/prompts.json` and `agent/middle_agent/agent.py`.
- AgentHub's messageboard is currently used as a lightweight structured event/context bus, not a rich chat UI. Ticket channels are deterministic: `ticket-<first 24 UUID hex chars>` from `agent/middle_agent/git_backend.py::_ticket_channel`.
- Before work starts, `get_peer_context(ticket_id)` pulls `/api/git/leaves` and recent posts from `/api/channels/{ticket-channel}/posts?limit=10`; that context is prepended to the worker context.
- On attempt publish, `swarm_publish()` posts a JSON `terarchitect_event` of type `attempt_published` to the ticket channel after `ah push`.
- The shipper posts composition events to AgentHub channels too (currently wave-named compatibility channels such as `wave-<project-slug>-<num>`), including `release_composition_started`, `release_composition_failed`, and `release_pr_opened`.
- To verify live board usage, inspect the AgentHub SQLite DB mounted from the running container (usually `data/agenthub/agenthub.db`) or call the AgentHub API. Count `channels`, `posts`, `commits`, and recent joined `posts` + `channels` before claiming what is active.

See `references/agenthub-messageboard.md` for concrete endpoints, event shapes, and the live-inspection query pattern.

## MVP execution model and integration-test sandboxing

For Terarchitect MVP validation, prefer a split execution model:

- **Docker Compose for platform services**: backend, Postgres/pgvector, AgentHub, and other stable infrastructure.
- **Local temporary git worktrees for coding workers**: Codex/OpenCode/Claude run host-side in isolated worktrees so they can use the user's existing auth/config and are easier to debug.
- Treat fully containerized agent workers as an optional hardening/scaling backend, not an MVP prerequisite, unless the user explicitly asks to validate container execution.

When working on Director/Worker prompts or acceptance criteria for projects with web services, add dynamic-port guidance so agent integration tests avoid collisions:

- prefer OS-assigned/free dynamic localhost ports for app services under test
- inject the resolved `BASE_URL` / `PORT` / equivalent env or temp config into the app and tests
- avoid fixed ports unless the framework/tool explicitly requires one
- for Docker Compose dependencies, use a unique compose project name per run and dynamic host port mappings when host access is needed
- always tear down services and process groups after tests


When invoking the shipper manually from source, verify the module actually calls `main()`. If `python -m agent.shipper.shipper` exits silently, run `python -c 'from agent.shipper.shipper import main; main()'` or use the configured service entrypoint.

## Publishing accepted AgentHub commits downstream

When AgentHub is the runtime truth but GitHub remains a downstream distribution/backup target, use the explicit publish path instead of auto-pushing on acceptance.

- Dry-run first:
  ```bash
  python -m cli --output json publish <project_id> --attempt-id <accepted_attempt_id> --branch main
  ```
- Actual GitHub push requires an explicit flag:
  ```bash
  python -m cli --output json publish <project_id> --attempt-id <accepted_attempt_id> --branch main --push
  ```
- The publish service selects the latest accepted/stable attempt by default, or accepts `--attempt-id` / `--commit` overrides.
- It refuses dirty target repos, missing GitHub metadata, and non-fast-forward updates unless `--force` is explicitly passed.
- For GitHub-seeded projects whose configured `project_path` is missing, publish uses an ephemeral clone from `github_url`, then fetches/materializes the AgentHub bundle and verifies fast-forward ancestry. `project_path` is a cache/debug surface, not a hard publish prerequisite.
- On successful `--push`, it updates `project.shipped_frontier` to the published commit. Keep `accepted_frontier_id` as the DAG work frontier unless the project policy says otherwise.
- If `--push` fails with `fatal: could not read Username for 'https://github.com': No such device or address`, verify GitHub auth in the same runtime that is performing the publish, not just the host shell. For backend/container publish paths, run `gh auth status` and `gh auth setup-git` inside that runtime, then retry the publish command.
- After any successful `--push`, separately verify the operator-visible target checkout and remote: `git fetch origin main`, `git status --short --branch`, `git rev-parse HEAD`, `git rev-parse origin/main`, and `git ls-remote origin refs/heads/main`. If the runtime that performed the publish was not the same checkout the user will inspect, fast-forward the local checkout to `origin/main` after confirming the pushed commit.

## Pitfalls

- Starting ticket runs before backend/AgentHub health is proven.
- Creating tickets before setting the project frontier to a real AgentHub leaf/imported target state.
- Letting existing-repo onboarding depend on a backend-container path that is only present on the host; either mount the host project parent into the backend container or run the import path from a host/local backend connected to the same services.
- For GitHub-first AgentHub imports, confusing AgentHub admin keys with registered agent API keys, or pointing backend/coordinator at host `localhost` from inside Docker. Backend/coordinator service-to-service AgentHub calls need a registered AgentHub API key and the Docker-network URL (`http://agenthub:8080`). Keep the real key in ignored `.env`, not committed examples.
- Retrying GitHub import unchanged after AgentHub returns `repository_url is required`; verify/fix the backend payload mapping (`repository_url` + `base_ref`) and response SHA mapping (`resolved_commit_sha`) before recreating the project.
- Rebuilding Docker services with persisted state in the build context. Keep `.dockerignore` excluding `data/`, `.git/`, virtualenvs, node modules, build outputs, caches, and env files so backend/coordinator rebuilds do not trip over root-owned runtime data or leak secrets.
- Forgetting the minimal graph-node prerequisite for `in_progress` transitions.
- Leaving coordinator unscoped during a smoke test when stale pending jobs exist for old projects. If coordinator repeatedly claims/rejects the same unrelated job, temporarily set `PROJECT_IDS` to the active project and then fix the queue/job-claiming behavior so invalid stale jobs cannot starve valid work.
- Importing backend Flask-dependent modules from coordinator/local preflight code. Coordinator images may not include backend deps; preflight should use direct AgentHub HTTP calls and local git commands or run inside the backend runtime.
- Running source-tree coordinator local agent commands without a `PYTHONPATH` that exposes `agent/middle_agent`; use `PYTHONPATH=/app:/app/agent` for coordinator local runs or execute through the dedicated agent image layout.
- Treating a containerized worker as ready before probing the worker lane itself. For Codex workers, verify `codex --version`, a tiny `codex exec` smoke, writable/usable Codex config, and the target repo's test runner inside the runtime that will execute the ticket.
- Treating a TDD red phase as meaningful when the failure is `pytest`/test-runner missing. Fix the agent image or bootstrap target test dependencies, rebuild, clear the old job, and retry; the red phase must fail because of the product assertion.
- Assuming only the configured agent image needs worker tooling. If Docker execution fails or falls back, the coordinator/local runtime may perform finalization or even source-tree execution; probe that exact runtime for `ah`, Python agent imports, worker/test deps, Codex auth, and PATH before rerunning.
- Deleting worker temp dirs immediately after a failed run. If execution reached `task_complete`/`finalize`, first preserve or inspect the worker repo's commits, diff, and focused test results; the implementation may be recoverable even when AgentHub publication failed.
- Requiring `HEAD^ == BASE_LEAF_ID` during AgentHub attempt publish. Workers often make multiple commits; validate that the ticket base is an ancestor of `HEAD` (`git merge-base --is-ancestor`) instead of requiring a direct child commit.
- Using `/api/projects/<id>/start` as the primary smoke trigger when you need readiness validation.
- Running backend tests from repo root without `PYTHONPATH=backend:agent`; imports under `backend/tests` can fail on `utils.*` even when the code is fine.
- Assuming parent process stdout contains the useful agent log; inspect Terarchitect ticket logs.
- Treating a local runner as healthy because it reached TDD red phase; a full pass also requires Director completion assessment, inspectable attempt/diff/tests, and AgentHub state.
- Treating research/plan approval as an end-to-end pass. For Terarchitect itself, continue until the worker receives an execution prompt, produces/commits an attempt, Director marks completion, and attempt surfaces are inspectable; otherwise report partial progress as blocked.
- Assuming a successful HTTP 200 from a local OpenAI-compatible Director means valid control output. If chat `message.content` is empty under strict JSON response formats, use the retry ladder in `references/local-codex-and-director-debugging.md` and add regression tests before rerunning live tickets.
- Retrying Codex worker runs unchanged after `bwrap: loopback: Failed RTM_NEWADDR`; make the sandbox policy explicit and verify the `codex exec`/`resume` flags being emitted.
- Passing `--sandbox` to `codex exec resume`; resume rejects/ignores different flag sets than first-turn exec, so handle resume command construction separately.
- Treating an agent implementation commit as accepted before attempt diff/files/tests have been inspected.
- Pushing to the target repo before Ship Room composition and final tests.
- Assuming host `gh auth status` proves Ship Room merge auth. Ship Room merge runs in the backend runtime, so a bad `GH_TOKEN`/`GITHUB_TOKEN` in the backend container can make `/ship` return 502/401 even when host `gh pr merge` works. Verify backend-runtime GitHub auth or repair/restart the container env before relying on the API merge path.
- Assuming CLI JSON errors are written to stdout. Terarchitect may write structured error envelopes to stderr for nonzero exits; parse the intended stream during operator-contract probes.
- Letting coordinator import paths start the long-running service loop during tests. `coordinator/__main__.py` should be import-safe and guard service startup with `if __name__ == "__main__"`.
- Continuing to retry the same AgentHub auth/bundle command unchanged after a 401 or prerequisite-commit failure; diagnose config/lineage first.
- Saying you merged AgentHub leaves when AgentHub publication failed. If you recovered by importing/cherry-picking local runner commits, report that exact path and keep AgentHub leaf status separate.
- Treating a malformed/fenced Director JSON response as a worker failure. First determine whether the worker completed code changes; then retry/fix Director config or recover the verified local commit as appropriate.

## Reporting contract

When reporting back, include:

- project name/id
- target repo URL/path and base hash
- ticket IDs and titles
- run status for each ticket
- attempt hashes and acceptance decisions
- Ship Room wave/composition result
- final tests run and exact pass/fail result
- pushed branch/remote verification or the blocker preventing push
