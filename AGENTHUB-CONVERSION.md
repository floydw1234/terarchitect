# AgentHub Conversion Plan

## Goal

Convert Terarchitect from a PR-per-ticket product into an AgentHub-native agentic coding system.

The target workflow is:

```text
Tickets define intent
        |
Dependency waves define safe parallelism
        |
Agents publish attempts to AgentHub
        |
Terarchitect accepts attempts and composes selected leaves into a release branch
        |
Humans inspect one release PR and ship it to main
```

PRs should no longer be the normal unit of agent work. The final human-facing ship boundary may still create one coherent release PR for a selected set of leaves. The product should not depend on one GitHub PR per ticket.

---

## Current Problem

The current system has two partially overlapping models:

1. **Terarchitect ticket model**
   - Tickets, columns, dependencies, graph node/edge ownership, worker jobs.
   - This model knows product intent and scheduling constraints.

2. **AgentHub model**
   - Git commit DAG, leaves, commit lineage, channels, posts, replies.
   - This model knows what agents actually did.

The integration currently treats AgentHub mostly as commit transport. In swarm mode, a ticket completes, the worker publishes to AgentHub, and Terarchitect stores the commit hash in the old `prs.commit_hash` field. Merge runs later collect those hashes and compose them into `origin/swarm`.

That works mechanically, but it is not cohesive:

- `prs` stores AgentHub commits even when there is no PR.
- `done` means "agent produced a commit", not "code reached main".
- AgentHub channels are not the canonical ticket conversation yet.
- AgentHub has leaves and lineage, but not accepted/rejected attempt state.
- The UI still has a PR-shaped Review page instead of a wave-shaped Ship Room.

---

## Product Principles

**Tickets define intent.**
Tickets are planning objects. They describe what should happen, why it matters, and which architecture nodes/edges are involved.

**AgentHub records execution.**
AgentHub is the work ledger. It should record attempts, commit lineage, agent discussion, validation results, feedback, acceptance, rejection, release composition, and ship events.

**Attempts are first-class.**
A ticket may have many attempts. Some fail tests, some conflict, some are superseded, and one or more may be accepted into a wave.

**Release PRs are the human review unit.**
Humans should not review every intermediate agent attempt. They should review one coherent release PR composed from selected AgentHub leaves.

**Ship is different from done.**
A ticket can have an accepted attempt before the work is shipped to `main`. The UI must distinguish produced, accepted, composed into a release branch, and shipped.

---

## Proposed Domain Model

### TicketAttempt

New table or AgentHub-side metadata concept:

```text
ticket_attempts
- id
- project_id
- ticket_id
- agenthub_commit_hash
- base_hash
- wave_num
- attempt_num
- agent_id
- status
- summary
- test_status
- test_output
- created_at
- updated_at
```

Suggested statuses:

```text
proposed
validating
accepted
rejected
superseded
composed
release_pr_open
shipped
failed
```

This replaces the current use of `prs.commit_hash` for swarm output.

### Wave

Waves are currently computed from `depends_on_ticket_ids`. That can remain at first, but the result should become explicit:

```text
waves
- project_id
- wave_num
- base_hash
- target_branch
- status
- created_at
- updated_at
```

Suggested statuses:

```text
open
running
ready_to_compose
composing
compose_failed
ready_to_ship
shipping
shipped
failed
```

This can be a formal table later. In the near term, the existing `MergeRun.wave_num` plus computed waves can continue to work while the table is renamed or reinterpreted as a ship run.

### ShipRun

Existing `merge_runs` should evolve into ship/release runs:

```text
ship_runs
- id
- project_id
- wave_num
- status
- release_branch
- release_pr_url
- release_pr_number
- composed_commit_hash
- base_main_hash
- test_status
- test_output
- changed_files
- summary
- error
- shipped_at
- shipped_commit_hash
- created_at
- updated_at
```

The current `pr_url` field should be renamed to make it clear that any PR belongs to the release/ship step, not to an individual ticket. A ship run creates or updates one release PR for the selected leaves.

---

## AgentHub Metadata

Every agent-produced commit should carry enough metadata to connect it back to Terarchitect.

