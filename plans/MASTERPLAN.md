# Terarchitect Masterplan

## Status

This document is **product framing**, not an execution checklist.

For implementation, use:

1. `agenthub_mvp_plan.md`
2. `agenthub_mvp_execution_checklist.md`

## Product Thesis

Terarchitect is most coherent when it does one thing well:

`human intent -> agent attempts -> evidence/validation -> human ship decision`

The repo becomes confusing when it tries to mature every future surface at once.

## The Working Product Vision

A user should be able to:

1. define or refine a ticket/intention
2. dispatch agent work
3. inspect one or more `TicketAttempt`s
4. accept the best attempt
5. compose a dependency-safe wave
6. inspect the resulting `ShipRun`
7. ship once at the release boundary

That is enough for a real MVP.

## What Is Good and Worth Preserving

- Tickets still work as planning objects.
- AgentHub is the right execution ledger.
- Dependency waves are a good way to manage safe parallel work.
- `TicketAttempt` is the right unit of agent output.
- `ShipRun` is the right unit of release composition.
- Ship Room is the right human decision surface.

## What Was Overbuilt

These areas may have value later, but they should not be allowed to define the MVP:

- Composite Workspace / no-main productization
- giant verification/evidence systems
- broad event/timeline unification
- graph-specialized workflows as core product behavior
- multi-repo and snapshot architecture

## Corrections to Older Framing

Some older planning language in this repo became stale as implementation moved:

- The coordinator entrypoint is not a trustworthy blocker to plan around anymore.
- The repo is no longer best described as “PR-first.”
- The old PR-per-ticket path should be treated as legacy behavior, not the future shape.

## Planning Rule

If a future idea does not directly improve one of these surfaces, it is not MVP work:

- ticket/intention definition
- attempt production and inspection
- base/frontier selection
- `ShipRun` composition
- Ship Room review
- final ship boundary

## Future Ideas

Interesting future directions still exist, but they must stay behind the MVP:

- lab-style composite workspaces
- richer evidence and replay systems
- broader graph-guided planning and generation
- deeper AgentHub-native conversation/event systems

Useful? Maybe. Urgent? No.
