# Ticket Redefinition

## Purpose

This note captures a future rethink of what a "ticket" should mean in Terarchitect after the AgentHub conversion.

Short version: tickets are still valuable, but they should stop being miniature project-management objects. In an AgentHub-native Terarchitect, a ticket should become a lightweight **intent object** that tells agents what matters, while AgentHub attempts record what actually happened.

The future shape should be:

```text
Intent/Ticket -> AgentHub Attempts -> Accepted Leaf -> ShipRun -> Shipped
```

Tickets should answer:

- What should change?
- Why does it matter?
- What context should agents use?
- What dependencies constrain the work?
- What architecture scope is involved?
- What does success look like?

Tickets should not be responsible for:

- representing every execution state
- pretending an agent attempt is "done"
- owning a GitHub PR
- being the unit humans review
- storing the result of agent work
- encoding all workflow logic through Kanban columns

---

## Current Ticket Model

Current database model: `backend/models/db.py:60`

```text
tickets
- id
- project_id
- column_id
- title
- description
- associated_node_ids
- associated_edge_ids
- priority
- status
- failed_count
- depends_on_ticket_ids
- created_at
- updated_at
```

Current relationships:

```text
Ticket -> TicketComment
Ticket -> ExecutionLog
Ticket -> PR
Ticket -> AgentJob
Ticket -> TicketAttempt
```

This is already halfway to the right future. The ticket owns planning context, graph scope, dependencies, logs, jobs, and attempts. The problem is not that tickets exist. The problem is that `column_id` and `status` are currently overloaded as the product state machine.

Today, the ticket tries to mean all of these at once:

- user intent
- Kanban card
- execution request
- dependency node
- architecture scope marker
- job status proxy
- review status proxy
- shipped/done proxy

That is too much.

---

## Code Review: What Tickets Do Today

### 1. Tickets are still driven by Kanban columns

The primary state field is `column_id`, not a domain lifecycle.

Examples:

- `backend/models/db.py:65` stores `column_id` directly on the ticket.
- `frontend/src/pages/KanbanPage.tsx:64` defines `backlog`, `queued`, `in_progress`, and `done`.
- `backend/api/routes.py:394` treats moving to `in_progress` as the signal to enqueue an agent job.
- `backend/api/routes.py:464` treats moving to `done` as the signal to dispatch dependent tickets.
- `backend/api/services/ticket_service.py:84` blocks dependencies until dependent tickets have `column_id == "done"`.

This creates a mismatch with AgentHub. In AgentHub-native mode, a ticket can have an attempt, an accepted attempt, a composed release, and a shipped result. All of those are different states. A single `done` column cannot represent them honestly.

Future direction:

- Keep Kanban columns as a view preference.
- Move real lifecycle into explicit fields or computed state.
- Do not use `done` to mean both "agent produced output" and "work reached main."

### 2. `status` exists, but it is weakly defined

The ticket has both `column_id` and `status`.

Current code:

- `backend/models/db.py:71` defines `status`, defaulting to `todo`.
- `backend/api/routes.py:449` lets callers update `status` directly.
- `backend/api/routes.py:561` sets `ticket.status = "completed"` when a worker completes.

This means `status` is not a strict lifecycle. It is partly redundant with `column_id`, partly worker state, and partly user-editable.

Future direction:

- Either remove `Ticket.status` from the core lifecycle or redefine it sharply.
- If retained, make it an intent lifecycle, not execution state.
- Suggested values:

```text
draft
ready
queued
running
attempt_ready
accepted
blocked
shipped
archived
```

Better long-term option:

- `Ticket.intent_status`: draft | ready | active | blocked | archived
- `Ticket.execution_state`: computed from jobs and attempts
- `Ticket.ship_state`: computed from attempts and ship runs

### 3. Completion currently conflates attempt production with done

Current worker completion:

- `backend/api/routes.py:547` handles `/tickets/:ticket_id/complete`.
- `backend/api/routes.py:561` sets `ticket.status = "completed"`.
- `backend/api/routes.py:562` sets `ticket.column_id = "done"`.
- `backend/api/routes.py:569` creates a `TicketAttempt` in swarm mode.

The creation of `TicketAttempt` is the right direction. The remaining problem is that the ticket is immediately moved to `done`, even though the work has not necessarily been composed into a release branch or shipped to `main`.

Future direction:

```text
worker completes -> create TicketAttempt
TicketAttempt proposed/accepted -> ticket appears Ready
ShipRun composes selected attempt -> ticket appears Composed
release PR merges -> ticket appears Shipped
```

In other words:

- Agent completion should create an attempt.
- Attempt acceptance should make the ticket ready.
- Ship completion should make the ticket shipped.

### 4. Dependencies are valuable, but their current condition is too coarse

Dependencies are currently stored as `depends_on_ticket_ids`.

Current code:

- `backend/models/db.py:73` stores dependency ids.
- `backend/api/services/merge_service.py:7` computes waves from dependencies.
- `backend/api/services/ticket_service.py:84` blocks enqueue until dependencies are `done`.
- `frontend/src/pages/KanbanPage.tsx:730` shows blocked state based on dependency tickets being `done`.

The dependency graph is one of the strongest reasons to keep tickets. It gives Terarchitect a planning DAG separate from the AgentHub commit DAG.

But "dependency is done" should become more precise:

```text
dependency accepted
dependency composed
dependency shipped
```

Different situations need different gates:

- A child attempt can often start from a parent's accepted AgentHub leaf before the parent ships.
- A release can only ship if dependency ordering is satisfied.
- A user-facing "ready to ship" view should distinguish accepted-but-unshipped parents from shipped parents.

Future direction:

- Keep ticket dependencies.
- Stop using `column_id == "done"` as the dependency condition.
- Make dependency gates explicit:

```text
dispatch_gate: dependency has accepted attempt or shipped result
ship_gate: selected leaves include required parent leaves, or parent is already shipped
ui_gate: show whether dependency is waiting, accepted, composed, or shipped
```

### 5. Architecture scope is a real differentiator

Tickets carry:

- `associated_node_ids`
- `associated_edge_ids`

Current code:

- `backend/models/db.py:68` and `backend/models/db.py:69` store architecture associations.
- `backend/api/services/job_service.py:5` uses associated nodes/edges to avoid conflicting swarm jobs.
- `backend/api/services/job_service.py:22` claims jobs only when graph scope does not conflict.
- `frontend/src/pages/KanbanPage.tsx` lets users assign graph nodes/edges on create/edit.

This is good. It makes tickets more than a task list. They become scoped intents tied to architecture.

Future direction:

- Preserve this field as part of the intent model.
- Add richer scope semantics later:

```text
scope_nodes
scope_edges
scope_conflict_policy: exclusive | shared | advisory
scope_confidence: human | inferred | agent_suggested
```

### 6. TicketAttempt is the right split

The current code already introduces `TicketAttempt`.

Current code:

- `backend/models/db.py:152` defines `TicketAttempt`.
- `backend/api/services/attempt_service.py` defines attempt transitions and serialization.
- `backend/api/services/ticket_service.py:30` surfaces latest attempt metadata on tickets.
- `backend/api/routes.py:598` exposes ticket attempts through an API.

This is the key architectural separation:

```text
Ticket = intent
TicketAttempt = execution output
```

Future direction:

- Continue moving execution details out of tickets and into attempts.
- Ticket cards should summarize attempts, not own attempt state.
- Acceptance/rejection should happen on attempts, not tickets.

### 7. The frontend still thinks in PR-era / done-era terms

Current code:

- `frontend/src/utils/api.ts:56` defines the `Ticket` type but does not yet include `latest_attempt`.
- `frontend/src/pages/KanbanPage.tsx:68` labels the final column `Done`.
- `frontend/src/pages/KanbanPage.tsx:730` considers dependencies blocked unless parent ticket is `done`.
- `frontend/src/pages/KanbanPage.tsx:1438` still renders PR review links when `ticket.pr_url` exists.

Future direction:

- Add typed `latest_attempt` to the frontend `Ticket` interface.
- Replace visible `Done` in swarm projects with `Ready` and `Shipped` concepts.
- Show release PR links only at `ShipRun`, not ticket.
- Use ticket card language like:

```text
No attempts
Running
Attempt ready
Accepted
Stale
Composed
Shipped
Failed
```

### 8. Current route has a likely bug during completion

In `backend/api/routes.py:581` and `backend/api/routes.py:589`, `ticket_complete` checks `if git_mode == "swarm":`, but the shown function does not define `git_mode` before using it.

The route does fetch `project` at `backend/api/routes.py:565`, so it likely meant:

```python
git_mode = getattr(project, "git_mode", None) or "swarm"
```