Minimum metadata:

```text
project_id
ticket_id
wave_num
attempt_num
agent_id
base_hash
status
summary
```

Possible storage options:

1. **Commit trailers**
   - Fastest to implement.
   - Works with plain git.
   - Example:

```text
Implement auth middleware

Ticket: 4f8c1...
Project: a13d2...
Wave: 2
Attempt: 1
Base: abc123
Agent: worker-7
```

2. **AgentHub metadata table**
   - Cleaner API.
   - Lets AgentHub query attempts directly.
   - Requires AgentHub schema/API changes.

3. **Terarchitect side table**
   - Least invasive.
   - Keeps AgentHub generic.
   - Still requires disciplined writes when agents publish.

Recommendation: start with Terarchitect `ticket_attempts`, then add AgentHub metadata APIs once the workflow stabilizes.

---

## AgentHub Channels

Make AgentHub channels the canonical conversation for execution.

Per-ticket channel:

```text
ticket-{short_ticket_id}
```

Per-wave channel:

```text
wave-{project_slug}-{wave_num}
```

Project channel:

```text
project-{short_project_id}
```

Events that should be posted:

- ticket assigned
- agent plan
- attempt published
- validation started
- validation passed
- validation failed
- human feedback
- retry requested
- attempt accepted
- attempt rejected
- release composition started
- release composition failed
- release PR opened
- release PR merged
- wave ready to ship
- wave shipped

The Terarchitect UI should read/write these channels instead of maintaining a separate PR comment loop.

---

## API Changes

### Replace PR Review APIs

Current PR-oriented APIs:

```text
GET  /projects/:project_id/review
GET  /projects/:project_id/tickets/:ticket_id/review
POST /projects/:project_id/tickets/:ticket_id/review/comment
POST /projects/:project_id/tickets/:ticket_id/review/approve
POST /projects/:project_id/tickets/:ticket_id/review/merge
```

New AgentHub-native APIs:

```text
GET  /projects/:project_id/ship/waves
GET  /projects/:project_id/ship/waves/:wave_num
POST /projects/:project_id/ship/waves/:wave_num/compose
POST /projects/:project_id/ship/waves/:wave_num/feedback
POST /projects/:project_id/ship/waves/:wave_num/ship
```

Ticket attempt APIs:

```text
GET  /projects/:project_id/tickets/:ticket_id/attempts
POST /projects/:project_id/tickets/:ticket_id/attempts/:attempt_id/accept
POST /projects/:project_id/tickets/:ticket_id/attempts/:attempt_id/reject
```

Worker completion API:

```text
POST /projects/:project_id/tickets/:ticket_id/complete
```

Should create a `TicketAttempt` in swarm mode instead of writing `prs.commit_hash`.

---

## UI Changes

### Replace Review With Ship Room

The existing Review page should become the Ship Room.

Ship Room top-level layout:

- **Project frontier**
  - `main` hash
  - AgentHub root hash
  - pending leaf count
  - latest shipped wave
  - latest ready wave

- **Wave list**
  - wave number
  - ticket count
  - attempt count
  - status
  - test result
  - ship run status
  - release PR status
  - shipped/unshipped

- **Wave detail**
  - tickets in the wave
  - accepted attempts
  - rejected/failed attempts
  - changed files
  - generated summary
  - tests
  - conflicts/errors
  - AgentHub board posts

- **Actions**
  - Compose release
  - Rerun tests
  - Request changes
  - Create fix ticket
  - Open release PR
  - Merge release PR

### Ticket Card Changes

Ticket cards should show AgentHub-native output:

- latest attempt hash
- attempt status
- wave number
- validation status
- shipped/unshipped badge
- link to ticket channel

Avoid showing `Review` or `PR #...` for swarm projects.

### State Naming

The UI should stop using `In Review` for swarm projects.

Suggested labels:

```text
Backlog
Queued
Running
Ready
Shipped
Failed
```

Internally, the old column IDs can be mapped during migration, but the visible language should match the new model.

---

## DAG Root and the Moving Frontier

