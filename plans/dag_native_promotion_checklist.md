# DAG-Native Promotion Checklist

Objective: refactor Terarchitect from wave-first Ship Room semantics to AgentHub-DAG-native base selection and stable promotion candidates.

Direction locked for this plan:

- Do not preserve old wave functionality as a first-class path.
- Replace wave-first behavior where practical instead of adding a parallel option.
- Let AgentHub DAG ancestry and accepted-attempt dependencies define what can start, compose, and ship.
- Make Ship Room and CLI speak in promotion candidates and `ShipRun`s, not wave management.

## Target Outcome

The refactor is done when all of the following are true:

- New ticket jobs derive `base_hash` from accepted dependency commits and `project.shipped_frontier`, not from wave grouping.
- `TicketAttempt` no longer needs `wave_num` to drive ship behavior.
- Ship Room lists stable promotion candidates built from accepted attempts whose dependency closure is satisfied.
- Operators compose and ship a candidate-backed `ShipRun`, not a numbered wave.
- Coordinator ship-run claiming and callbacks operate on candidate-backed `ShipRun`s without wave orchestration assumptions.
- AgentHub DAG is the source of truth for ancestry/composition inputs as much as the current repo permits.

## Migration Order

1. Freeze the new vocabulary and state model.
2. Replace wave-derived dispatch/base-selection rules with DAG-native dependency rules.
3. Introduce stable promotion candidate records/API without keeping wave as the canonical grouping.
4. Move `ShipRun` creation, composition, and shipping onto candidate IDs.
5. Cut Ship Room and CLI over to candidate/ShipRun language.
6. Remove leftover wave-first auto-queueing, validation, and docs.

## Phase 1 — Freeze The New Contract

Files:

- `backend/models/db.py`
- `backend/api/routes.py`
- `README.md`
- `docs/RUNBOOK.md`
- `docs/PHASE1_WORKER_API.md`
- `agenthub/README.md`

Tasks:

- [ ] Define the canonical nouns for the refactor: `shipped_frontier`, accepted `TicketAttempt`, promotion candidate, and `ShipRun`.
- [ ] Mark `wave_num` as legacy-only in code comments and plan its removal from behavior-driving paths.
- [ ] Decide whether promotion candidates live in a new table or as a tight extension of `ShipRun`; prefer a separate record if `ShipRun` should remain execution-only.
- [ ] Document that numbered waves are no longer the operator concept in README/runbook/worker API docs.
- [ ] Document that a ship run is created from a stable candidate set selected from accepted attempts whose dependency closure is valid.
- [ ] Identify every route/CLI string that currently exposes “wave” as the primary operator abstraction and list them for replacement.

Verification:

```bash
cd /home/william/Documents/codingProj/terarchitect
rg -n "\bwave\b|Ship Room|ShipRun|base_hash|shipped_frontier" README.md docs backend/api/routes.py cli/commands/ship.py coordinator/coordinator.py
python3 -m py_compile backend/models/db.py backend/api/routes.py
```

## Phase 2 — Make Dispatch And Base Selection DAG-Native

Files:

- `backend/api/services/job_service.py`
- `backend/api/services/attempt_service.py`
- `backend/api/routes.py`
- `coordinator/coordinator.py`
- `backend/tests/test_agenthub.py`
- `backend/tests/test_integration.py`

Tasks:

- [ ] Make `mvp_dependency_base_context()` the only active base-selection path for ticket jobs.
- [ ] Remove `dependency_base_context()`, temporary dependency workspace creation, and blessed-workspace fallback from ticket dispatch once no callers require them.
- [ ] Keep ticket start rules strictly dependency-based:
  - no deps -> `project.shipped_frontier`
  - one accepted unshipped dep -> that dep commit
  - only shipped deps -> `project.shipped_frontier`
  - multiple accepted unshipped deps -> blocked until promoted/shipped prerequisite work makes one stable base available
