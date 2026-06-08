# AgentHub Conversion Implementation Plan (Archived / Theoretical)

## Status

**Do not use this file as the implementation checklist.**

This document is an older, overgrown migration roadmap that mixed:

- the *real* AgentHub-native migration,
- later product/platform ideas,
- and a bunch of speculative future work.

It is kept only as historical context.

## What Replaced It

Use these instead:

1. `agenthub_mvp_plan.md` — authoritative MVP scope
2. `agenthub_mvp_execution_checklist.md` — authoritative execution order

If this file conflicts with those documents, this file loses.

## Why This File Was Demoted

The original roadmap bundled too many layers together:

- the necessary shift from PR-per-ticket to `TicketAttempt` + `ShipRun`
- Ship Room and release-boundary shipping
- Composite Workspace / no-main product ideas
- event canon / timeline cleanup
- broad verification/evidence ambitions
- future graph and multi-repo expansion

That made the migration look bigger and fuzzier than it needs to be.

## The Useful Core That Survived

The important thesis here was real:

`Ticket intent -> agent attempts -> accepted attempt -> composition -> human ship decision`

That idea now lives in the MVP docs in a much tighter form.

## What To Keep From This Document

Only keep these ideas when implementing:

- PRs are not the unit of agent work.
- `TicketAttempt` is the unit of produced work.
- `Project.shipped_frontier` is the base for new independent work.
- Accepted attempts are different from shipped work.
- `ShipRun` is the release composition record.
- Ship Room is the human review boundary.
- GitHub PRs belong only at the final release boundary.

## What To Ignore From This Document During MVP Work

Ignore these unless the authoritative MVP docs explicitly reintroduce them:

- Composite Workspace as a required path
- no-main workflow as part of MVP
- full AgentHub event platform cleanup
- generalized verification/evidence platform work
- multi-repo workspaces
- snapshots/runtime export architecture
- future graph-specialized surfaces

## One-Sentence Summary

This file describes a *possible broader future*, not the implementation plan Codex should follow today.
