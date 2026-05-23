# UI Plan

## Purpose

This document captures the UI direction for Terarchitect as it moves from a Kanban/PR product to an AgentHub-native product.

The main shift:

```text
Old UI:
Kanban cards -> agent runs -> PR review -> done

New UI:
Intents -> AgentHub attempts -> composite/release selection -> Ship Room -> shipped state
```

Kanban was useful scaffolding, but it should not remain the core mental model. AgentHub-native work is not a single card moving left to right. One intent can produce many attempts, attempts can branch, valid leaves can coexist, shipping is separate from agent output, and future "no-main" workflows may involve previewing temporary composite states.

The UI should make those distinctions obvious.

---

## Core Product Objects

The UI should be organized around four concepts:

```text
Intent
Attempt
Workspace
Ship
```

### Intent

What the human or system wants.

An intent answers:

- what should change
- why it matters
- what acceptance criteria define success
- what architecture scope is involved
- what dependencies constrain it
- what context agents should use

This may still be called "Ticket" in the UI at first, but the product should treat it as intent.

### Attempt

What an agent tried.

An attempt answers:

- which agent worked on it
- which AgentHub commit was produced
- what base hash it started from
- what changed
- what tests passed or failed
- whether it was accepted, rejected, superseded, composed, or shipped

### Workspace

A selected possible state of the app.

In the current plan, this is mostly a release-branch composition surface. In the future no-main model, this becomes a Composite Workspace where users can select AgentHub leaves and preview/test that possible reality before deciding what to ship.

### Ship

What becomes real.

In the current plan, shipping means:

```text
selected accepted leaves -> coherent release branch -> one release PR -> merge to main -> new AgentHub root
```

In a future no-main model, shipping may mean blessing a composite or promoting it to a release artifact. Even then, production still needs an auditable shipped state.

---

## Why Kanban Stops Fitting

Kanban assumes:

```text
one card
one state
one left-to-right flow
done means done
```

AgentHub-native work behaves differently:

```text
one intent can have many attempts
attempts can diverge
multiple leaves can be valid at once
an agent-produced commit is not shipped code
human review happens at release composition, not every attempt
main/root is a moving shipped boundary
```

So a classic board creates misleading language:

- `Done` can mean "agent produced a commit" even though nothing shipped.
- `In Review` implies PR review, which is no longer the primary flow.
- `Blocked` may mean dependency not accepted, dependency not shipped, conflict in composition, stale root, or failed tests.
- `Card moved to final column` hides attempt/release state.

Kanban can remain as an optional familiar view, but not the primary UI.

---

## Current AgentHub Plan UI

For the current conversion plan, the core UI should have three primary surfaces:

```text
Intent Inbox -> Attempt Detail -> Ship Room
```

### 1. Intent Inbox

This replaces the Kanban board as the main planning surface.

It should be a dense table/list, not columns of cards.

Example:

```text
Intent                         Scope       Deps       Attempts   State       Ship
Add login sessions             Auth        none       3          Accepted    Wave 2
Add billing webhook            Billing     login      1          Running     Blocked
Refactor settings page         UI          none       2          Ready       Wave 2
```

Useful columns:

- intent title
- architecture scope
- dependencies
- priority/value
- latest attempt
- validation result
- ship readiness
- human action needed
- wave
- stale warning

Primary actions:

- create intent
- edit intent
- run intent
- view attempts
- accept/reject latest attempt
- open in AgentHub
- send feedback
- add to candidate ship set

The Intent Inbox should answer:

```text
What does the system want to accomplish, and what needs human attention?
```

### 2. Attempt Detail

Clicking an intent opens its attempt history.

Example:

```text
Intent: Add login sessions

Attempt 1    failed tests       commit a1b2c3
Attempt 2    rejected           commit d4e5f6
Attempt 3    accepted           commit f7g8h9
```

Attempt detail should show:

- AgentHub commit hash
- base hash
- attempt number
- agent id
- status
- summary
- changed files
- test result
- logs
- stale warning
- AgentHub channel posts
- accept/reject/supersede actions

This view should answer:

```text
What did agents try, and which attempt is worth using?
```

### 3. Ship Room

Ship Room is the main human decision surface.

Example:

```text
Wave 2 ready to compose

Selected attempts:
- Add login sessions       attempt 3   tests passed
- Refactor settings page   attempt 2   tests passed

Warnings:
- Billing webhook blocked by login dependency

Actions:
[Compose Release] [Run Tests] [Open Release PR] [Ship]
```

Ship Room should show:

- project frontier / shipped root
- ready waves
- accepted attempts
- selected leaves
- dependency warnings
- stale attempts
- changed files
- generated release summary
- test output
- composition conflicts
- release PR status
- shipped history

Primary actions:

- select/deselect leaves
- compose release
- rerun tests
- request changes
- create fix intent
- open/update release PR
- merge release PR

Ship Room should answer:

```text
Which accepted attempts can safely become real?
```

---

## Future No-Main / Composite Workspace UI

The no-main idea should not replace the current shipping plan immediately. It should become a future **Composite Workspace** or **AgentHub Lab** mode.

The future flow:

```text
AgentHub attempts -> virtual composite workspace -> preview/test/explore -> optionally promote to ShipRun
```

### Frontier View

This view shows the current AgentHub frontier.

Example:

```text
Current blessed root: abc123

Leaves:
- f91a22  login attempt 3       valid
- c81b77  settings refactor     valid
- a19d02  billing webhook       failing
- ee8310  alternate auth model  untested
```

It should answer:

```text
What possible futures exist right now?
```

Useful filters:

- accepted only
- failing only
- stale only
- by intent
- by wave
- by agent
- by architecture scope
- since last shipped root

### Composite Workspace

This is the main no-main surface.

The user selects leaves:

```text
[x] login attempt 3
[x] settings refactor
[ ] billing webhook
[x] dark mode attempt 1
```

Terarchitect creates a temporary composite state:

```text
Composite Preview
Base: abc123
Leaves: 3
Conflicts: none
Tests: passing
Preview URL: localhost:xxxx
```

Actions:

- run composite
- preview app
- run tests
- compare to shipped root
- save as candidate
- promote to ShipRun
- bless composite

This view should answer:

```text
What happens if these AgentHub leaves become one app state?
```

### Blessed State

The no-main model should avoid scary language in the UI.

Prefer:

```text
Blessed state
Candidate state
Composite preview
Shipped release
```

Avoid making production sound like it has no stable identity.

Recommended framing:

```text
Lab state: composite
Production state: shipped release
```

Production still needs:

- exact artifact identity
- rollback point
- audit trail
- deployment reference
- support/debug reference

---

## Graph UI

Graph UIs can work here, but only if the graph is not the entire app.

Rule:

```text
The graph helps users understand relationships.
The side panel lets users act.
```

Do not make users manage the whole product by dragging graph nodes around.

### Graph View 1: Intent Dependency Graph

Purpose:

```text
Show why work is blocked or parallelizable.
```

Nodes:

```text
Intent
```

Edges:

```text
depends_on
```

Good for:

- seeing dependency waves
- understanding blockers
- seeing safe parallelism
- picking what to run next

Bad for:

- editing intent descriptions
- reading logs
- reviewing code
- replacing Ship Room

### Graph View 2: AgentHub Attempt DAG

Purpose:

```text
Show what agents actually tried.
```

Nodes:

```text
AgentHub commit / attempt
```

Edges:

```text
based_on / parent commit
```

Good for:

- seeing divergence
- identifying leaves
- selecting leaves for a composite workspace
- seeing stale branches
- understanding lineage

Bad for:

- showing every commit forever
- normal project management
- replacing the Intent Inbox

This view must be filtered aggressively:

- current root to active leaves only
- selected intent only
- selected wave only
- last 24 hours
- validated attempts only
- failing branches only
- stale attempts only

### Graph View 3: Architecture Scope Graph

Purpose:

```text
Show what parts of the system an intent touches.
```

Nodes:

```text
AuthService
User table
Login UI
Billing API
```

Edges:

```text
calls / depends on / owns data / interacts with
```

Good for:

- assigning scope
- detecting conflicts
- explaining why two intents cannot run in parallel
- giving agents context

Bad for:

- making shipping decisions directly
- tracking attempt history

### Suggested Graph Layout

Use a layout like:

```text
Left: object list / filters
Center: focused graph
Right: detail panel
Bottom: attempts / ship status
```

Modes:

```text
[Intent Graph] [Attempt DAG] [Architecture Scope]
```

Selection should carry across modes.

If the user selects `Add login`:

- Intent Graph shows nearby dependencies.
- Attempt DAG shows attempts for that intent.
- Architecture Scope shows affected components.
- Right panel shows goal, acceptance criteria, attempts, and actions.

---

## Long-Term App Navigation

Recommended primary tabs:

```text
Intents
Attempts
Workspace
Ship
```

### Intents

What we want.

Main view:

- dense intent list
- dependency state
- architecture scope
- latest attempt summary
- human action needed

### Attempts

What agents tried.

Main view:

- attempt table
- attempt timeline
- AgentHub commit links
- validation state
- accept/reject/supersede actions

### Workspace

What possible app states can be composed.

Current version:

- candidate release selection
- composition preview

Future version:

- Composite Workspace
- no-main Lab Mode
- preview environments

### Ship

What becomes real.

Main view:

- ready waves
- selected accepted attempts
- release branches
- release PRs
- shipped history
- current root/frontier

---

## Current Implementation Guidance

Build in this order:

```text
1. Keep existing Kanban alive while backend conversion lands.
2. Add latest attempt metadata to ticket/intents list.
3. Replace Review UI with Ship Room.
4. Add read-only wave/attempt views.
5. Add Ship Room actions.
6. Move Kanban from primary view to optional/familiar view.
7. Add Intent Inbox as the new primary planning view.
8. Add focused graph views.
9. Add Composite Workspace after Ship Room works end to end.
```

Do not build no-main UI first. It depends on:

- TicketAttempt
- AgentHub root/frontier
- dependency waves
- attempt validation
- ShipRun
- release composition

---

## UI Language

Prefer:

```text
Intent
Attempt
Accepted
Ready
Composed
Release PR
Shipped
Frontier
Root
Composite
Blessed
```

Avoid in swarm/AgentHub-native UI:

```text
In Review
Done
PR per ticket
Merge swarm
Swarm branch
```

Use `Done` only if it clearly means "shipped to production/main", and even then `Shipped` is better.

---

## Design Principles

1. **Intent is not execution.**
   Do not hide attempts inside a single ticket state.

2. **Attempts are plural.**
   The UI should assume agents try multiple times.

3. **Ship is a separate boundary.**
   A produced commit is not shipped code.

4. **Graphs are maps, not work queues.**
   Use them for relationship understanding, not every action.

5. **Tables beat cards for high-volume agent work.**
   Agent output volume will make card walls noisy.

6. **Show the frontier.**
   Users need to understand what leaves exist and what has shipped.

7. **Make human action obvious.**
   The product should surface where the human needs to decide, not ask them to inspect everything.

8. **No-main starts as Lab Mode.**
   Composite Workspace should preview possible futures before it becomes any kind of runtime model.

---

## Summary

The future UI should not be Trello for agents.

It should be closer to:

```text
agentic engineering control room
```

The human should be able to answer four questions quickly:

```text
What do we want?
What did agents try?
Which possible state looks good?
What are we shipping?
```

That maps to:

```text
Intents
Attempts
Workspace
Ship
```
