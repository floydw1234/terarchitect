# AgentHub Integration Plan

## Background

[AgentHub](https://github.com/karpathy/agenthub) is a Karpathy open source project: an agent-first collaboration platform built as a bare git repo + message board, designed for swarms of AI agents working on the same codebase. It replaces GitHub's branch/PR model with a DAG of commits and a simple threaded message board.

This plan describes adding a **swarm mode** to terarchitect that uses agenthub as its git and coordination backend, alongside the existing **structured mode** (GitHub branches + PRs).

---

## Two Modes

### Structured Mode (current behavior, unchanged)

```
Worker clones repo → creates ticket-{id} branch → commits → git push → gh pr create
                                                                              ↓
                                               background PR poller watches GitHub
                                                                              ↓
                                    reviewer comments → re-work job enqueued → PR merged → Done
```

Designed for projects with human reviewers. Quality gate = PR review.

### Swarm Mode (new)

```
Worker clones repo → commits locally → ah push (git bundle to agenthub DAG)
                                                        ↓
                              orchestrator polls: ah leaves + run tests
                                                        ↓
                                      tests pass → Done
                                      tests fail → back to Backlog with failure context
```

Designed for fully automated agent-driven workflows. No GitHub dependency. Quality gate = test suite. No human in the loop unless explicitly desired.

---

## What Changes

### Agent side — 3 methods in `agent/middle_agent/agent.py`

All git/publish operations are already isolated to three methods. The integration wraps these behind a `GitBackend` protocol with two implementations.

**`_ensure_ticket_branch`**

| Structured | Swarm |
|---|---|
| `git fetch origin` | no-op (agents work from latest DAG leaf) |
| `git checkout -b ticket-{id}` from main/master | `ah fetch <leaf_hash>` to get latest peer work, then work from there |

In swarm mode, before starting work the Director is also injected with peer context: the output of `ah leaves` and recent posts from the ticket channel. This tells the Worker what other agents have already tried.

**`_checkout_ticket_branch`**

| Structured | Swarm |
|---|---|
| `git checkout ticket-{id}` (for review re-work) | `ah fetch <commit_hash>` for the specific commit to amend |

Review re-work in swarm mode is triggered by the orchestrator posting to the agenthub board rather than by a GitHub PR comment.

**`_finalize`**

| Step | Structured | Swarm |
|---|---|---|
| Stage changes | `git add -A` | `git add -A` (identical) |
| Check for changes | `git status --porcelain` | `git status --porcelain` (identical) |
| Commit | `git commit -m <message>` | `git commit -m <message>` (identical) |
| Publish | `git push -u origin ticket-{id}` | `ah push` (creates bundle, uploads to agenthub DAG) |
| Notify | `gh pr create --title ... --body ...` | `ah post ticket-{ticket_id} "done: {summary}\ncommit: {hash}"` |
| Review re-work | `gh pr comment <pr_number> --body ...` | `ah reply <post_id> "{feedback_response}"` |

The `git add`, `git status`, and `git commit` steps are identical in both modes. Only the push and notification differ.

### Backend side — minimal changes

The ticket completion API currently stores `pr_number`, `pr_url`, `commit_hash`. In swarm mode there is no PR, so the `complete` endpoint needs to accept either:
- `pr_url` + `pr_number` (structured), or
- `agenthub_commit_hash` (swarm)

The `AgentJob` and `Ticket` models need a nullable `agenthub_commit_hash` field alongside the existing PR fields.

The PR review polling background thread (`_run_pr_poll_loop`) is **not started** in swarm mode.

### What does NOT change

- Kanban board and ticket state machine (Backlog → In Progress → In Review → Done)
- Dependency scheduler (`depends_on_ticket_ids`)
- Director/Worker loop (all LLM orchestration)
- Docker container management (DinD, privileged flag, host URL rewriting)
- Memory system (HippoRAG embeddings)
- Coordinator job queue (`agent_jobs` table, claim/complete/fail flow)
- All existing structured mode behavior

---

## The GitBackend Abstraction

A `GitBackend` protocol class in `agent/middle_agent/git_backend.py` (new file, ~150 lines):

```
GitBackend (Protocol)
├── prepare_work(ticket_id, project_path) → Optional[str]
│     structured: create/checkout ticket branch, return branch_name
│     swarm:      ah fetch <latest_leaf>, return None (no branch name needed)
│
├── checkout_for_review(ticket, project_path) → bool
│     structured: git checkout ticket-{id}
│     swarm:      ah fetch <commit_hash stored on ticket>
│
├── publish(project_path, branch, commit_msg, ticket,
│           review_mode, pr_number, pr_comment, summary)
│         → tuple[pr_url | None, pr_number | None, agenthub_hash | None]
│     structured: git push + gh pr create/comment
│     swarm:      ah push + ah post/reply
│
├── get_peer_context() → str
│     structured: returns "" (no peer context)
│     swarm:      returns formatted string of ah leaves + recent board posts
│                 injected into Director system prompt before work begins
│
└── post_update(channel, message)
      structured: no-op
      swarm:      ah post <channel> <message>
```

The backend is instantiated once per agent run, determined by the `TERARCHITECT_MODE` env var injected into the Docker container by the coordinator.

---

## Configuration

New env vars added to the agent container (via coordinator `docker run`):

| Var | Description |
|---|---|
| `TERARCHITECT_MODE` | `structured` (default) or `swarm` |
| `AGENTHUB_URL` | Base URL of the agenthub server, e.g. `http://agenthub:8080` |
| `AGENTHUB_API_KEY` | Agent's API key (provisioned by agenthub admin on first run) |
| `AGENTHUB_CHANNEL_PREFIX` | Optional prefix for channel names, e.g. project slug (default: `ticket`) |

These are stored in terarchitect's project settings (DB), alongside the existing `GITHUB_TOKEN`, `WORKER_API_KEY`, etc.

---

## Swarm Mode Ticket Lifecycle

### Normal flow

```
1. Ticket created in Backlog
2. Orchestrator (or human) moves to In Progress
3. Coordinator claims AgentJob, starts Docker container with TERARCHITECT_MODE=swarm
4. Agent: get_peer_context() → inject into Director prompt
5. Agent: prepare_work() → ah fetch latest leaf
6. Worker implements the ticket
7. Agent: publish() → git commit + ah push + ah post to ticket channel
8. Agent calls /complete with agenthub_commit_hash (no pr_url)
9. Ticket moves to In Review
10. Orchestrator (or test runner) fetches commit via ah fetch, runs tests
11. Tests pass → orchestrator marks Done via API
    Tests fail → orchestrator posts failure to agenthub board,
                 moves ticket back to Backlog with failure appended to description
```

### Review re-work flow (swarm)

Instead of GitHub PR comments triggering re-work, the orchestrator posts directly to the ticket's agenthub channel. Terarchitect polls the board (replacing the GitHub PR poller) to detect new posts addressed to the agent, enqueues a review `AgentJob`, and the agent calls `checkout_for_review` + `publish` in review mode.

Alternatively: skip "In Review" entirely in swarm mode, with tests as the sole gate. This is the simpler starting point.

---

## Agenthub Concepts Mapped to Terarchitect

| Agenthub concept | Terarchitect mapping |
|---|---|
| Agent (API key) | One per Worker container. Provisioned by coordinator on first use, cached in project settings. |
| Commit | One per ticket completion. Commit message = ticket title + summary. |
| DAG leaf | Latest completed work. `ah leaves` tells orchestrator what's been done before starting next ticket. |
| Channel | One per ticket: `ticket-{ticket_id}`. Plus a project-level `project-{project_id}` channel for cross-ticket coordination. |
| Post | Completion notification, failure report, or orchestrator feedback. |
| Reply | Agent's response to orchestrator feedback in review re-work. |
| `ah diff <hash_a> <hash_b>` | Orchestrator validation: compare ticket's commit against the base to understand what changed before running tests. |

---

## Phased Rollout

### Phase 1 — GitBackend abstraction (no behavior change)

Extract the three existing git methods into `StructuredBackend`. Wire `GitBackend` protocol. All existing tests pass. `TERARCHITECT_MODE=structured` is the only working value.

### Phase 2 — SwarmBackend: push + post

Implement `SwarmBackend.publish()`: `ah push` + `ah post`. Implement `get_peer_context()`. No changes to ticket state machine yet — swarm tickets still go to "In Review" with `agenthub_commit_hash` stored instead of `pr_url`.

### Phase 3 — Test-based quality gate

Add optional test command to project settings (`TEST_COMMAND`, e.g. `pytest` or `npm test`). After `ah push`, coordinator fetches the commit, runs the test command inside a fresh container, and either marks Done or bounces back to Backlog. "In Review" becomes optional in swarm mode.

### Phase 4 — Board-based review loop

Replace GitHub PR poll loop with an agenthub board poll for swarm mode projects. Orchestrator posts review feedback to ticket channel; agent picks it up and does re-work. Full parity with structured mode's review workflow, without GitHub.

---

## agenthub Server Setup

agenthub is a single static Go binary with no runtime dependencies except `git` on PATH. For local development:

```bash
cd servers/agenthub
go build ./cmd/agenthub-server
go build ./cmd/ah
./agenthub-server --admin-key <secret> --data ./data
```

For production, add to `docker-compose.yml` alongside terarchitect backend:

```yaml
agenthub:
  image: agenthub:latest
  volumes:
    - agenthub-data:/data
  environment:
    AGENTHUB_ADMIN_KEY: ${AGENTHUB_ADMIN_KEY}
  command: ["--data", "/data", "--listen", ":8080"]
```

Agent API keys are provisioned once per project (or once per agent identity) via the admin API and stored in terarchitect's project settings table.

---

## Open Questions

1. **Agent identity**: should each Docker container get a unique agenthub API key (ephemeral), or should there be one key per project (shared)? Unique keys make the DAG more informative (you can see which container made which commit) but require provisioning on container start.

2. **Base commit for swarm workers**: when multiple tickets are in progress simultaneously, which DAG leaf should each Worker start from? Options:
   - Always the globally latest leaf (maximally informed but potentially conflicting)
   - The leaf corresponding to the ticket's dependency (deterministic, safe)
   - The leaf at the time the job was enqueued (reproducible)

3. **Conflict resolution**: two Workers may commit changes to the same file from different leaves. agenthub's DAG doesn't enforce merges. The orchestrator needs a strategy: re-run the later ticket on top of the earlier leaf, or accept divergence.

4. **Human visibility**: in structured mode, humans see PRs on GitHub. In swarm mode, visibility is through the agenthub dashboard + terarchitect UI. Consider surfacing the agenthub commit hash and board posts in the terarchitect ticket detail view.
