# Terarchitect attempt lifecycle semantics

Session-derived operator contract for competing attempts and dependency gating.

## Canonical states / concepts

Keep these nouns separate in implementation, docs, and reports:

1. **Validated candidate** — an agent completed work and Terarchitect/AgentHub accepted the artifact as inspectable. This is not a winner and does not advance any frontier.
2. **Chosen winner** — the operator selected one attempt as the preferred solution for the ticket. A winner can remain unintegrated indefinitely.
3. **Accepted / blessed winner** — the chosen winner was explicitly approved for integration readiness. This is still distinct from frontier composition.
4. **Pushed to frontier / composed frontier** — one or more blessed sibling winners were composed together into a new frontier commit. A single ticket should only become the frontier directly when it has no siblings in that composition wave.
5. **Shipped** — the composed frontier was exported/composed/pushed through Ship Room or the explicit publish path and verified downstream.

Do not use `accepted` as a synonym for validated. Do not collapse `winner`, `accepted/blessed`, and `frontier composition` into one step. If legacy code still has `accepted` statuses, inspect the current model/API before assuming what it means.

## Composition / staleness rule

Do not treat sibling tickets as something to avoid or discard just because an earlier winner exists on an older frontier base. In-progress sibling tickets can remain valid candidates. The extra work belongs at composition time: merge the blessed sibling winners together into a fresh frontier commit instead of forcing each ticket to become the frontier one-by-one.

## Dependency gating rule

Downstream tickets should unblock only when each prerequisite ticket has a **winner** and that winner is **accepted/integrated** for dependency use. These are not enough by themselves:

- a validated attempt with no winner selection
- a chosen winner that has not been integrated
- a shipped/local branch that has not been reflected in Terarchitect's frontier state

## Competing attempt dispatch rule

Each ticket can carry a default attempt count; William's desired product default is `3`. Fresh ticket dispatch should use that per-ticket default. Explicit reruns may override the count for that run.

Persist attempt metadata on the job rather than deriving it from currently-active siblings:

- `attempt_batch_id`
- `attempt_index` (1-based)
- `attempt_count`
- `attempt_strategy`
- `attempt_strategy_description`

Coordinator/worker env should expose corresponding `TERARCHITECT_ATTEMPT_*` variables so the worker prompt can adopt the intended strategy.

## Five strategy slots

The product-level strategy set used in this session:

- `conservative-minimalist` — smallest safe change, low blast radius
- `test-first-verifier` — start by proving behavior with tests
- `architecture-cleanup` — improve structure/centralization while solving the ticket
- `performance-simplicity` — prefer simpler/faster runtime paths
- `product-polish` — bias toward user-facing clarity and finish quality

Use stable keys; docs/UI can render friendly descriptions.

## Verification checklist after lifecycle changes

Run focused tests that prove:

- completion creates validated attempts, not implicit winners
- choosing a winner does not advance `accepted_frontier_id` / shipped frontier by itself
- dependencies remain blocked until winner + accepted/integrated are both true
- promotion candidates reject non-winning or non-integrated attempts
- fresh dispatch enqueues the ticket default attempt count with durable metadata
- rerun override still works
- coordinator forwards strategy metadata to worker env