There is no persistent `swarm` branch. The AgentHub DAG is the working state. Agent leaves ARE the pending work — they are never accumulated into a separate branch.

The DAG has a moving root: the last shipped commit. Every time a wave is shipped to main, that commit is recorded as the new AgentHub root. All new agent work starts from this root (or from a leaf descended from it).

```text
main@SHA-A  <- root
    |
[leaves from wave 1 tickets]
    |
ship wave 1 -> main@SHA-B  <- new root recorded in AgentHub
    |
[leaves from wave 2 tickets start here]
```

### Root Refresh on Ship

When main advances (via ship or a direct human commit), the coordinator fires a root refresh:

- Record new main tip as the current AgentHub root.
- Queued (not yet started) tickets: update their base to the new root.
- In-flight tickets: leave them alone. Divergence is handled at ship time.
- Tickets waiting on a parent that was just shipped: re-evaluate. Their dependency is now in main, so they start from the new root.

---

## Agent Base Selection

When a ticket job is dispatched, the coordinator selects its starting commit using this priority order:

1. **Single explicit dependency** — start from that ticket's accepted leaf. No LLM needed.
2. **Multiple explicit dependencies** — compose the parent leaves into a temporary base commit, then start from that. The coordinator handles this before dispatching the job.
3. **No dependency, no leaves ahead of root** — start from the last shipped commit (main tip). Clean, unambiguous.
4. **No dependency, leaves exist ahead of root** — LLM selects the best leaf as base. Typically the furthest-ahead validated leaf. LLM is a tiebreaker here, not the primary mechanism.

The dependency graph does the real work. The LLM only acts when the graph is silent.

This logic replaces `prepare_work` in `git_backend.py`.

---

## Compose And Ship Flow

### Current Merge Flow (to be replaced)

```text
wave done
  -> MergeRun queued
  -> merger fetches ticket commit hashes
  -> merger merges commits into origin/swarm
  -> merger optionally runs tests
  -> merger marks MergeRun done
```

### Target Flow — Leaf-Based, No Swarm Branch

There is no `origin/swarm`. Ship Room operates directly on AgentHub leaves.

```text
human (or LLM suggestion) selects leaves to ship
  -> coordinator resolves full ancestry for each leaf back to last shipped root
  -> coordinator checks dependency ordering (leaf B must include leaf A if B depends on A)
  -> compose selected leaves into a coherent release branch based on current main
  -> run tests on composed result
  -> conflicts? -> surface in Ship Room, offer "Create fix ticket", abort
  -> tests fail? -> mark ship_failed, surface output
  -> tests pass -> open/update one release PR
  -> human reviews the coherent PR
  -> merge PR with --no-ff into main
  -> record shipped_commit_hash as new AgentHub root
  -> mark selected TicketAttempts as shipped
  -> coordinator fires root refresh
```

Key points:
- Composition happens at ship time against current main into a short-lived release branch, not into a persistent swarm branch.
- The release PR is a final review artifact for a coherent set of selected leaves, not the unit agents work in.
- `--no-ff` merge commit gives one SHA to revert if the ship is bad.
- The shipped commit becomes the new DAG root immediately.
- In-flight leaves that were based on the old root are not invalidated — their divergence is evaluated at their own ship time.

### Selective Shipping

Because there is no swarm branch, leaves can be composed into release PRs independently or in subsets. A failing or conflicting leaf does not block other leaves. The Ship Room shows which leaves are clean and which are not, and the human picks what to land.

### Staleness Warning

The Ship Room should show a staleness badge when selected leaves are N commits behind current main. Surface this before the user clicks Ship, not after. "These 3 leaves are 8 commits behind main — compose will run a conflict check."

---

## Implementation Phases

### Phase 1 - Flip The Default

Goal: make swarm mode the normal path without deleting structured mode yet.

- Change project default `git_mode` to `swarm`.
- Change CLI default to `swarm`.
- Change frontend project defaults to `swarm`.
- Hide structured mode behind an advanced setting.
- Stop starting the PR poller unless legacy structured mode is enabled.
- Update README and onboarding text.

### Phase 2 - Introduce TicketAttempt

Goal: stop storing AgentHub ticket output in the `prs` table.