- [ ] Ensure `/api/worker/jobs/start` returns enough base-selection metadata for agents and debugging without any wave field being required.
- [ ] Keep coordinator env forwarding on `BASE_HASH` and `AGENTHUB_ROOT_HASH`, but stop implying that root/frontier selection is a wave concern.
- [ ] Update attempt creation/serialization so `wave_num` is not required for acceptance, inspection, or later promotion selection.
- [ ] Add explicit blocked reasons for tickets that cannot start because their dependency shape does not yield a single stable base.

Verification:

```bash
cd /home/william/Documents/codingProj/terarchitect
pytest backend/tests/test_agenthub.py backend/tests/test_integration.py -q
python3 -m py_compile backend/api/services/job_service.py backend/api/services/attempt_service.py backend/api/routes.py coordinator/coordinator.py
```

## Phase 3 — Add Stable Promotion Candidates

Files:

- `backend/models/db.py`
- `backend/api/routes.py`
- `backend/api/services/merge_service.py`
- `backend/tests/test_unit.py`
- `backend/tests/test_agenthub.py`
- `backend/tests/test_e2e.py`

Tasks:

- [ ] Add a first-class promotion candidate model if one does not already exist cleanly in the schema.
- [ ] Candidate schema should capture at minimum:
  - project id
  - selected attempt ids
  - selected leaf hashes
  - candidate base root hash
  - status
  - validation summary / conflict summary
  - optional composed commit hash once materialized
- [ ] Make candidate selection derive from accepted attempts plus dependency closure, not from `compute_waves()`.
- [ ] Define candidate validity rules:
  - every included ticket has one accepted attempt
  - every dependency is either already shipped into `shipped_frontier` or represented by an included accepted attempt
  - every included attempt base is either the frontier or another included accepted attempt
  - unknown deps / cycles / ambiguous multi-parent ancestry block the candidate
- [ ] Decide whether candidates are auto-created from eligible accepted attempts or created explicitly by operator/API; whichever is chosen, make that the only happy path.
- [ ] Keep candidate selection stable across new unrelated attempts so operators can review a durable set instead of a moving wave.
- [ ] Replace wave-oriented helper logic in `merge_service.py` with candidate graph analysis helpers.

Verification:

```bash
cd /home/william/Documents/codingProj/terarchitect
pytest backend/tests/test_unit.py backend/tests/test_agenthub.py backend/tests/test_e2e.py -q
python3 -m py_compile backend/models/db.py backend/api/services/merge_service.py backend/api/routes.py
```

## Phase 4 — Move ShipRun Creation And Validation To Candidate IDs

Files:

- `backend/api/routes.py`
- `backend/api/services/merge_service.py`
- `coordinator/coordinator.py`
- `agent/shipper.py`
- `backend/tests/test_e2e.py`
- `backend/tests/test_integration.py`

Tasks:

- [ ] Replace `ShipRun.wave_num` as the primary lookup key with a candidate reference.
- [ ] Add candidate-backed endpoints for:
  - list candidates
  - get candidate detail
  - compose candidate into a `ShipRun`
  - inspect `ShipRun`
  - ship `ShipRun`
  - send feedback on candidate or ship run
- [ ] Make `/worker/ship-run/next` return candidate context instead of wave ticket lists as the primary composition unit.
- [ ] Update ship-run validation to work from candidate membership and DAG ancestry rather than “earlier wave must already be shipped”.
- [ ] Ensure ship-run compose callbacks persist:
  - candidate id
  - base main/frontier hash
  - composed commit hash
  - changed files
  - test status/output
- [ ] Ensure shipping advances `project.shipped_frontier` from the shipped run and marks only the candidate’s attempts as shipped.
- [ ] Remove auto-queue logic that creates ship runs on “wave complete”; candidate creation/compose should be the trigger instead.

Verification:

```bash
cd /home/william/Documents/codingProj/terarchitect
pytest backend/tests/test_e2e.py backend/tests/test_integration.py -q
python3 -m py_compile backend/api/routes.py backend/api/services/merge_service.py coordinator/coordinator.py agent/shipper.py
```

## Phase 5 — Cut Ship Room And CLI Over To Candidates/ShipRuns

Files:

