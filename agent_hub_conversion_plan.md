# AgentHub Conversion Implementation Plan

This is the concrete task plan for converting Terarchitect from PR-per-ticket execution to an AgentHub-native workflow.

The strategic direction has two layers:

1. **Near-term reliability layer** - stop using PRs as the unit of agent work. Capture agent output as AgentHub attempts, compose selected leaves into release artifacts, and keep GitHub/main only as the final shipping boundary.
2. **Differentiating product layer** - evolve from "better agent PR workflow" into a no-main-style **Composite Workspace** where users can select AgentHub leaves, preview/test possible codebase states, bless Snapshot candidates, and export the state they want.

This means the immediate path still builds `TicketAttempt`, `ShipRun`, and Ship Room. But the product thesis is bigger than "one release PR instead of many PRs":

```text
Agents create many possible futures.
Terarchitect helps humans inspect, compose, validate, preview, and bless the future they want.
```

Near-term target model:

```text
Tickets/Intents define goals
Dependency waves define safe parallelism
Agents publish attempts/leaves to AgentHub
Terarchitect validates and accepts attempts
Ship Room selects leaves
Coordinator composes a coherent candidate state
Terarchitect opens one release PR
Human reviews and merges the release PR
Merged main commit becomes the new AgentHub root
```

Long-term target model:

```text
Intents define desired outcomes
Agents publish attempts/leaves to AgentHub
Composite Workspace selects compatible leaves
Verification Engine produces evidence
Blessed Composite Workspace becomes a Snapshot
Snapshot becomes the stable product state
GitHub main optionally mirrors exported Snapshots
AgentHub root derives from the latest blessed/exported/imported Snapshot
```

The key near-term product rule: **no PR per ticket**. A PR exists only at the final release boundary.

The key long-term product rule: **main is not the work universe**. AgentHub is the work universe. Main is only one shipped/blessed artifact.

---

## How To Use This Plan

This file is the execution checklist. `AGENTHUB-CONVERSION.md` explains the strategy; this file breaks the strategy into buildable tasks.

Work through the phases in order unless a later phase explicitly says it can be built behind a stub. The safest order is:

1. Add the schema and write paths that preserve data.
2. Start treating tickets as intent objects instead of execution containers.
3. Change worker/coordinator behavior to produce AgentHub attempts.
4. Build the read-only Ship Room over the new state.
5. Add composition and release PR creation.
6. Add merge-to-main and root refresh for the compatibility path.
7. Add Composite Workspace as the no-main differentiator.
8. Add Verification, Graph, Multi-Repo, and Snapshot phases before treating no-main as production-grade.
9. Remove PR-per-ticket only after the AgentHub path works end to end.

Global invariants:

- A ticket describes intent. Long-term, think of `Ticket` as `Intent`.
- A `TicketAttempt` describes agent output.
- A `ShipRun` describes release composition and export/promotion workflow.
- A `Snapshot` describes the stable product state once the no-main model is active.
- AgentHub leaves are pending work.
- Before Snapshots exist, the AgentHub root is the last shipped `main` commit.
- After Snapshots exist, the AgentHub root derives from the latest blessed/exported/imported Snapshot.
- A release/export PR is allowed only at the final compatibility boundary.
- No normal swarm ticket completion should create a GitHub PR.
- In-flight attempts are never silently rewritten when `main` advances.
- The UI must distinguish `ready` from `shipped`.
- `done` is not a valid synonym for shipped in AgentHub-native mode.
- Kanban is a view, not the workflow source of truth.
- Composite Workspace is the future no-main product layer; Ship Room is the first production-safe subset.
- GitHub `main` is a compatibility/export surface in the long-term model, not the internal work universe.

Related planning docs:

- `AGENTHUB-CONVERSION.md` - strategy and architecture direction.
- `ticket_redefinition.md` - why tickets should become intent objects.
- `no_main_idea.md` - no-main / composite-state thesis.
- `UI_plan.md` - UI direction for Intents, Attempts, Workspace, and Ship.

---

## Phase 0 - Unblock Safe Iteration

Goal: make the codebase safe enough to perform schema and workflow changes without constantly fighting startup/runtime issues.

### 0.0 Inventory current PR and swarm paths

- Search backend, agent, CLI, frontend, and tests for:
  - `git_mode`
  - `structured`
  - `swarm`
  - `PR`
  - `prs`
  - `Review`
  - `MergeRun`
  - `merge_runs`
  - `origin/swarm`
  - `swarm_publish`
  - `prepare_work`
- Create a short implementation note or tracking issue that maps each reference to one of:
  - keep temporarily for legacy structured mode
  - migrate to `TicketAttempt`
  - migrate to `ShipRun`
  - remove after Ship Room parity
- Identify tests that assert PR-per-ticket behavior and mark whether they should be rewritten or preserved as legacy structured coverage.

Acceptance criteria:

- There is a clear list of PR-era code paths before conversion starts.
- No later phase has to rediscover where PR-per-ticket behavior lives.

### 0.1 Add coordinator module entrypoint

- Add `coordinator/__main__.py`.
- It should import and call `coordinator.main()`.
- Verify `python -m coordinator` works from repo root.
- Update any docs that currently reference a non-working command.

Acceptance criteria:

- `python -m coordinator` starts the coordinator loop.
- Existing coordinator environment variables still work.
- No behavior changes beyond making the documented entrypoint valid.

### 0.2 Add a migration mechanism

- Add Alembic or a lightweight migration runner.
- Record current schema as baseline.
- Ensure migrations run on backend startup or via a documented command.
- Stop relying on `db.create_all()` as the only schema evolution path.

Acceptance criteria:

- Existing DBs can be upgraded without wiping state.
- New DBs can be initialized from migrations.
- Future phases can add `ticket_attempts`, root fields, and ship-run fields safely.

### 0.3 Keep worker auth unblocked

- Review UI auth and worker auth path handling.
- Ensure worker-facing routes used by agents and coordinator remain accessible with `TERARCHITECT_WORKER_API_KEY`.
- Ensure enabling UI auth does not block worker calls.
- Defer full auth productization unless it blocks conversion work.

Acceptance criteria:

- Agent job completion still works with UI auth enabled.
- Coordinator job claim and ship-run claim still work with worker auth enabled.

### 0.4 Stabilize stale-job reset

- Change coordinator startup reset from unconditional `max_age_seconds=0` to a sane default.
- Make the reset threshold configurable.
- Document the setting.

Acceptance criteria:

- Restarting the coordinator does not immediately reset legitimate running jobs unless configured to do so.

---

## Phase 1 - Flip The Product Default To AgentHub

Goal: make swarm/AgentHub the normal path for new projects while keeping structured mode only as a temporary compatibility path.

### 1.1 Change project default git mode

- Change DB/model default from `structured` to `swarm`.
- Add migration to update the default for future projects.
- Decide whether existing projects remain as-is or are migrated explicitly.

Acceptance criteria:

- New projects created without specifying `git_mode` use `swarm`.
- Existing structured projects are not silently broken.

### 1.2 Change API creation fallback

- Update project creation route so missing/unknown `git_mode` defaults to `swarm`.
- Keep accepting `structured` for legacy mode.
- Audit all server-side fallbacks of `(project.git_mode or "structured")`.
- Change target defaults to `swarm` where appropriate.

Acceptance criteria:

- API-created projects default to swarm.
- Structured mode still works only when explicitly requested.

### 1.3 Change CLI defaults

- Update `cli project create --git-mode` default to `swarm`.
- Update help text to describe structured mode as legacy/advanced.
- Update config examples and fixtures as needed.

Acceptance criteria:

- `ta project create --name X` creates a swarm project.
- `ta project create --git-mode structured` remains possible temporarily.

### 1.4 Change frontend defaults and copy

- Default project creation/editing UI to swarm.
- Hide structured mode behind an advanced section or legacy label.
- Remove primary UI copy that says the product creates PRs per ticket.

Acceptance criteria:

- A new user sees AgentHub/swarm as the default path.
- PR-per-ticket is not presented as the normal workflow.

### 1.5 Gate the PR poller

- Stop starting the PR poller by default.
- Start it only when legacy structured mode is explicitly enabled.
- Prefer a backend env var such as `ENABLE_LEGACY_PR_POLLER=1`.

Acceptance criteria:

- A swarm-only setup does not poll GitHub PRs.
- Legacy structured projects can still opt into the poller while the path exists.

---

## Phase 2 - Redefine Tickets As Intents

Goal: keep the existing `tickets` table and API for now, but stop treating a ticket as the object that owns all execution state. A ticket is the durable intent: goal, rationale, acceptance, architecture scope, and dependencies.

This phase does not require renaming routes from `/tickets` to `/intents`. It changes semantics first. Naming can change later.

### 2.1 Define the intent contract

Document and enforce that a ticket/intent owns:

```text
goal
rationale
acceptance criteria
constraints
architecture scope
dependencies
priority/value
```

It does not own:

```text
agent attempt output
release PR status
ship status
validation logs
composite workspace state
```

Acceptance criteria:

- New backend/UI copy describes tickets as intent where appropriate.
- New execution state is not added directly to ticket columns.
- Existing `tickets` table remains compatible.

### 2.2 Add intent-oriented fields

Add fields gradually, behind migrations:

```text
rationale
acceptance_criteria
constraints
intent_status
value_score
risk_level
created_source
```

Recommended initial `intent_status` values:

```text
draft
ready
active
blocked
archived
```

Acceptance criteria:

- Agents can receive acceptance criteria and constraints separately from freeform description.
- Ticket detail can show why the work matters, not only what to change.

### 2.3 Stop using `done` as the source of truth

