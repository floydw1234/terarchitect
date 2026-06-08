# No-Main Idea

## Status

This file is **explicitly theoretical** and **out of MVP scope**.

Do not use it to guide current implementation.

Use instead:

1. `agenthub_mvp_plan.md`
2. `agenthub_mvp_execution_checklist.md`

## The Idea

The thought experiment is simple:

Instead of one branch being the source of truth, a system could treat the application as a temporary composite of multiple recent AgentHub leaves.

In other words:

`base + selected leaves -> synthetic composite state`

This is interesting for exploration and experimentation.

## Why It Is Not MVP Work

Production software still needs:

- a known shipped artifact
- a clear audit boundary
- reliable rollback semantics
- a direct answer to “what code is running?”

The current MVP needs a trustworthy ship path, not a new ontology for reality.

## Safe Interpretation

If this idea ever becomes real, it should first appear as a **lab mode**:

- preview multiple compatible leaves together
- test synthetic combinations
- compare possible future states
- optionally promote a successful composition into the normal ship flow

## Practical Rule

Interesting? Yes.
Actionable for the current implementation pass? No.
