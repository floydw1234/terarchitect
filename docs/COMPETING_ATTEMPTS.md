# Competing Attempts

Terarchitect already has first-class **inspectable attempts**:

- `GET /api/projects/<project_id>/tickets/<ticket_id>/attempts`
- `GET /api/projects/<project_id>/attempts`
- `GET /api/projects/<project_id>/attempts/<attempt_id>`
- `GET /api/projects/<project_id>/attempts/<attempt_id>/files`
- `GET /api/projects/<project_id>/attempts/<attempt_id>/diff`

Those routes expose ordinary `TicketAttempt` rows after agent work finishes.

Explicit competing attempts are a narrower operator tool: rerun one ticket from the same current frontier, fan out several fresh jobs, validate each result as a candidate, choose one winner, and only unblock downstream work after one winner is explicitly accepted into the frontier.

## Lifecycle

The intended lifecycle is:

1. Dispatch `N` attempts for one ticket from the same current accepted frontier.
2. Each worker completion creates a normal `TicketAttempt`.
3. Each attempt is validated as a **candidate**.
4. Operators inspect the candidate set and choose a **winner**.
5. The chosen winner may remain **unintegrated** for a while.
6. Downstream dependencies stay blocked until one winner is explicitly **accepted/integrated** into `project.accepted_frontier_id`.
7. Ship Room exports that accepted work through a promotion candidate and then a `ShipRun`.

Keep the nouns separate:

- **validated candidate**: passed validation and is eligible for review
- **winner**: the operator-preferred candidate for that ticket
- **accepted/integrated**: the winner that actually advanced the accepted frontier
- **promotion candidate**: a stable export set built from accepted/integrated attempts

Validated does not mean chosen. Chosen does not mean integrated. Dependency unblocking requires the last step.

## API contract

**Entry point**

- `POST /api/projects/<project_id>/tickets/<ticket_id>/rerun-from-current-frontier`

**Body**

- `{"attempt_count": 1}`: one fresh rerun from the current frontier
- `{"attempt_count": 2}` through `{"attempt_count": 5}`: explicit competing attempts for that ticket

Target product default:

- Operator-triggered reruns should default to `3` attempts per ticket.
- Raw API clients should send `{"attempt_count": 3}` explicitly until every backend lane has flipped the server-side default.
- Rerun surfaces may still override to any value in `1..5`.

**Success response**

- HTTP `202 Accepted`
- normal ticket payload plus:
  - `attempt_count`
  - `job_count`
  - `job_ids`
  - `message`

**Validation**

- HTTP `400` if `attempt_count` is not an integer or is outside `1..5`
- HTTP `409` if the project has no current `accepted_frontier_id`
- HTTP `409` if the ticket already has pending or running jobs

## Execution semantics

When the rerun endpoint succeeds, Terarchitect:

1. Reads the project's current accepted frontier.
2. Copies that frontier onto the ticket as the fresh `base_leaf_id`.
3. Moves the ticket back to `in_progress`.
4. Enqueues one or more `AgentJob` rows for the same ticket.
5. Allows each completion to land as a normal `TicketAttempt`.
6. Exposes those sibling attempts through the standard attempt list/detail/files/diff surfaces.
7. Waits for an operator to choose a winner and accept/integrate it before the frontier changes.

This is intentionally not a new review object. Competing runs still land as ordinary `TicketAttempt` records. The extra structure is lifecycle meaning, not a parallel table.

## Concurrency

Concurrency is capped globally by `MAX_CONCURRENT_AGENTS`.

- The cap applies across all workers, not per project and not per ticket.
- Same-ticket competing attempts may run concurrently inside that shared cap.
- Other-ticket graph conflicts still apply. A competing-attempt fan-out for ticket `A` does not waive dependency or overlap guards for unrelated tickets.
- In legacy shared-daemon Docker mode (`AGENT_DOCKER_MODE=dood`), treat `MAX_CONCURRENT_AGENTS=1` as the only safe setting.

Example:

- `MAX_CONCURRENT_AGENTS=4`
- Ticket `A` rerun requests `attempt_count=3`
- Tickets `B` and `C` are otherwise runnable

Possible outcomes:

- all three attempts for `A` may run at once, leaving one remaining slot for `B` or `C`
- or two attempts for `A` plus two unrelated runnable tickets may fill the cap
- but blocked/conflicting tickets still wait even when there is raw container capacity

## Worker metadata

The competing-attempt worker contract has two layers.

Stable metadata already forwarded today:

- job payload metadata fields such as `attempt_slot`, `attempt_index`, `attempt_count`
- environment variables `ATTEMPT_SLOT`, `ATTEMPT_INDEX`, `ATTEMPT_COUNT`
- lineage/base fields including `BASE_LEAF_ID`, `BASE_HASH`, `AGENTHUB_ROOT_HASH`, and `ACCEPTED_FRONTIER_ID` when available

Target strategy metadata for sibling lanes to expose consistently:

- `strategy_id`
- `strategy_label`
- `strategy_prompt`
- `strategy_index`
- `strategy_count`

Recommended environment mirror for workers:

- `ATTEMPT_STRATEGY_ID`
- `ATTEMPT_STRATEGY_LABEL`
- `ATTEMPT_STRATEGY_PROMPT`
- `ATTEMPT_STRATEGY_INDEX`
- `ATTEMPT_STRATEGY_COUNT`

Workers should treat these fields as selection guidance only. The strategy does not change frontier semantics: every run still produces just a candidate until an operator chooses and integrates one.

## Five operator-visible strategies

The default competing-attempt set should expose five pre-selected coding strategies/personas:

1. `minimal-patch` — smallest safe delta that satisfies the ticket with minimal churn
2. `root-cause-debugger` — investigate underlying failure mode first, then patch the real cause
3. `test-first` — reproduce or tighten coverage before changing implementation
4. `refactor-forward` — improve local structure while delivering the requested behavior
5. `systems-explorer` — consider broader architectural tradeoffs within the ticket boundary

Operators should be able to see which strategy was assigned to each attempt. Workers should receive the strategy identity in job metadata and env, not infer it from prompt wording alone.

## Dependency rule

Dependencies are satisfied only by accepted/integrated work.

- A validated candidate does not unblock dependents.
- A chosen winner that is deliberately left unintegrated does not unblock dependents.
- Only the accepted/integrated winner, which advances `project.accepted_frontier_id`, counts as available upstream work.

That rule is what keeps competing attempts safe: operators can compare alternatives without accidentally advancing the graph.

## Ship Room relationship

Competing attempts stop at ticket-level selection. Ship Room is the export path after that.

- attempt review decides which candidate wins a ticket
- acceptance/integration advances the accepted frontier
- promotion candidates are built from accepted/integrated attempts whose dependency closure is valid
- `ShipRun` composes and ships that promotion candidate

Do not treat Ship Room as the place where raw competing attempts are resolved. Ship Room operates on accepted work, not merely validated alternatives.

## Current implementation edges

- Current cap remains `5` attempts per rerun request.
- Sibling jobs are mainly distinguished by job id plus attempt metadata; richer lifecycle labels may still be landing in adjacent branches.
- Some older backend paths still blur validation and acceptance in emitted status names. Treat that as implementation drift, not the intended product contract.

## CLI and UI direction

Expected operator surfaces:

- `ta ticket rerun-current-frontier <project_id> <ticket_id>` defaults to `3` attempts
- `ta ticket rerun-current-frontier <project_id> <ticket_id> --attempt-count 1` for a single retry
- `ta ticket rerun-current-frontier <project_id> <ticket_id> --attempt-count 5` for wider exploration
- UI labels should distinguish candidate validation, winner selection, and accepted/integrated frontier state

If the richer UI has not landed in your build yet, call the POST route directly, inspect the resulting attempts through the existing attempt APIs, and keep the lifecycle nouns above as the source of truth.