- Keep `column_id` during migration.
- Stop adding new logic that depends on `column_id == "done"` for AgentHub-native behavior.
- Introduce a computed display state based on:
  - intent status
  - latest job
  - latest attempt
  - accepted attempt
  - ship run membership
  - shipped frontier
  - dependency state
- Replace visible `Done` in swarm projects with `Ready`, `Accepted`, or `Shipped` depending on computed state.

Acceptance criteria:

- Agent completion does not semantically mean ticket shipped.
- The UI can show "attempt ready" and "shipped" as distinct states.

### 2.4 Add `compute_ticket_display_state`

Create a backend helper:

```text
compute_ticket_display_state(ticket) -> state
```

Inputs:

- ticket/intent fields
- dependency state
- latest `AgentJob`
- latest `TicketAttempt`
- accepted `TicketAttempt`
- relevant `ShipRun`
- project root/frontier

Suggested outputs:

```text
draft
blocked
queued
running
attempt_ready
accepted
stale
composed
release_pr_open
shipped
failed
archived
```

Acceptance criteria:

- Frontend no longer has to infer AgentHub-native state from raw columns.
- Dependency UI can distinguish "waiting on accepted attempt" from "waiting on shipped parent."

### 2.5 Update intent creation UX/API

- Keep existing ticket creation API.
- Add optional fields for rationale, acceptance criteria, constraints, and architecture scope.
- Update frontend create/edit forms to make intent explicit:
  - goal/title
  - why it matters
  - acceptance criteria
  - scope
  - dependencies
  - constraints
- Keep title/description as backwards-compatible fields.

Acceptance criteria:

- Newly created work items carry enough context for agents to act without Jira-style ceremony.

### 2.6 Preserve architecture scope and dependencies

- Keep `associated_node_ids` and `associated_edge_ids`.
- Keep `depends_on_ticket_ids`.
- Reinterpret them as intent planning fields:
  - architecture scope controls safe parallelism
  - dependencies define wave order and acceptable base commits
- Stop treating dependencies as only "parent ticket card is done."

Acceptance criteria:

- Dependency waves remain based on ticket/intent dependencies.
- AgentHub DAG remains the record of execution lineage.

---

## Phase 3 - Introduce TicketAttempt

Goal: replace the overloaded `prs.commit_hash` storage with a first-class model for AgentHub work.

### 3.1 Add `ticket_attempts` table

Create a migration and model.

Suggested fields:

```text
id
project_id
ticket_id
agenthub_commit_hash
base_hash
wave_num
attempt_num
agent_id
status
summary
test_status
test_output
created_at
updated_at
```

Initial statuses:

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

Acceptance criteria:

- `TicketAttempt` ORM model exists.
- Migration creates indexes on `project_id`, `ticket_id`, `agenthub_commit_hash`, `wave_num`, and `status`.

### 3.2 Add ticket-attempt serialization helpers

- Add service helpers for converting attempts to JSON.
- Include short commit hash, age, status, summary, wave number, and test status.
- Keep raw `test_output` available on detail endpoints, not necessarily list endpoints.

Acceptance criteria:

- Backend can return attempts for a ticket and for a wave.

### 3.3 Change worker completion behavior

- In swarm mode, `/tickets/:id/complete` should create a `TicketAttempt`.
- Stop writing AgentHub hashes into `PR`.
- Store summary on the attempt.
- Set `attempt_num` based on previous attempts for that ticket.
- Store `wave_num` using the current dependency-wave computation.
- Initial status should be `proposed` or `accepted` depending on whether validation is implemented yet.
- Do not treat this event as "ticket shipped."

Acceptance criteria:

- Completing a swarm ticket creates exactly one new `TicketAttempt`.
- The ticket no longer depends on `prs.commit_hash` for AgentHub output.
- Ticket display state becomes `attempt_ready` or `accepted`, not shipped.

### 3.4 Migrate existing AgentHub hashes

- Write a migration or script to convert `prs.commit_hash` rows into `ticket_attempts`.
- Preserve ticket/project links.
- Mark migrated attempts as `accepted` or `proposed` based on current ticket state.
- Leave existing `prs` data intact for rollback until deletion phase.

Acceptance criteria:

- Existing swarm test data can be converted.
- Ship/attempt APIs read migrated attempts.

### 3.5 Add ticket attempt APIs

Add:

```text
GET  /api/projects/:project_id/tickets/:ticket_id/attempts
POST /api/projects/:project_id/tickets/:ticket_id/attempts/:attempt_id/accept
POST /api/projects/:project_id/tickets/:ticket_id/attempts/:attempt_id/reject
```

Behavior:

- Accept marks the selected attempt `accepted`.
- Reject marks it `rejected` and posts feedback to AgentHub if body is supplied.
- Accepting one attempt may optionally supersede older proposed attempts for the same ticket.

Acceptance criteria:

- UI and CLI can inspect and update attempt state.
- Acceptance/rejection is independent of GitHub PRs.

### 3.6 Update every swarm reader to use `TicketAttempt`

- Update wave/merge/ship collection logic to read accepted attempts from `ticket_attempts`.
- Update ticket serialization to include latest attempt metadata.
- Update Kanban data loaders to avoid joining through `prs` for swarm projects.
- Update tests that currently inspect `prs.commit_hash`.
- Keep legacy structured mode readers pointed at `prs`.
- Add explicit helper methods instead of scattering conditional logic:
  - `get_latest_attempt(ticket_id)`
  - `get_accepted_attempt(ticket_id)`
  - `list_wave_attempts(project_id, wave_num)`
  - `list_ready_attempts(project_id)`

Acceptance criteria:

- Swarm behavior can run without reading `prs.commit_hash`.
- Structured projects still read PR data through the legacy path.
- There is a single service boundary for attempt queries.

### 3.7 Add attempt state transitions

- Define allowed transitions in one service, not in route handlers.
- Recommended initial transition graph:
  - `proposed -> validating`
  - `validating -> accepted`
  - `validating -> failed`
  - `proposed -> accepted` for temporary no-validation flow
  - `proposed -> rejected`
  - `accepted -> composed`
  - `composed -> release_pr_open`
  - `release_pr_open -> shipped`
  - `accepted -> superseded`
  - `failed -> proposed` only through a new retry attempt, not by mutating the failed attempt
- Store a short reason or event post for human-triggered transitions.

Acceptance criteria:

- Invalid transitions are rejected by backend services.
- UI actions cannot accidentally mark failed or rejected attempts as shipped.

---

## Phase 4 - AgentHub Root And Base Selection

Goal: make AgentHub leaves the working frontier and remove the persistent `swarm` branch as a future-state dependency.

### 4.1 Add project root/frontier fields

Add project-level storage:

```text
agenthub_root_hash
agenthub_root_updated_at
```

Implementation note: if current code already uses `shipped_frontier`, keep that name and treat it as the AgentHub root. Do not create two competing root fields.

Optional:

```text
agenthub_root_source
```

Possible source values:

```text
initial
release_pr_merge
manual_main_advance
repair
```

Acceptance criteria:

- Each swarm project has a recorded root hash.
- Root can be displayed in project/Ship Room UI.

### 4.2 Initialize root for existing projects

- For projects with local `project_path`, read current `main` or `master`.
- For projects with only `github_url`, fetch the default branch tip.
- Store that as `agenthub_root_hash`.
- If root cannot be determined, surface a setup warning instead of silently proceeding.

Acceptance criteria:

- New swarm projects start with a known root.
- Existing swarm projects can be initialized.

### 4.3 Replace swarm `prepare_work`

Current behavior fetches `origin/swarm` and overlays AgentHub leaves. Target behavior should select a base commit for each job.

Implement coordinator-side or agent-side base selection:

1. Single explicit dependency: start from accepted leaf for that dependency.
2. Multiple explicit dependencies: compose dependency leaves into a temporary base commit.
3. No dependency and no leaves ahead of root: start from root.
4. No dependency and leaves exist ahead of root: choose best validated leaf, using LLM only as a tiebreaker.

Acceptance criteria:

- New jobs no longer require an `origin/swarm` branch.
- Base hash is recorded on `TicketAttempt`.
- Base-selection decisions are logged.

### 4.4 Add root refresh

When main advances:

- Record new main tip as `agenthub_root_hash`.
- Update queued tickets' base planning to the new root.
- Do not mutate in-flight jobs.
- Re-evaluate tickets blocked by dependencies that have just shipped.

Acceptance criteria:

- Ship completion updates root.
- Queued tickets start from the new root.
- In-flight attempts remain traceable to their original base.

### 4.5 Remove `origin/swarm` from the future execution contract

- Audit `agent/middle_agent/git_backend.py` for `origin/swarm` assumptions.
- Keep temporary compatibility only if needed for existing tests during transition.
- Replace "checkout swarm branch" behavior with "checkout selected base hash" behavior.
- Ensure `swarm_publish` publishes a leaf to AgentHub and returns enough metadata for `TicketAttempt`.
- Ensure no worker assumes pending work is accumulated on a remote branch.
- Add logs that print:
  - project id
  - ticket id
  - selected base hash
  - root hash
  - dependency ticket ids
  - produced AgentHub commit hash

Acceptance criteria:

- New swarm jobs can run when no `origin/swarm` branch exists.
- The only persistent frontier is the AgentHub root stored by Terarchitect.

### 4.6 Handle dependency bases explicitly

- For a ticket with one dependency:
  - find the dependency ticket's accepted or shipped attempt
  - use that commit as base if dependency is not shipped
  - use current AgentHub root if dependency is already shipped into main
- For a ticket with multiple dependencies:
  - collect accepted or shipped parent attempts
  - reject dispatch if any parent has no accepted/shipped output
  - compose a temporary base from unshipped parent leaves
  - record temporary base hash and parent hashes in job metadata
