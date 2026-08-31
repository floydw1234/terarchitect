# AgentHub Conversion Plan (Reference Theory)

## Status

This is a **reference theory note**, not the active implementation plan.

Use instead:

1. `agenthub_mvp_plan.md`
2. `agenthub_mvp_execution_checklist.md`

## Goal

The strategic shift is still correct:

`Ticket defines intent -> agents publish attempts -> humans accept attempts -> Ship Room composes a release -> one final ship boundary`

The point is to stop treating a GitHub PR as the normal unit of agent work.

## Core Principles

- **Tickets define intent.** They are planning objects.
- **AgentHub records execution.** It stores what agents actually produced.
- **Attempts are first-class.** A ticket can have many attempts.
- **Accepted is not shipped.** Human selection and final ship are separate.
- **One final release boundary.** If GitHub is involved, it happens at the release boundary, not per ticket.

## MVP Interpretation

For the MVP, the strategic idea collapses into a much smaller implementation spine:

- `TicketAttempt`
- deterministic base selection from `shipped_frontier` or one accepted dependency
- explicit accept/reject of attempts
- candidate-backed `ShipRun`
- minimal Ship Room
- one release PR at the end

## Out of Scope for MVP

These ideas may still be interesting, but they are **not** part of the current implementation target:

- Composite Workspace as a required workflow
- canonical AgentHub channels/events as the main product surface
- generalized verification/evidence orchestration
- no-main runtime model
- multi-repo and snapshot architecture

## Practical Rule

If an implementer is unsure what to build, this file should not decide. The MVP plan and execution checklist decide.
