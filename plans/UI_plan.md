# UI Plan

## Status

This is a **reference UI note**.

For implementation order, use `agenthub_mvp_execution_checklist.md`.
For product scope, use `agenthub_mvp_plan.md`.

## MVP UI Goal

The UI only needs to make one path obvious:

`Ticket -> attempts -> accepted attempt -> Ship Room -> ShipRun -> ship`

## Primary MVP Surfaces

### 1. Ticket / Intent Surface

Purpose:

- show what should change
- show dependencies
- trigger agent work
- link to produced attempts

The ticket can still be called a ticket in the UI. It does not need a full intent-model rewrite for MVP.

### 2. Attempt Detail

Purpose:

- show commit hash and base hash
- show summary
- show test status/output
- allow accept/reject decisions
- make it obvious that an accepted attempt is *not yet shipped*

### 3. Ship Room

Purpose:

- group work by wave
- show accepted counts
- show current/latest `ShipRun`
- show compose failures and ready-to-ship state
- show release PR link when present
- expose compose, feedback, and ship actions

## UI Rules for MVP

- Keep Kanban only as a familiar view, not the source of truth.
- Do not present ticket-level PR review as the main path.
- Do not require graph exploration, workspace composition, or evidence panels to understand the happy path.
- Make ship state visibly different from attempt state.
- Make stale/conflicted/blocked states readable in plain language.

## Out of Scope for MVP UI

Do not let these become required before shipping the MVP:

- Composite Workspace as a core screen
- no-main UI flows
- generalized timeline/event product surfaces
- rich attempt ranking/comparison suites
- graph-first product navigation
- large onboarding redesigns

## Near-Future UI Improvements

After the MVP path is stable, the next sensible UI improvements are:

- cleaner attempt selection and retry flows
- clearer dependency/wave explanations
- stronger shipping history and frontier visibility
- better operator feedback for compose/ship failures

That is enough. The UI does not need to become a philosophy degree before the product ships.