- For a ticket with no dependencies:
  - use current root when no validated leaves exist ahead of root
  - if validated leaves exist, select a base deterministically first, then use LLM only for unresolved ties

Acceptance criteria:

- Dependency tickets do not rely on an implicit branch state.
- Base selection can be reproduced from DB state and AgentHub lineage.

### 4.7 Add staleness tracking

- Store the root hash used when each job starts.
- Compare attempt base/root with current project root.
- Surface stale attempts in Ship Room, but do not automatically reject them.
- Staleness should become a warning before composition and a hard error only if ancestry cannot be resolved.

Acceptance criteria:

- Users can see when an attempt was produced before `main` advanced.
- Stale but still composable attempts remain usable.

---

## Phase 5 - ShipRun And Release Composition

Goal: replace the current wave merge into `origin/swarm` with leaf selection, release-branch composition, and one release PR.

### 5.1 Rename or reinterpret `MergeRun`

Short-term:

- Keep table name `merge_runs` if renaming is too risky.
- Rename service/API language to ship run.
- Add fields needed for release PR flow.

Long-term:

- Rename table/model to `ShipRun`.

Fields to add:

```text
release_branch
release_pr_url
release_pr_number
composed_commit_hash
base_main_hash
test_status
test_output
changed_files
summary
shipped_at
shipped_commit_hash
```

Acceptance criteria:

- Ship runs can track composition, test result, release PR, and shipped commit.

### 5.2 Add ship/wave APIs

Add or replace with:

```text
GET  /api/projects/:project_id/ship/waves
GET  /api/projects/:project_id/ship/waves/:wave_num
POST /api/projects/:project_id/ship/waves/:wave_num/compose
POST /api/projects/:project_id/ship/waves/:wave_num/feedback
POST /api/projects/:project_id/ship/waves/:wave_num/ship
```

Responsibilities:

- `waves`: list wave status, attempts, accepted leaves, and ship-run status.
- `wave detail`: show selected leaves, changed files, test output, AgentHub posts.
- `compose`: create/update release branch and PR.
- `feedback`: post to AgentHub wave/ticket channel.
- `ship`: merge release PR and update root.

Acceptance criteria:

- Existing `/merge/*` routes are either adapted or superseded.
- Frontend can build Ship Room using these APIs.

### 5.3 Implement leaf selection

- Default selection: accepted attempts in the selected wave.
- Allow manual exclusion of leaves if they are independent.
- Enforce dependency ordering: if B depends on A, selected B must include A or a shipped descendant of A.
- Show dependency errors before composition.

Acceptance criteria:

- Ship Run cannot compose an invalid dependency subset.
- Independent leaves can be shipped without blocked leaves.

### 5.4 Compose release branch

Implementation outline:

1. Fetch latest `main`.
2. Checkout new branch, e.g. `terarchitect/release/wave-<n>-<short-id>`.
3. Fetch selected AgentHub leaves into local git.
4. Resolve ancestry back to `agenthub_root_hash`.
5. Merge/cherry-pick selected leaves into the release branch.
6. Run configured tests.
7. Store `composed_commit_hash`, `changed_files`, `test_status`, and `test_output`.

Conflict behavior:

- Abort branch composition.
- Mark ship run `compose_failed`.
- Store conflict details.
- Offer fix-ticket creation.

Acceptance criteria:

- A selected leaf set can produce a coherent release branch.
- Conflicts are surfaced without corrupting main or root state.

### 5.5 Open or update release PR

- Generate title, e.g. `Release wave 3: auth/session improvements`.
- Body should include:
  - selected tickets
  - selected AgentHub leaves
  - summaries
  - changed files
  - test command and result
  - root hash
  - base main hash
- Open PR from release branch to `main`.
- If PR already exists for the ship run, update branch/body instead of creating duplicates.

Acceptance criteria:

- One release PR represents the selected leaves.
- No per-ticket PRs are opened.

### 5.6 Merge release PR and update root

- Add action to merge release PR with `--no-ff`.
- After merge, fetch new main tip.
- Record `shipped_commit_hash`.
- Mark selected attempts `shipped`.
- Mark ship run `shipped`.
- Record new `agenthub_root_hash`.
- Fire root refresh.

Acceptance criteria:

- Shipping through the release PR updates Terarchitect and AgentHub root state.
- The shipped commit is auditable and revertable.

### 5.7 Add ship-worker lifecycle and locking

- Add a worker claim path for ship runs, similar to agent job claiming.
- Ensure only one active compose/ship operation can mutate a project frontier at a time.
- Use row-level locking or an equivalent project-level mutex around:
  - release branch creation
  - release PR update
  - PR merge
  - root update
- Make compose idempotent:
  - repeated compose for the same selected leaves updates the existing release branch/PR
  - changed leaf selection creates a new ship run or clearly supersedes the old one
- Record worker heartbeat and failure reason.

Acceptance criteria:

- Two users cannot simultaneously ship conflicting roots for the same project.
- Retrying a failed compose does not create duplicate release PRs unless explicitly requested.

### 5.8 Define release branch naming and cleanup

- Use deterministic branch names:

```text
terarchitect/release/wave-<wave_num>-<ship_run_short_id>
```

- Never reuse a release branch across unrelated selected leaf sets.
- Keep shipped release branches until the PR is merged and audit data is stored.
- Add optional cleanup for closed/abandoned release branches.

Acceptance criteria:

- Release branches are traceable to `ShipRun`.
- Abandoned composition branches can be cleaned without losing audit history.

### 5.9 Generate the coherent release summary

- Generate a release summary from:
  - ticket titles/descriptions
  - accepted attempt summaries
  - changed files
  - test output
  - dependency context
  - AgentHub discussion highlights
- Store the summary on `ShipRun`.
- Put the summary in the release PR body.
- Keep the summary editable or regeneratable before PR creation.

Acceptance criteria:

- The release PR reads like one coherent human review unit, not a pile of unrelated agent commits.

---

## Phase 6 - Ship Room UI

Goal: replace the PR Review page with a Ship Room that exposes AgentHub-native state and release PR workflow.

### 6.1 Replace Review list with Ship Room

Top-level sections:

- Project frontier
- Ready waves
- Running compositions
- Failed compositions
- Open release PRs
- Shipped waves

Acceptance criteria:

- User no longer sees a list of per-ticket PRs for swarm projects.
- User can understand what is ready to compose, what failed, and what shipped.

### 6.2 Add wave detail view

Show:

- tickets in wave
- accepted attempts
- rejected/failed attempts
- selected leaves
- dependency warnings
- changed files
- test output
- release PR status
- AgentHub channel timeline

Acceptance criteria:

- User can inspect the full context before opening/merging release PR.

### 6.3 Add Ship Room actions

Actions:

- Compose release
- Rerun tests
- Request changes
- Create fix ticket
- Open release PR
- Merge release PR

Acceptance criteria:

- User can complete the full flow from selected leaves to release PR to main without leaving Terarchitect.

### 6.4 Update ticket cards

For swarm projects, ticket cards should show:

- latest attempt hash
- attempt status
- wave number
- validation status
- shipped/unshipped badge
- link to AgentHub ticket channel

Remove:

- `Review` link for PR detail
- `PR #...` display

Acceptance criteria:

- Ticket cards reflect AgentHub attempts, not PRs.

### 6.5 Add frontend API types and state handling

- Add TypeScript types for:
  - `TicketAttempt`
  - `WaveSummary`
  - `WaveDetail`
  - `ShipRun`
  - `ProjectFrontier`
  - `AgentHubEvent`
- Add API utilities for `/ship/waves` and ticket-attempt endpoints.
- Represent loading, empty, failed, and stale states explicitly.
- Ensure long test output is collapsed by default with a copy/view action.
- Ensure release PR links are shown only at the ship-run level.

Acceptance criteria:

- Ship Room does not depend on PR Review API response shapes.
- Frontend compile-time types reflect the AgentHub-native model.

### 6.6 Update navigation and routing

- Replace primary `Review` nav entry with `Ship Room`.
- Keep legacy Review routes accessible only for structured projects during transition.
- Route swarm project review links to the relevant wave detail or Ship Room.
- Update empty states:
  - no ready waves
  - no attempts yet
  - composition running
  - composition failed
  - release PR open
  - shipped

Acceptance criteria:

- Swarm users land in Ship Room for human review/ship decisions.
- The old Review page is no longer the default human decision surface.

### 6.7 Add human feedback flow

- Add feedback input on wave detail.
- Allow feedback targeted to:
  - whole wave
  - specific ticket
  - specific attempt
- Post feedback to AgentHub channel.
- Create retry/fix ticket when appropriate.
- Show feedback in the event timeline.

Acceptance criteria:

- Human feedback becomes part of the AgentHub execution ledger.
- Agents can consume feedback without going through GitHub PR comments.

---

## Phase 7 - AgentHub Channels And Events

Goal: make AgentHub the execution ledger users and agents can reason from.

### 7.1 Standardize channel naming

Use:

```text
ticket-{short_ticket_id}
wave-{project_slug}-{wave_num}
project-{short_project_id}
```

Acceptance criteria:

- Every attempt has a ticket channel.
- Every release composition has a wave channel.

### 7.2 Emit structured event posts

Events:

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
- wave shipped

Acceptance criteria:

- Ship Room and ticket detail can show a coherent execution timeline.

### 7.3 Add optional AgentHub events API

If channel posts become hard to query:

- Add `/api/events` or AgentHub-side events endpoint.
- Keep event payloads generic enough for AgentHub to remain reusable.

Acceptance criteria:

- UI can efficiently load recent execution history without scraping all channels.

---