- `cli/commands/ship.py`
- `cli/commands/ticket.py`
- `cli/commands/attempt.py`
- `cli/commands/workspace.py`
- `frontend/src/pages/ShipRoomPage.tsx`
- `frontend/src/pages/AttemptDetailPage.tsx`
- `frontend/src/utils/api.ts`
- `frontend/src/__tests__/ShipRoom.test.tsx`

Tasks:

- [ ] Replace `ta ship waves`, `show <wave_num>`, `compose <wave_num>`, `merge-pr <wave_num>` with candidate/ship-run oriented commands.
- [ ] Remove CLI text that tells operators to reason in “wave complete”, “ship prerequisite waves”, or “compose wave N”.
- [ ] Update ticket/attempt output to point operators at candidate review or ship-run review, not wave review.
- [ ] Reframe Ship Room UI around:
  - current frontier
  - eligible/blocked promotion candidates
  - active/latest ship runs
  - candidate blockers derived from DAG ancestry
  - composed diff/test output
- [ ] Remove workspace promote language as the main export path if candidates supersede it; if workspaces remain, demote them to a separate experimental surface.
- [ ] Keep route wrappers thin in the CLI and API client; no second orchestration path in the client.

Verification:

```bash
cd /home/william/Documents/codingProj/terarchitect
python3 -m py_compile cli/commands/ship.py cli/commands/ticket.py cli/commands/attempt.py cli/commands/workspace.py
cd frontend && npm test -- --runInBand ShipRoom
```

## Phase 6 — Remove Wave-First Compatibility Paths

Files:

- `backend/api/services/merge_service.py`
- `backend/api/routes.py`
- `backend/api/services/job_service.py`
- `cli/commands/ship.py`
- `README.md`
- `docs/RUNBOOK.md`
- `docs/PHASE1_WORKER_API.md`
- `plans/agenthub_mvp_execution_checklist.md`

Tasks:

- [ ] Delete `compute_waves()` and `analyze_wave_dependencies()` from active shipping logic once no routes depend on them.
- [ ] Remove `wave_num` requirements from attempt creation, ship-run lookup, ship-run feedback channels, and operator documentation where practical.
- [ ] Remove ship-run auto-creation from ticket completion.
- [ ] Remove legacy route handlers or convert them into compatibility shims that immediately redirect to candidate-backed APIs; prefer deletion if nothing depends on them.
- [ ] Remove runbook/readme guidance that tells humans to accept attempts, compose a wave, inspect a wave, or ship a wave.
- [ ] Update MVP planning docs so the repo no longer describes wave-first shipping as the target architecture.

Verification:

```bash
cd /home/william/Documents/codingProj/terarchitect
rg -n "\bwave\b|compute_waves|analyze_wave_dependencies|ship_wave_|/ship/waves" backend cli docs README.md plans
pytest backend/tests/test_unit.py backend/tests/test_agenthub.py backend/tests/test_integration.py backend/tests/test_e2e.py -q
```

## Suggested Test Matrix

- [ ] Ticket with no dependencies starts from `shipped_frontier`.
- [ ] Ticket with one accepted unshipped dependency starts from that dependency commit.
- [ ] Ticket with only shipped dependencies starts from `shipped_frontier`.
- [ ] Ticket with multiple accepted unshipped dependencies is blocked until the dependency state becomes unambiguous.
- [ ] Candidate creation fails on unknown dependency references.
- [ ] Candidate creation fails on dependency cycles.
- [ ] Candidate creation fails when an attempt base is neither frontier nor another selected accepted attempt.
- [ ] Candidate compose is idempotent while a ship run is active.
- [ ] Shipping advances frontier and marks only selected attempts shipped.
- [ ] New accepted attempts do not silently mutate an already-created candidate under review.

## Recommended Implementation Sequence

- [ ] Land model/API comments and docs contract changes first.
- [ ] Simplify dispatch/base selection second so new attempts are DAG-native before the ship surface changes.
- [ ] Introduce candidate schema and candidate validation third.
- [ ] Re-key ship runs to candidates fourth.
- [ ] Cut CLI/UI to the new APIs fifth.
- [ ] Delete wave-first behavior and stale docs last, after tests are green.