- Add `ticket_attempts`.
- On swarm completion, create a `TicketAttempt`.
- Migrate existing `prs.commit_hash` rows into `ticket_attempts`.
- Update ship-run leaf collection to read accepted/latest attempts.
- Keep `prs` only for legacy structured projects.

### Phase 3 - Ship Room

Goal: replace per-ticket PR review with release/wave review.

- Replace Review page copy and routes with Ship Room concepts.
- Add wave list/detail API.
- Show ship-run status, release PR status, test output, changed files, and attempts.
- Add feedback action that posts to AgentHub channels.
- Add manual compose/release trigger and rerun tests controls.

### Phase 4 - Ship To Main

Goal: make shipping explicit, safe, and leaf-based. No swarm branch; one coherent release PR at the boundary.

- Add ship endpoint (`POST /projects/:project_id/ship/waves/:wave_num/ship`).
- Coordinator resolves selected leaves and their ancestry back to the last shipped root.
- Validate dependency ordering — if leaf B depends on A, A must be included in the ship.
- Compose selected leaves into a release branch based on current main tip.
- Run tests on composed result.
- On conflict: surface in Ship Room, abort, offer "Create fix ticket" action.
- On test failure: mark wave `ship_failed`, surface output.
- On success: open or update one release PR with summary, selected tickets/leaves, changed files, and test output.
- Human reviews the release PR.
- On approval: merge PR with `--no-ff` into main.
- Record `shipped_commit_hash` on the ship run, mark wave `shipped`.
- Record new main tip as AgentHub root.
- Fire coordinator root refresh: update queued tickets, unblock dependent tickets.

### Phase 5 - AgentHub Events

Goal: make AgentHub feel like the execution ledger.

- Add event-shaped posts or an AgentHub `/events` API.
- Emit attempt, validation, release composition, feedback, release PR, and ship events.
- Render event timeline in ticket and wave views.

### Phase 6 - Remove PR-Per-Ticket

Goal: remove the old product path.

- Remove PR Review page and detail page.
- Remove PR poller.
- Remove `AgentJob.kind="review"` if no longer needed.
- Remove PR comment classifier.
- Remove GitHub PR create/comment/merge code from the normal agent path.
- Keep release PR creation as the final ship artifact, not as a per-ticket workflow.

---

## What To Keep Temporarily

Keep these until the AgentHub path is stable:

- `github_url` for cloning, opening the release PR, and merging to main.
- Structured mode behind an advanced or legacy flag.
- `prs` table for existing data and structured projects.
- `cli review` until `cli ship` reaches parity.

Do not keep per-ticket PRs in the main user flow.

---

## Open Decisions

1. **Where should AgentHub metadata live first?**
   - Terarchitect table is fastest.
   - AgentHub metadata API is cleaner long-term.

2. **Can multiple attempts per ticket be accepted?**
   - Usually no.
   - But for large tickets, partial accepted attempts may be useful.

3. **Should waves be persisted or computed?**
   - Compute is fine initially.
   - Persist once users need manual wave edits.

4. **Where is the AgentHub root stored?**
   - Recommended: a `shipped_frontier` field on the project (last shipped commit hash + timestamp).
   - Coordinator reads this on startup and on every root refresh event.
   - AgentHub does not need to know about it — Terarchitect owns the root pointer.

5. **What exactly does `done` mean?**
   - Recommended: remove visible `done` from swarm UX.
   - Use `Ready` and `Shipped`.

6. **Should AgentHub remain generic?**
   - Recommended: yes.
   - Add generic attempt/batch/event concepts only if they do not hard-code Terarchitect's architecture graph.

---

## Success Criteria

The conversion is successful when:

- New projects default to swarm mode.
- A ticket completion creates a TicketAttempt, not a PR row.
- The main UI never asks the user to review one PR per ticket.
- A human can inspect selected leaves, review the release PR, and ship to main without leaving Terarchitect.
- AgentHub channels show the real execution conversation.
- Failed attempts, rejected attempts, accepted attempts, composed release PRs, and shipped waves are all visible.
- Structured PR mode can be removed without breaking the core product.
