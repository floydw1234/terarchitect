# Ticket Redefinition

## Status

This is a **reference concept note**, not required to execute the MVP.

Use the MVP docs first:

1. `agenthub_mvp_plan.md`
2. `agenthub_mvp_execution_checklist.md`

## Core Idea

A ticket should increasingly behave like an **intent object**, not a fake all-purpose workflow state machine.

Useful ticket responsibilities:

- describe what should change
- describe why it matters
- capture dependencies
- capture architecture scope/context
- provide enough context for agent dispatch

Things a ticket should stop pretending to be:

- the canonical record of what an agent produced
- the same thing as a GitHub PR
- the final human review unit
- the proof that code shipped

## MVP Interpretation

For the MVP, do **not** redesign tickets from scratch.

Just preserve this separation:

- `Ticket` = planning / intent container
- `TicketAttempt` = produced agent work
- `ShipRun` = release composition and ship record

That is enough to avoid most of the old confusion.

## What To Avoid During MVP Work

Do not block implementation on:

- removing Kanban entirely
- inventing a new universal intent schema
- fully replacing `column_id` semantics everywhere
- rewriting all ticket views before Ship Room works

## Future Direction

Later, if the MVP path is stable, ticket state can be cleaned up further so that:

- tickets represent intent
- attempts represent execution
- shipping represents shipped reality

That future cleanup is reasonable. It is not required before the MVP ships.