Why this matters for ticket redefinition:

- The current workflow is still in transition.
- Ticket completion is now an especially sensitive boundary because it creates attempts and triggers downstream work.
- This route should become "attempt creation" rather than "ticket is done."

---

## Are Tickets Still Valuable?

Yes. But their value is not "humans need a Jira-shaped card."

Their value is that AgentHub commits alone do not explain intent.

AgentHub can tell us:

- which commit happened
- what it descended from
- which leaves exist
- what messages were posted
- what attempts agents made

AgentHub does not inherently tell us:

- why the work matters
- which user need it serves
- which architecture boundary it targets
- what dependencies should constrain it
- what success means
- which attempts are acceptable for this goal
- how this work fits a larger product plan

That missing layer is still needed. Today it is called a ticket.

The future ticket should be:

```text
an intent object that anchors agent work to product, architecture, and acceptance context
```

---

## Proposed Future Definition

### Ticket as Intent

Rename mentally first, maybe in code later:

```text
Ticket = Intent
```

Possible future names:

- Intent
- Work Item
- Change Request
- Goal
- Mission

Recommendation:

- Keep the word `ticket` in the UI until the AgentHub workflow stabilizes.
- Internally design it as `Intent`.
- Consider renaming after Ship Room and attempt tracking are mature.

### Future fields

Future `tickets` table could evolve toward:

```text
tickets
- id
- project_id
- title
- goal
- rationale
- acceptance_criteria
- constraints
- priority
- value_score
- risk_level
- intent_status
- scope_node_ids
- scope_edge_ids
- scope_conflict_policy
- depends_on_ticket_ids
- parent_ticket_id
- created_by
- created_source
- created_at
- updated_at
```

Fields to demote or remove from core meaning:

```text
column_id
status
failed_count
```

They can remain during migration, but should not be the source of truth for execution or shipping.

### Future computed state

Instead of storing one ambiguous ticket status, compute multiple states:

```text
intent_state
- draft
- ready
- active
- blocked
- archived

execution_state
- not_started
- queued
- running
- attempt_ready
- failed

attempt_state
- no_attempts
- proposed
- validating
- accepted
- rejected
- superseded

ship_state
- unshipped
- composed
- release_pr_open
- shipped
```

The UI can collapse these into simple labels, but the backend should not pretend they are the same thing.

### Future relationships

```text
Ticket/Intent
  has many TicketAttempts
  has many AgentJobs
  has many ExecutionLogs
  has many AgentHub channel events
  belongs to dependency wave
  participates in ShipRuns through selected attempts
```

The release PR should relate to `ShipRun`, not directly to each ticket.

---

## AgentHub-Native Ticket Lifecycle

Target lifecycle:

```text
draft
  -> ready
  -> queued
  -> running
  -> attempt_ready
  -> accepted
  -> composed
  -> release_pr_open
  -> shipped
```

Failure paths:

```text
running -> failed
attempt_ready -> rejected
accepted -> superseded
composed -> compose_failed
release_pr_open -> ship_failed
```

But this should not all live on the ticket row.

Suggested ownership:

```text
Ticket.intent_status
  draft | ready | active | blocked | archived

AgentJob.status
  pending | running | completed | failed | canceled

TicketAttempt.status
  proposed | validating | accepted | rejected | superseded | composed | release_pr_open | shipped | failed

ShipRun.status
  queued | composing | compose_failed | ready_for_pr | release_pr_open | shipping | shipped | failed
```

The ticket view can then show one synthesized label:

```text
Ticket display state = computed from ticket + latest job + latest attempt + ship run
```

---

## How Tickets Should Work With AgentHub

### Ticket creation

Tickets should be created from:

- human description
- AI-generated backlog from graph
- failed attempts
- failed release compositions
- AgentHub channel discussion
- root refresh / stale attempt analysis

Each created ticket should include:

- goal
- rationale
- acceptance criteria
- graph scope
- dependencies
- constraints

### Ticket dispatch

Dispatch should not mean "move a card to In Progress."

Dispatch should mean:

```text
create AgentJob from a ready intent
select base hash
attach AgentHub ticket channel
send context to worker
```

The job payload should include:

- ticket id
- goal
- acceptance criteria
- architecture scope
- dependencies
- AgentHub root
- selected base hash
- relevant parent attempt hashes
- channel ids

### Attempt creation

When an agent finishes:

```text
create TicketAttempt
post attempt event to AgentHub
run validation if configured
surface attempt on ticket
do not mark ticket shipped
```

### Acceptance

Human or policy acceptance should happen on the attempt:

```text
TicketAttempt proposed -> accepted
```

The ticket can show "Accepted" or "Ready", but the accepted object is the attempt.

### Shipping

Shipping should happen through `ShipRun`:

```text
accepted attempts -> selected leaves -> release branch -> release PR -> main
```

Only after the release PR merges should the ticket show "Shipped."

---

## Future UI Model

The board should stop being the lifecycle source of truth.

Better views:

### Intent Board

Shows:

- draft
- ready
- queued/running
- needs human
- shipped/recently shipped

This remains useful for humans planning work.

### Attempt View

Shows:

- attempts per ticket
- attempt status
- base hash
- AgentHub commit hash
- test result
- stale warning
- accept/reject actions

### Ship Room

Shows:

- waves
- accepted attempts
- selected leaves
- release composition status
- release PR status
- shipped state

### AgentHub Timeline

Shows:

- ticket channel
- agent plans
- attempt summaries
- validation events
- feedback
- release events

---

## Migration Plan

### Step 1: Keep `Ticket`, redefine semantics

- Keep table name `tickets`.
- Keep UI word "ticket."
- Document that tickets are intent objects.
- Stop adding new execution semantics to ticket columns.

### Step 2: Move execution state outward

- Use `AgentJob` for running/pending state.
- Use `TicketAttempt` for produced work.
- Use `ShipRun` for release state.
- Keep `failed_count` only as a summary cache, if needed.

### Step 3: Replace `done`

In swarm projects, replace visible `Done` with:

```text
Ready
Shipped
```

Or split board sections:

```text
Backlog | Queued | Running | Ready
```

and put shipped work in a separate history/recently shipped area.

### Step 4: Add acceptance criteria

Add structured fields:

```text
acceptance_criteria
constraints
rationale
```

These should be passed to agents and shown when evaluating attempts.

### Step 5: Compute display state

Add a backend helper:

```text
compute_ticket_display_state(ticket)
```

Inputs:

- ticket intent status
- latest job
- latest attempt
- accepted attempt
- ship run membership
- shipped frontier
- dependency state

Outputs:

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
```

### Step 6: Consider rename only after product stabilizes

Do not rename tickets immediately. It would create churn while AgentHub conversion is still underway.

Possible later path:

```text
Ticket table remains `tickets`
UI copy changes from Ticket to Intent in selected places
API v2 exposes `/intents`
Legacy `/tickets` remains as alias
```

---

## Design Principles

1. **Tickets define intent.**
   AgentHub records execution.

2. **Attempts are the unit of agent output.**
   A ticket can have many attempts.

3. **ShipRuns are the unit of human release review.**
   A release PR belongs to a ShipRun, not to a ticket.

4. **Dependencies belong to intent.**
   Commit lineage belongs to AgentHub.

5. **Kanban is a view.**
   It should not be the source of execution truth.

6. **Done is not shipped.**
   Avoid this ambiguity everywhere in swarm mode.

7. **Human feedback should attach to the intent or attempt.**
   Not to a per-ticket PR.

8. **The ticket should get smaller.**
   It should carry less process and more meaning.

---

## Open Questions

1. Should the user-facing word remain `ticket`, or should the product eventually say `intent`?
2. Should acceptance criteria be structured text, checklist items, or generated tests?
3. Can one ticket have multiple accepted attempts?
4. Should dependencies gate dispatch on accepted attempts or only shipped attempts?
5. Should tickets belong to explicit persisted waves, or should waves remain computed?
6. Should failed release composition create a new ticket or attach feedback to existing tickets?
7. Should AgentHub eventually store intent metadata directly, or should Terarchitect remain the intent owner?

---

## Recommendation

Keep tickets, but redefine them.

Do not delete the concept. Delete the Jira-shaped assumptions.

The future ticket should be the smallest durable object that captures human/product intent and gives agents enough context to act. Everything else should move to AgentHub attempts, jobs, events, and ship runs.

The strongest future architecture is:

```text
Terarchitect Ticket = intent, scope, dependency, acceptance
AgentHub Attempt = execution, lineage, discussion, validation
ShipRun = release composition, final PR, shipped root
```

That gives Terarchitect a clean division of responsibility and keeps the product understandable as agent output volume grows.