## Phase 8 - Validation And Reliability

Goal: make attempt acceptance and release composition trustworthy.

### 8.1 Attempt validation

Validation checks:

- commit hash exists in AgentHub
- commit can be fetched into local git
- base hash is known
- summary exists
- optional test command passes

Acceptance criteria:

- Invalid attempts do not become accepted by default.
- Validation failures are visible on ticket card and attempt detail.

### 8.2 Release composition validation

Validation checks:

- all selected leaves descend from root or a known accepted base
- dependency ordering is satisfied
- release branch composes cleanly
- tests pass
- release PR is open and points at expected branch/head

Acceptance criteria:

- Ship Room blocks unsafe release PR merge.

### 8.3 Failure recovery

For failures:

- Compose conflict: create fix ticket option.
- Test failure: create fix ticket option with test output.
- PR creation failure: retry action and error surface.
- PR merge failure: retry action and stale-main warning.

Acceptance criteria:

- Every failed state has a visible next action.

### 8.4 Observability and audit trail

- Add structured logs for:
  - job dispatch base selection
  - attempt publish
  - validation start/end
  - compose start/end
  - release PR create/update
  - release PR merge
  - root refresh
- Include project id, ticket id, attempt id, ship run id, wave number, root hash, base hash, and commit hash where available.
- Add backend admin/debug endpoint or CLI command to inspect:
  - current project root
  - pending leaves known to Terarchitect
  - accepted attempts by wave
  - open ship runs
  - stale attempts
- Ensure errors shown in UI have matching logs.

Acceptance criteria:

- A failed ship can be diagnosed from DB state, logs, and AgentHub events.
- Root movement is auditable.

### 8.5 Concurrency and race-condition tests

Test:

- two compose requests for the same wave
- compose while a new attempt is published
- ship while `main` has advanced externally
- accept attempt while compose is running
- reject attempt after it has been selected for a ship run
- coordinator restart during composition

Acceptance criteria:

- Race conditions either serialize safely or produce explicit retryable errors.

---

## Phase 9 - Composite Workspace / No-Main Differentiator

Goal: build the product layer that differentiates Terarchitect from GitHub-native agent tools and hosted AgentHub infrastructure. AgentHub stores the DAG; Terarchitect helps humans turn many agent-produced leaves into previewable, testable, blessable codebase states.

This phase should come after the core attempt/ship path works. Do not build no-main UI first. Composite Workspace depends on attempts, root/frontier, validation, dependency ordering, and release composition.

Phase 9 is the first no-main product surface, but it should be treated as a **lab-grade workspace** until Phase 14 adds the Verification Engine and Phase 17 adds Snapshots. A composite can be previewed and preferred here; it should not become the canonical stable product state until the later Snapshot phase exists.

### 9.1 Define composite state model

Add a model or service concept for a candidate composite:

```text
composite_workspaces
- id
- project_id
- base_root_hash
- selected_attempt_ids
- selected_leaf_hashes
- status
- composed_commit_hash
- conflict_summary
- test_status
- test_output
- preview_url
- summary
- created_by
- created_at
- updated_at
```

Suggested statuses:

```text
draft
composing
conflicted
test_failed
preview_ready
blessed
snapshot_candidate
discarded
```

Acceptance criteria:

- A selected set of AgentHub leaves can be represented as a durable candidate state.
- Composite state is separate from `ShipRun`.
- In Phase 9, a composite may still promote into a `ShipRun` for compatibility shipping.
- In the long-term model, a blessed composite becomes a Snapshot candidate first, and `ShipRun` becomes an export/promotion workflow.

### 9.2 Add leaf selection and compatibility analysis

- Let users select AgentHub leaves/attempts from:
  - a wave
  - an intent
  - the active frontier
  - a filtered set of accepted attempts
- Analyze selected leaves before composition:
  - dependency ordering
  - shared file touches
  - architecture scope overlap
  - stale base/root warning
  - missing parent leaves
  - known failed validation
- Return a compatibility report.

Acceptance criteria:

- Users can see which leaves are likely compatible before running composition.
- The system can explain why a leaf cannot be included.

### 9.3 Compose temporary workspace

- Create a temporary worktree from the current stable root.
- Fetch selected AgentHub leaves.
- Apply/merge/cherry-pick leaves in dependency order.
- Store conflict details without mutating the stable root.
- Store `composed_commit_hash` if composition succeeds.
- Keep the temporary workspace reproducible from selected leaf hashes.

Acceptance criteria:

- Users can create a no-main candidate state without opening a release PR.
- Failed composition does not corrupt project root or ship state.

### 9.4 Run tests and previews

- Run configured test command against the composite.
- Store test status and output.
- If project supports preview servers, start a preview environment.
- Store `preview_url`.
- Show changed files and diff summary against the stable root.

Acceptance criteria:

- A composite can be tested and previewed before becoming a Snapshot candidate or compatibility `ShipRun`.
- Users can compare possible app states without committing to shipping.

### 9.5 Bless composite and optionally promote through compatibility shipping

Support three conceptual actions, even if Snapshot creation lands later:

```text
Bless composite
Create Snapshot candidate
Promote through ShipRun
```

Blessing means:

- mark a composite as the preferred candidate
- optionally make future agents use its composed commit as a recommended base
- do not imply production, deployment, GitHub export, or root movement by itself

Creating a Snapshot candidate means:

- freeze the selected leaves and composed commit into the future Snapshot model
- attach evidence when Phase 14 is available
- make the candidate eligible for export/deploy policy in Phase 17

Promoting means:

- create a `ShipRun` from the composite or Snapshot candidate
- open/update one release PR if using GitHub as the compatibility boundary
- continue through normal export/ship flow
- avoid treating the `ShipRun` as the long-term stable product identity

Acceptance criteria:

- Users can mark a possible codebase state as preferred without pretending it shipped.
- A blessed composite can become a Snapshot candidate when Snapshots exist.
- Before Snapshots exist, a blessed composite can still flow into a compatibility `ShipRun`.

### 9.6 Build Composite Workspace UI

Add a `Workspace` surface, matching `UI_plan.md`.

Core sections:

- current stable root / blessed Snapshot state
- active AgentHub frontier
- leaf selector
- compatibility report
- composition status
- conflicts
- tests
- preview URL
- actions: compose, test, preview, bless, create Snapshot candidate, promote/export through ShipRun, discard

Acceptance criteria:

- The user can answer: "What happens if these leaves become one app state?"
- Composite Workspace is visibly distinct from Ship Room.

### 9.7 Keep production boundary explicit

- Do not remove the release PR path yet.
- Do not claim a composite is production unless it has become a Snapshot and passed the configured evidence/export/deploy policy.
- Keep labels clear:
  - `Composite Preview`
  - `Blessed Candidate`
  - `Snapshot Candidate`
  - `Promoted for Export`
  - `Shipped`
- Keep GitHub/main optional at the export boundary, not central to agent work.

Acceptance criteria:

- The no-main model is introduced as a Lab/Workspace product surface without sacrificing auditability.
- The UI does not imply that blessing a composite is the same as merging to production.

---

## Phase 10 - CLI And Docs

Goal: make the CLI and docs match the new product.

### 10.1 Add `ta ship`

Commands:

```text
ta ship waves <project_id>
ta ship show <project_id> <wave_num>
ta ship compose <project_id> <wave_num>
ta ship feedback <project_id> <wave_num>
ta ship merge-pr <project_id> <ship_run_id>
```

Acceptance criteria:

- CLI can perform the same core operations as Ship Room.

### 10.2 Deprecate `ta review`

- Keep temporarily for structured mode.
- Mark as legacy.
- Remove from primary CLI help once Ship Room is stable.

Acceptance criteria:

- Users are directed to `ta ship`, not `ta review`.

### 10.3 Add `ta workspace`

Commands:

```text
ta workspace leaves <project_id>
ta workspace create <project_id> --attempt <id> --attempt <id>
ta workspace show <project_id> <workspace_id>
ta workspace compose <project_id> <workspace_id>
ta workspace test <project_id> <workspace_id>
ta workspace bless <project_id> <workspace_id>
ta workspace snapshot-candidate <project_id> <workspace_id>
ta workspace promote-export <project_id> <workspace_id>
```

Acceptance criteria:

- CLI can inspect and operate the Composite Workspace without the frontend.
- CLI wording keeps blessing, Snapshot candidacy, and export/promotion separate.

### 10.4 Update docs

- README no longer says every change ships as a PR per ticket.
- Runbook explains AgentHub setup and root model.
- Integration test plan reflects release PR flow.
- Environment docs explain AgentHub and GitHub roles:
  - AgentHub for attempts.
  - ShipRun for compatibility release/export workflow.
  - Snapshot for stable blessed product state once Phase 17 lands.
  - GitHub for optional final export PR/main mirror.
- Product docs explain:
  - tickets as intents
  - attempts as agent output
  - Ship Room as production-safe release composition
  - Composite Workspace as the no-main differentiator
  - Snapshot as the long-term stable product state

Acceptance criteria:

- New docs teach the AgentHub-native model consistently.

---

## Phase 11 - Remove Legacy PR-Per-Ticket Path

Goal: remove the old product path after Ship Room and release PR flow are stable.

### 11.1 Remove PR poller

- Remove or disable PR review comment polling.
- Delete PR comment classifier if unused.
- Remove `PRReviewComment` when migrations allow.

Acceptance criteria:

- Backend no longer polls GitHub for per-ticket PR comments.

### 11.2 Remove structured finalize path from default agent flow

- Remove `gh pr create` from normal ticket finalization.
- Keep any structured code behind explicit legacy mode only during transition.
- Ensure swarm completion only creates `TicketAttempt`.

Acceptance criteria:

- No normal agent job opens a per-ticket PR.

### 11.3 Remove Review UI

- Delete or hide Review page and Review detail page for swarm projects.
- Route users to Ship Room.

Acceptance criteria:

- Main app has no PR-per-ticket review surface.

### 11.4 Clean data model

- Retire `prs` for swarm projects.
- Keep/rework PR storage only for release/export PRs created by `ShipRun` or Snapshot export.
- Rename `merge_runs` to `ship_runs` if not already done.
- Do not remove GitHub export support; remove only PR-per-ticket execution.

Acceptance criteria:

- Data model names match product concepts.
- There is no ambiguity between deprecated per-ticket PRs and supported release/export PRs.

---

## Phase 12 - Test Plan

Goal: prove the conversion end to end.

### 12.1 Unit tests

Add tests for:

- wave computation
- base selection
- dependency subset validation
- TicketAttempt state transitions
- root refresh logic
- ship-run serialization

### 12.2 Backend integration tests

Add tests for:

- swarm ticket completion creates `TicketAttempt`
- no PR row is created for swarm ticket completion
- ship wave detail lists accepted attempts
- compose endpoint rejects invalid dependency subset
- compose endpoint records conflicts
- ship endpoint updates root after PR merge in compatibility mode

### 12.3 AgentHub integration tests

Add tests for:

- attempt commit can be fetched from AgentHub
- leaf ancestry resolves back to root
- root refresh changes base for queued work
- in-flight attempts based on old root remain valid but stale

### 12.4 UI tests

Add tests for:

- Ship Room displays waves and attempts
- ticket cards show attempts, not PR numbers
- release PR status appears in wave detail
- failed composition surfaces next actions
- Intent Inbox displays computed intent/attempt/ship state
- Composite Workspace displays selected leaves, conflicts, tests, and preview state

### 12.5 End-to-end ship happy path

Scenario:

1. Create project.
2. Create dependency-linked tickets.
3. Run agents.
4. Attempts publish to AgentHub.
5. Attempts become accepted.
6. Ship Room composes selected leaves.
7. Release PR opens.
8. Release PR merges.
9. New main commit becomes AgentHub root in the pre-Snapshot compatibility model.
10. Dependent queued work starts from new root.

Acceptance criteria:

- Entire flow works without creating a per-ticket PR.
- The test clearly labels this as the compatibility release path, not the final no-main Snapshot model.

### 12.6 End-to-end Composite Workspace path

Scenario:

1. Create project.
2. Create two independent intents.
3. Run agents.
4. Attempts publish to AgentHub.
5. Attempts become accepted.
6. User selects both leaves in Composite Workspace.
7. Composite workspace composes a temporary state.
8. Tests pass.
9. Preview is available.
10. User blesses the composite.
11. User creates a Snapshot candidate when Snapshot support exists, or promotes composite to `ShipRun` in compatibility mode.
12. Release/export PR opens only if the selected policy uses GitHub export.

Acceptance criteria:

- A user can preview and bless a possible codebase state before shipping it.
- The flow does not require a persistent main branch as the work surface.
- The test distinguishes blessing, Snapshot candidacy, and GitHub export.

---

## Phase 13 - Rollout And Cutover

Goal: switch the product direction without breaking existing projects or leaving two equally supported workflows.

### 13.1 Add temporary feature flags

Suggested flags:

```text
AGENTHUB_NATIVE_ENABLED=1
ENABLE_LEGACY_STRUCTURED_MODE=1
ENABLE_LEGACY_PR_POLLER=0
ENABLE_RELEASE_PR_SHIP=1
ENABLE_COMPOSITE_WORKSPACE=0
```

Tasks:

- Gate new AgentHub-native APIs behind `AGENTHUB_NATIVE_ENABLED` only if needed for safe rollout.
- Gate Composite Workspace behind `ENABLE_COMPOSITE_WORKSPACE` until Ship Room and validation are stable.
- Gate old structured behavior behind explicit legacy flags.
- Avoid creating long-term dual-mode complexity.
- Document when each flag should be removed.

Acceptance criteria:

- Local development can opt into the new path immediately.
- Legacy structured behavior is available only intentionally.

### 13.2 Define migration checkpoints

Checkpoint A: data model ready

- migrations exist
- intent-oriented ticket fields exist or are explicitly deferred
- `TicketAttempt` exists
- project root fields exist
- old data can be backfilled

Checkpoint B: write path ready

- swarm ticket completion creates attempts
- no per-ticket PR is created in swarm mode
- worker/coordinator still complete jobs

Checkpoint C: read path ready

- Ship Room can display waves, attempts, frontier, and failures
- Intent Inbox can display computed state
- Kanban cards show attempts and shipped state
- CLI can list waves and attempts

Checkpoint D: composition ready

- accepted leaves compose into a release branch
- tests run
- release PR opens or updates
- failures are visible

Checkpoint E: ship ready

- release PR merges with `--no-ff`
- main tip becomes new AgentHub root in pre-Snapshot compatibility mode
- selected attempts become shipped
- queued dependent work starts from new root

Checkpoint F: composite workspace ready

- selected AgentHub leaves compose into a temporary candidate state
- tests run against composite
- conflicts and stale roots are visible
- composite can be blessed
- composite can be marked as a Snapshot candidate when Snapshot support exists
- composite can be promoted to `ShipRun` only as a compatibility export path

Checkpoint G: cleanup ready

- PR Review UI is hidden or removed for swarm projects
- PR poller disabled by default
- PR-per-ticket tests are either deleted or marked legacy structured

Acceptance criteria:

- Each checkpoint can be verified independently before moving to the next.

### 13.3 Backward compatibility policy

- Existing structured projects may keep working during transition.
- New projects should default to AgentHub/swarm as soon as Phase 1 lands.
- No new product features should be built for PR-per-ticket.
- Bug fixes for legacy structured mode should be limited to data safety and migration blockers.
- Once Ship Room reaches parity, remove legacy PR-per-ticket from primary code paths.
- Once Composite Workspace reaches parity, make it the primary differentiating workspace surface.

Acceptance criteria:

- The team does not spend roadmap time improving the deprecated workflow.

### 13.4 Final cutover checklist

- New project creation defaults to swarm.
- Tickets are treated as intents in UI/API copy.
- Worker completion creates `TicketAttempt`.
- Ship Room is the primary review surface.
- Composite Workspace exists behind a feature flag or primary `Workspace` nav.
- Release/export PR is created only by `ShipRun` or Snapshot export policy.
- `origin/swarm` is not required.
- AgentHub root updates after ship in compatibility mode and after Snapshot movement once Snapshots exist.
- Ticket cards distinguish ready vs shipped.
- Intent Inbox or equivalent replaces Kanban as the primary planning surface.
- Legacy Review nav is gone for swarm projects.
- Docs teach AgentHub-native workflow.
- End-to-end test covers intent to AgentHub attempt to release/export PR to main.
- End-to-end test covers selected leaves to composite preview to blessed candidate.

Acceptance criteria:

- A new user can complete the core Terarchitect loop without encountering PR-per-ticket concepts.
- This cutover removes PR-per-ticket, not GitHub export compatibility.

---

## Recommended Execution Order

1. Phase 0 - Unblock safe iteration.
2. Phase 1 - Flip defaults to AgentHub.
3. Phase 2 - Redefine tickets as intents.
4. Phase 3 - Add TicketAttempt.
5. Phase 4 - Add root/base selection.
6. Phase 5 - Add ShipRun and release PR composition.
7. Phase 6 - Build Ship Room UI.
8. Phase 7 - Add AgentHub event timeline.
9. Phase 8 - Harden validation/recovery.
10. Phase 9 - Build Composite Workspace / no-main differentiator.
11. Phase 10 - Update CLI/docs.
12. Phase 11 - Remove legacy PR-per-ticket.
13. Phase 12 - Keep tests expanding throughout; do not leave them until the end.
14. Phase 13 - Use rollout checkpoints to cut over and delete old PR-per-ticket paths.
15. Phase 14 - Add Verification Engine and evidence bundles before positioning no-main as production-grade.
16. Phase 15 - Add focused graph views for debugging, composition, and intent dependency work.
17. Phase 16 - Extend Composite Workspace to multi-repo systems.
18. Phase 17 - Add Snapshots and GitHub main compatibility so the long-term root model no longer depends on `main`.

---

## Phase 14 - Verification Engine And Evidence Bundles

Goal: make no-main / Composite Workspace trustworthy. A user should not bless or ship a possible codebase state because it "looks okay." Terarchitect should generate an evidence bundle that proves what was checked, what passed, what failed, what changed, and what risk remains.

This phase comes after the lab-grade Composite Workspace. It is the trust layer that must exist before no-main workflows are positioned as production-grade.

Core thesis:

```text
Do not trust the agents.
Trust the evidence generated around their work.
```

### 14.1 Define the evidence bundle model

Create an `evidence_bundles` model or equivalent service object.

Suggested fields:

```text
evidence_bundles
- id
- project_id
- target_type              # attempt | ship_run | composite_workspace | snapshot
- target_id
- base_hash
- candidate_hash
- selected_attempt_ids
- selected_leaf_hashes
- status                   # collecting | passed | failed | warning | incomplete
- risk_level               # low | medium | high | unknown
- summary
- created_at
- updated_at
```

Child result records:

```text
evidence_checks
- id
- evidence_bundle_id
- check_type               # llm_review | unit | integration | e2e | security | static | property | mutation | replay | visual
- status                   # passed | failed | warning | skipped
- tool_name
- command
- output
- artifact_url
- metadata
- started_at
- finished_at
```

Acceptance criteria:

- Every attempt, ShipRun, Composite Workspace, and future Snapshot can have an evidence bundle.
- Evidence results are queryable by target object and by check type.
- Evidence is stored separately from attempt/ship/composite state.

### 14.2 Add verification policy configuration

Add project-level verification policies.

Example:

```text
verification_policy
- required_checks:
  - unit
  - integration
  - static
  - security
- optional_checks:
  - e2e
  - visual
  - mutation
  - property
  - production_replay
- required_llm_reviewers:
  - security
  - architecture
  - test_adequacy
- block_on:
  - critical_security
  - failing_required_tests
  - unresolved_conflicts
  - missing_evidence
```

Acceptance criteria:

- Different projects can require different verification depth.
- Composite Workspace and Ship Room can explain why something is blocked.
- Missing evidence is treated as a real state, not as success.

### 14.3 Add layered deterministic test execution

Support deterministic checks first:

- lint/typecheck
- unit tests
- integration tests
- API/contract tests
- migration tests
- dependency vulnerability scans
- static analysis
- secret scans
- build/package checks

Acceptance criteria:

- Required deterministic checks run before LLM review results can approve a candidate.
- Failures include command, exit code, output, and artifacts.
- Results can be compared between the stable root and candidate state.

### 14.4 Add Playwright / browser evidence

For web apps, Playwright should be first-class.

Tasks:

- Detect or configure Playwright test command.
- Run Playwright against Composite Workspace preview environments.
- Store HTML report artifacts.
- Store `trace.zip` for failed tests.
- Store screenshots and videos only on failure or retry.
- Capture console errors and network failures.
- Support sharding for larger suites.
- Track flake/retry rate separately from pass/fail.

Acceptance criteria:

- A composite can prove browser flows passed against the actual preview state.
- Failed browser flows produce inspectable artifacts.
- A pass-on-retry is visible as risk, not hidden as a clean pass.

### 14.5 Add LLM review agents

Add multiple specialized review agents. They should emit structured findings, not freeform comments only.

Suggested reviewers:

```text
security_reviewer
architecture_reviewer
regression_risk_reviewer
test_adequacy_reviewer
ux_flow_reviewer
dependency_reviewer
performance_reviewer
```

Each finding should include:

```text
severity
file/path
line or symbol if available
claim
evidence
suggested_fix
blocking
confidence
```

Acceptance criteria:

- LLM review cannot override failing required tests.
- LLM review can produce warnings, blockers, or suggested follow-up intents.
- Findings can be converted into feedback or fix intents.

### 14.6 Add test generation and test adequacy checks

Agents may generate tests, but Terarchitect should check whether those tests prove anything.

Tasks:

- Generate candidate tests from intent acceptance criteria.
- Require generated tests to run as normal deterministic tests.
- Add test adequacy review:
  - do tests assert behavior or only implementation details?
  - do tests cover failure paths?
  - do tests cover the acceptance criteria?
  - did the agent weaken or delete existing tests?
- Add mutation testing for changed areas where practical.
- Add property-based tests for logic-heavy code where practical.

Acceptance criteria:

- AI-generated tests are not trusted just because they pass.
- Evidence bundle records whether tests appear meaningful.
- Weak or tautological tests produce warnings or blockers.

### 14.7 Add production-like replay and contract validation

For backend/API/service changes:

- Replay captured production or staging traffic where available.
- Validate OpenAPI or contract compatibility.
- Compare responses between the stable root and candidate state.
- Flag schema, status-code, auth, and behavioral regressions.
- Store replay diffs as evidence artifacts.

Acceptance criteria:

- API candidates can be tested against realistic traffic.
- Breaking changes are detected before blessing or shipping.

### 14.8 Add risk scoring

Compute a risk score for attempts, ShipRuns, and composite candidates.

Inputs:

- changed file count
- changed line count
- touched architecture scope
- critical paths touched
- dependency depth
- stale root distance
- test coverage/evidence completeness
- failing or warning evidence checks
- LLM review blockers
- security findings
- retry/flakiness rate
- production replay diffs

Acceptance criteria:

- Ship Room and Composite Workspace can show `low`, `medium`, `high`, or `unknown` risk.
- `unknown` risk is not treated as low risk.
- Risk score is explainable from evidence.

### 14.9 Add Evidence UI

Add an Evidence panel to:

- Attempt Detail
- Ship Room wave detail
- Composite Workspace

Display:

```text
Evidence Summary
- overall status
- risk level
- required checks
- optional checks
- LLM review findings
- test artifacts
- browser traces
- security findings
- replay diffs
- unresolved blockers
```

Acceptance criteria:

- Users can understand why Terarchitect recommends blessing, blocking, or shipping a candidate.
- Evidence can be inspected without leaving the product.

### 14.10 Add bless/ship gates

Before a composite can be blessed or promoted:

- required evidence must exist
- required checks must pass
- critical blockers must be resolved
- skipped checks must be explicitly waived
- waiver reason must be recorded

Before a ShipRun can merge or export:

- evidence bundle must be attached
- production/export-boundary policy must pass
- human approval must reference the evidence bundle

Before a Snapshot can be exported:

- snapshot evidence must exist
- required evidence checks must pass or be waived
- export policy must reference the Snapshot, not only a release branch

Acceptance criteria:

- Blessing and shipping are policy-gated.
- Waivers are visible and auditable.

### 14.11 Add evidence-driven repair loop

When evidence fails:

- create fix intent from failing check
- attach logs/artifacts to the intent
- post failure to AgentHub channel
- allow agents to produce a repair attempt
- re-run only affected evidence checks where safe

Acceptance criteria:

- Failed validation naturally feeds back into the AgentHub work loop.
- Users do not have to manually translate test failures into agent tasks.

### 14.12 Verification engine success criteria

This phase is successful when:

- Every Composite Workspace has an evidence bundle.
- Every ShipRun has an evidence bundle.
- Every Snapshot can reuse or attach an evidence bundle once Phase 17 lands.
- Browser apps can attach Playwright traces and reports.
- LLM reviews are structured and auditable.
- Deterministic tests remain the primary hard gate.
- Risk is explainable.
- Users can bless a candidate based on evidence, not vibes.

---

## Phase 15 - Focused Graph Views

Goal: add graph UIs that make AgentHub-native development understandable and debuggable. These graphs should be focused tools, not the entire application. The graph helps users understand relationships; the side panel lets users act.

Do not build a giant all-purpose graph. Build three specific new graph views:

1. AgentHub DAG Debugger
2. Composite Builder Graph
3. Intent Dependency Graph

Also migrate the existing Project Architecture Graph UI to the same graph infrastructure so all graph surfaces share rendering, interaction, and selection behavior.

### 15.1 Standardize on Cytoscape graph infrastructure

- Use Cytoscape as the shared graph rendering library.
- Use `cytoscape-cose-bilkent` as the default force/layout engine for dynamic graphs.
- Use `/Users/william.floyd/repos/graphVDB/frontend/src/components/GraphVisualization.js` as the reference implementation pattern.
- Add frontend dependencies:
  - `cytoscape`
  - `cytoscape-cose-bilkent`
  - TypeScript types if needed
- Build a reusable Terarchitect graph component:

```text
frontend/src/components/GraphView/
- GraphCanvas.tsx
- graphTypes.ts
- graphStyles.ts
- graphLayouts.ts
- graphInteractions.ts
```

- Reuse this component for:
  - AgentHub DAG Debugger
  - Composite Builder Graph
  - Intent Dependency Graph
  - Project Architecture Graph
- Requirements:
  - handles hundreds of nodes with filtering
  - custom node badges
  - custom edge styles
  - click/hover interactions
  - mini-map or viewport controls
  - deterministic layout options
  - supports lazy loading or graph windowing
- Keep graph state separate from business state.

Acceptance criteria:

- Cytoscape is installed and available in the Terarchitect frontend.
- A shared Cytoscape-based graph component exists.
- The graphVDB implementation has been reviewed and used as the reference pattern, not copied domain-specific as-is.
- Graph views can render filtered subgraphs without loading the entire project universe.

### 15.2 Migrate Project Architecture Graph to Cytoscape

Purpose:

```text
Use the same graph engine for the existing architecture graph and future AgentHub graphs.
```

Current state:

- Terarchitect's architecture graph is hand-rendered in `frontend/src/pages/GraphEditorPage.tsx`.
- It supports manual positioning and editing, which should be preserved where useful.

Tasks:

- Render project architecture nodes and edges with the shared Cytoscape component.
- Preserve existing graph data format:
  - node id
  - node type
  - node position
  - node label/description/tech/ports/security
  - edge source/target
  - edge label/protocol
- Add Cytoscape styles for architecture node types:
  - service
  - database
  - cache
  - queue
  - api
  - worker
  - view
  - frontend
- Preserve editing operations:
  - add node
  - edit node
  - delete node
  - connect nodes
  - edit edge
  - delete edge
  - save graph
- Preserve or improve manual node positioning.
- Add optional auto-layout action using `cose-bilkent`.
- Keep generated graph flow working.

Acceptance criteria:

- The existing architecture graph page works with Cytoscape.
- Existing saved graph data remains compatible.
- Users can still edit and save architecture graphs.
- Architecture graph can later share selection with Intent and AgentHub graph views.

### 15.3 Build AgentHub DAG Debugger

Purpose:

```text
Show what agents actually did.
```

Questions it should answer:

- What is each attempt based on?
- Where did divergence happen?
- Which leaves exist?
- Which leaves are accepted, rejected, stale, blessed, or shipped?
- Why did composition fail?
- Which commit should an agent build from next?

Nodes:

```text
AgentHub commit / TicketAttempt
```

Edges:

```text
parent commit
based_on
```

Node badges:

```text
intent id
attempt number
agent id
status
test result
stale
accepted
blessed
shipped
```

Filters:

```text
project
intent
wave
since current root
leaves only
accepted only
failing only
stale only
agent id
```

Side panel:

```text
commit hash
base hash
parents
children
intent
attempt number
agent id
summary
changed files
test result
evidence bundle
AgentHub posts
actions
```

Actions:

- fetch commit
- diff against root
- diff against parent
- accept attempt
- reject attempt
- open evidence bundle
- add to composite
- open AgentHub channel

Acceptance criteria:

- Developers can debug AgentHub lineage without using raw `ah` commands.
- Stale and divergent attempts are visible.
- Leaves can be inspected and selected from the graph.

### 15.4 Add AgentHub DAG backend API

Add an API that returns a filtered graph-shaped payload.

Example:

```text
GET /api/projects/:project_id/agenthub/dag
```

Query params:

```text
intent_id
wave_num
since_root=true
leaves_only=true
status
agent_id
limit
```

Response shape:

```text
{
  "root_hash": "...",
  "nodes": [
    {
      "id": "commit_hash",
      "type": "attempt",
      "attempt_id": "...",
      "intent_id": "...",
      "status": "accepted",
      "agent_id": "...",
      "short_hash": "...",
      "summary": "...",
      "test_status": "passed",
      "stale": false,
      "is_leaf": true,
      "is_blessed": false,
      "is_shipped": false
    }
  ],
  "edges": [
    {
      "source": "parent_hash",
      "target": "child_hash",
      "type": "parent"
    }
  ]
}
```

Acceptance criteria:

- Frontend can render the DAG without scraping AgentHub directly.
- Graph data joins AgentHub lineage with Terarchitect attempt/status metadata.

### 15.5 Build Composite Builder Graph

Purpose:

```text
Help users select leaves that can become a possible app state.
```

This should reuse the AgentHub DAG data but add selection and compatibility overlays.

Visual states:

```text
green  = compatible
yellow = stale or warning
red    = conflict or blocked
blue   = selected
bold   = accepted
star   = blessed
gray   = rejected/superseded
```

User flow:

1. Open Workspace.
2. View frontier since current stable root.
3. Select leaves.
4. See compatibility report update.
5. Compose temporary workspace.
6. Run evidence checks.
7. Bless, create Snapshot candidate, or promote/export through ShipRun.

Side panel:

```text
selected leaves
included intents
missing dependencies
conflicts
stale warnings
estimated risk
evidence status
actions
```

Actions:

- select leaf
- deselect leaf
- select all compatible accepted leaves
- clear selection
- compose workspace
- open evidence bundle
- bless composite
- create Snapshot candidate
- promote/export through ShipRun

Acceptance criteria:

- Users can build a candidate composite from graph-selected leaves.
- The graph explains why a leaf is blocked or risky.
- Selection flows into Composite Workspace APIs.

### 15.6 Add compatibility graph overlays

Add backend or frontend logic to annotate graph nodes with compatibility state.

Inputs:

- selected leaves
- intent dependencies
- attempt status
- root/frontier
- changed files
- architecture scope overlap
- validation/evidence state

Outputs:

```text
compatible
selected
missing_dependency
conflicts_with_selection
stale
failed_required_evidence
rejected
already_shipped
```

Acceptance criteria:

- Composite Builder Graph can show compatibility without requiring users to infer it.
- Conflict and dependency reasons are visible in the side panel.

### 15.7 Build Intent Dependency Graph

Purpose:

```text
Show why work is blocked or parallelizable.
```

Nodes:

```text
Intent / Ticket
```

Edges:

```text
depends_on
```

Node badges:

```text
intent status
latest attempt status
ship status
wave number
blocked
stale
failed
human action needed
```

Filters:

```text
selected intent neighborhood
current wave
next wave
blocked only
ready only
unshipped only
human action needed
```

Side panel:

```text
goal
rationale
acceptance criteria
dependencies
dependents
architecture scope
latest attempt
ship state
actions
```

Actions:

- run intent
- edit intent
- open attempts
- open AgentHub DAG for intent
- add to wave/composite selection
- create dependent intent

Acceptance criteria:

- Users can understand blockers and safe parallelism visually.
- The graph is focused by default, not a full-project hairball.

### 15.8 Add shared selection model across views

Selection should carry across:

- Intent Inbox
- Intent Dependency Graph
- AgentHub DAG Debugger
- Composite Builder Graph
- Ship Room
- Project Architecture Graph

Example:

- Selecting an intent in the Intent Inbox highlights it in the Intent Dependency Graph.
- Opening the AgentHub DAG from that intent filters to its attempts.
- Selecting leaves in the DAG can add them to Composite Workspace.
- Selecting a graph scope node in the Architecture Graph can filter intents that touch that architecture area.

Acceptance criteria:

- Users do not lose context when switching between table, graph, workspace, and ship views.

### 15.9 Graph UI guardrails

Rules:

- Do not make the graph the only navigation path.
- Do not show the entire project DAG by default.
- Do not require drag-and-drop for core workflows.
- Do not hide important actions inside graph node menus only.
- Always provide a table/list alternative.
- Always provide filters and a reset view action.

Acceptance criteria:

- Graph views remain useful debugging/composition tools, not decorative complexity.

---

## Phase 16 - Multi-Repo Composite Workspaces

Goal: make Terarchitect useful for real systems where one product change spans multiple repositories. This extends the no-main Composite Workspace idea from "one repo has many possible futures" to "a whole system has many possible multi-repo states."

Core thesis:

```text
GitHub sees many PRs across many repos.
Terarchitect should see one coherent system change.
```

Do not attempt fully atomic multi-repo production deploys at first. Start by coordinating repo-specific AgentHub DAGs, composing a reproducible multi-repo workspace state, validating that state, and then opening/coordinating repo-specific release/export artifacts.

### 16.1 Add workspace manifest

Add a manifest model that defines which repos belong to one Terarchitect workspace.

Example:

```yaml
workspace: billing-system
repos:
  web:
    url: github.com/acme/web
    local_path: ./web
    role: frontend
  api:
    url: github.com/acme/api
    local_path: ./api
    role: backend
  worker:
    url: github.com/acme/worker
    local_path: ./worker
    role: background_jobs
  infra:
    url: github.com/acme/infra
    local_path: ./infra
    role: infrastructure
```

Suggested DB model:

```text
workspaces
- id
- name
- description
- created_at
- updated_at

workspace_repos
- id
- workspace_id
- project_id
- repo_key
- repo_url
- local_path
- role
- agenthub_root_hash
- created_at
- updated_at
```

Acceptance criteria:

- One workspace can contain many repos/projects.
- Each repo keeps its own AgentHub root/frontier.
- Existing single-repo projects can be treated as one-repo workspaces.

### 16.2 Allow intents to span repos

Extend intent/ticket scope so one intent can target multiple repos.

Example:

```text
Intent: Add team billing

Repos:
- web
- api
- worker
- infra
```

Tasks:

- Add repo scope to intents.
- Allow architecture scope per repo.
- Allow dependencies across repo-scoped intents.
- Let an intent spawn repo-specific agent jobs.

Acceptance criteria:

- One intent can coordinate work across multiple repos.
- Each repo-specific attempt remains traceable back to the parent intent.

### 16.3 Model repo-specific attempts

Each attempt should belong to:

```text
workspace_id
project_id / repo_id
intent_id
agenthub_commit_hash
base_hash
```

Rules:

- AgentHub DAGs stay per repo.
- Attempts stay per repo.
- Intents can span repos.
- Evidence can validate across repos.

Acceptance criteria:

- A multi-repo intent can have one or more attempts per affected repo.
- Repo-specific failures do not erase the whole workspace state.

### 16.4 Add WorkspaceState

Add a new object that represents a candidate multi-repo system state.

Example:

```text
WorkspaceState: team-billing-candidate-1

web:    leaf-a
api:    leaf-b
worker: leaf-c
infra:  leaf-d
```

Suggested model:

```text
workspace_states
- id
- workspace_id
- name
- status
- selected_repo_heads
- selected_attempt_ids
- evidence_bundle_id
- preview_url
- summary
- created_at
- updated_at
```

`selected_repo_heads` example:

```json
{
  "web": "abc123",
  "api": "def456",
  "worker": "789aaa",
  "infra": "111bbb"
}
```

Acceptance criteria:

- A candidate system state can be reproduced exactly from repo keys and commit hashes.
- Composite Workspace can represent multi-repo states, not only single-repo states.

### 16.5 Materialize multi-repo composite workspace

Create a temporary filesystem layout:

```text
/workspace/team-billing-candidate-1
  /web
  /api
  /worker
  /infra
```

Tasks:

- Fetch each repo's selected AgentHub leaf.
- Checkout or materialize each repo at the selected commit.
- Apply workspace-level environment/config.
- Start services if configured.
- Record exact repo commit lockfile.

Acceptance criteria:

- Terarchitect can recreate the same multi-repo candidate state later.
- Tests and previews run against the exact selected repo commits.

### 16.6 Add multi-repo evidence bundle

Evidence should validate the whole system state, not only individual repos.

Checks:

- unit tests per repo
- integration tests across repos
- API contract tests
- DB migration tests
- Playwright full-stack tests
- worker/job tests
- infrastructure plan checks
- security and dependency scans
- production-like replay if available

Acceptance criteria:

- Evidence bundle records both per-repo and cross-repo results.
- A WorkspaceState cannot be blessed without required cross-repo evidence.

### 16.7 Add multi-repo ShipRun

Initial compatibility shipping/export model:

```text
one WorkspaceState
  -> one coordinated ShipRun
  -> one release/export PR per affected repo
  -> one shared evidence bundle
  -> one shared release checklist
```

Long-term, the `WorkspaceState` should first become a Snapshot. The coordinated `ShipRun` then becomes the export/promotion workflow for that Snapshot rather than the stable product identity.

Suggested model:

```text
ship_runs
- id
- workspace_id
- workspace_state_id
- status
- evidence_bundle_id
- summary
- created_at
- updated_at

ship_run_repos
- id
- ship_run_id
- repo_key
- project_id
- release_branch
- release_pr_url
- release_pr_number
- shipped_commit_hash
- status
```

Acceptance criteria:

- Terarchitect can coordinate multiple repo-specific PRs as one system change.
- The user can see whether each repo has opened, passed, merged, failed, or shipped.
- The shared evidence bundle remains attached to the coordinated ShipRun.
- The model remains compatible with Phase 17 Snapshots.

### 16.8 Add multi-repo Workspace UI

Add UI to show:

- workspace repos
- repo roots/frontiers
- multi-repo intents
- per-repo attempts
- candidate WorkspaceStates
- selected repo heads
- cross-repo evidence
- coordinated ShipRun status

Example table:

```text
Repo      Selected Head   Status      Evidence
web       leaf-a          ready       passed
api       leaf-b          warning     contract drift
worker    leaf-c          ready       passed
infra     leaf-d          blocked     plan review required
```

Acceptance criteria:

- Users can understand a system-level change across repos.
- The UI does not force users to inspect each repo independently.

### 16.9 Add multi-repo graph support

Extend graph views to support workspace context:

- Intent Dependency Graph can show cross-repo intents.
- AgentHub DAG Debugger can switch repo lanes.
- Composite Builder Graph can select leaves grouped by repo.

Suggested layout:

```text
repo lanes:

web:    root -> leaf-a
api:    root -> leaf-b
worker: root -> leaf-c
infra:  root -> leaf-d
```

Acceptance criteria:

- Multi-repo graph view shows repo boundaries clearly.
- Users can select one candidate head per repo for a WorkspaceState.

### 16.10 Multi-repo rollout boundaries

Keep the first version conservative:

- no claim of atomic production deploy
- no automatic merge across all repos without human approval
- no hidden partial failure
- one shared evidence bundle
- one coordinated release/export checklist
- repo-specific rollback notes

Acceptance criteria:

- Multi-repo support improves coordination without pretending distributed deploys are trivial.

### 16.11 Multi-repo success criteria

This phase is successful when:

- A workspace can contain multiple repos.
- One intent can span multiple repos.
- Agents can produce repo-specific attempts for a shared intent.
- Terarchitect can materialize a reproducible multi-repo WorkspaceState.
- Evidence can validate the full system state.
- One coordinated ShipRun can open and track repo-specific release/export PRs.
- Users can answer: "Do these changes across these repos work together?"

---

## Phase 17 - Snapshots And GitHub Main Compatibility

Goal: make the no-main model stable and compatible with existing GitHub-based teams. Terarchitect should function independently of GitHub internally, while still being able to export proven snapshots to GitHub `main` and import external human changes from `main`.

Core thesis:

```text
AgentHub DAG = work universe
Composite Workspace = possible app state
Evidence Bundle = proof
Snapshot = stable product state
GitHub main = compatibility mirror/export target
```

This keeps Terarchitect agent-native internally while allowing teams to keep existing CI, deployment, audit, and human hotfix workflows around GitHub `main`.

### 17.1 Add Snapshot as a first-class model

Add a model that freezes a proven state.

Suggested model:

```text
snapshots
- id
- workspace_id
- project_id
- composite_workspace_id
- workspace_state_id
- ship_run_id
- evidence_bundle_id
- status
- source
- snapshot_lockfile
- artifact_refs
- github_export_status
- github_export_branch
- github_pr_url
- github_pr_number
- github_main_commit
- created_by
- created_at
- blessed_at
- exported_at
- deployed_at
- superseded_at
- rolled_back_from_id
```

Suggested statuses:

```text
candidate
blessed
exporting
exported
deployed
superseded
rolled_back
stale
failed
```

Suggested sources:

```text
composite_workspace
ship_run
github_main_import
manual_import
rollback
```

Acceptance criteria:

- Terarchitect can represent a stable product state without relying on GitHub `main`.
- A snapshot can reference a single repo or a multi-repo `WorkspaceState`.
- Snapshot state is auditable and reproducible.

### 17.2 Define snapshot lockfile format

Create a lockfile that records exactly what code/artifacts belong to a snapshot.

Single-repo example:

```json
{
  "snapshot_id": "snap_42",
  "repos": {
    "app": "abc123"
  },
  "evidence_bundle_id": "ev_456",
  "created_from": "composite_workspace:cw_123"
}
```

Multi-repo example:

```json
{
  "snapshot_id": "snap_99",
  "repos": {
    "web": "abc123",
    "api": "def456",
    "worker": "789aaa",
    "infra": "111bbb"
  },
  "artifacts": {
    "web_image": "registry/app-web:sha",
    "api_image": "registry/app-api:sha"
  },
  "evidence_bundle_id": "ev_999",
  "created_from": "workspace_state:ws_555"
}
```

Acceptance criteria:

- Any snapshot can be recreated from its lockfile.
- The lockfile can be stored in Terarchitect and optionally committed/exported to GitHub.

### 17.3 Create snapshots from blessed composites

When a Composite Workspace passes required evidence and is blessed:

- freeze selected leaves into a snapshot
- attach evidence bundle
- attach generated summary
- record lockfile
- mark previous blessed snapshot superseded if appropriate
- make snapshot available for export/deploy

Acceptance criteria:

- Blessing a composite can produce a stable snapshot.
- Production deploys can target snapshots rather than raw DAG leaves.

### 17.4 Export snapshot to GitHub main

Support GitHub as a compatibility export target.

Export modes:

```text
pr_export
direct_export
no_export
```

Recommended default:

```text
pr_export
```

PR export flow:

```text
snapshot
  -> materialize selected state
  -> create release/export branch
  -> open GitHub PR to main
  -> attach evidence summary
  -> merge when approved
  -> record github_main_commit
```

Direct export flow:

```text
snapshot
  -> materialize selected state
  -> push/merge to main according to policy
  -> record github_main_commit
```

Acceptance criteria:

- A snapshot can be mirrored to GitHub `main`.
- Export is optional and policy-controlled.
- GitHub PR/main is an export artifact, not the agent work substrate.

### 17.5 Import external GitHub main changes

Humans may still push or merge code directly to GitHub `main`.

When GitHub `main` advances outside Terarchitect:

- detect the new main tip
- create a snapshot with `source = github_main_import`
- mark evidence as `unknown` or `manual`
- update AgentHub root/frontier policy
- mark existing composites stale if based on older root
- notify users in Workspace/Ship UI

Acceptance criteria:

- Human changes to `main` do not break Terarchitect's model.
- External changes become explicit snapshots instead of invisible drift.
- Users can see which candidate states need revalidation.

### 17.6 Update root/frontier semantics

Refine and supersede the Phase 4 compatibility root rule:

```text
AgentHub root = latest blessed/exported/imported snapshot
```

Not strictly:

```text
AgentHub root = GitHub main
```

Rules:

- If a snapshot is exported to GitHub `main`, that main commit can become the root.
- If a human updates GitHub `main`, imported snapshot can become the root after acknowledgement/policy.
- If Terarchitect runs without GitHub export, the blessed snapshot remains the internal root.
- In-flight attempts keep their original base; staleness is handled by evidence/composition.

Acceptance criteria:

- Terarchitect can run independently of GitHub.
- GitHub main can still stay compatible with the latest exported snapshot.
- Root movement is tied to snapshots, not raw branch assumptions.

### 17.7 Add Snapshot UI

Add a Snapshot view or section in Workspace/Ship.

Show:

- current blessed snapshot
- current exported GitHub main snapshot
- imported external snapshots
- snapshot lockfile
- evidence status
- export status
- deployment status
- rollback target
- stale composites caused by snapshot movement

Actions:

- create snapshot from blessed composite
- export snapshot to GitHub
- import latest GitHub main
- compare snapshots
- rollback to prior snapshot
- mark snapshot deployed

Acceptance criteria:

- Users can understand stable product states separately from active AgentHub leaves.
- GitHub export/import state is visible.

### 17.8 Add snapshot diff and rollback

Support:

- snapshot-to-snapshot diff
- snapshot-to-current-frontier diff
- snapshot-to-GitHub-main diff
- rollback by selecting a previous snapshot

Rollback should:

- select prior lockfile/artifacts
- optionally export rollback snapshot to GitHub
- preserve audit trail
- never delete newer AgentHub work

Acceptance criteria:

- Users can recover to a known stable state without rewriting the AgentHub DAG.
- Rollback is a new snapshot event, not destructive history editing.

### 17.9 Snapshot export evidence requirements

Before export to GitHub `main`:

- snapshot must have evidence bundle
- required evidence checks must pass or be waived
- waivers must include reason and actor
- export PR/direct export must include evidence summary

Acceptance criteria:

- GitHub main only receives states that were explicitly blessed/exported.
- Evidence remains attached to the exported state.

### 17.10 Snapshot success criteria

This phase is successful when:

- Terarchitect can bless a Composite Workspace into a stable snapshot.
- Production/deployment can target snapshots.
- GitHub `main` can mirror a snapshot.
- External GitHub `main` changes can be imported as snapshots.
- AgentHub root/frontier can be derived from snapshots.
- Rollback selects an older snapshot instead of rewriting DAG history.
- Users can understand the difference between active leaves, candidate composites, snapshots, and production state.
