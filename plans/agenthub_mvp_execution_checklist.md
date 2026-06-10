# AgentHub MVP Execution Checklist

**Objective:** implement the smallest credible AgentHub-native release path in Terarchitect without dragging the whole repo into another grand unified theory.

This checklist assumes the target MVP is:

`Ticket -> TicketAttempt -> accepted attempt -> ShipRun -> Ship Room -> one release PR -> shipped_frontier advances`

## Definition of Done

The MVP is done when all of the following are true for a swarm/AgentHub project:

- Worker completion creates a `TicketAttempt` instead of depending on a ticket-level PR.
- Each dispatched worker job has an explicit base hash derived from `shipped_frontier` or one accepted dependency.
- A promotion candidate with accepted attempts can be composed through a `ShipRun`.
- Ship Room can show candidate status, accepted attempts, compose failures, and final ship state.
- Shipping happens once at the promotion boundary through a candidate-backed `ShipRun`.
- The final ship updates `projects.shipped_frontier` and marks selected attempts as `shipped`.
- No per-ticket PR is required anywhere in the happy path.
- Focused backend and frontend tests cover the happy path plus the obvious failure modes.

## Execution Order

1. Lock the active backend state model to the MVP surface.
2. Lock worker base-selection and dispatch behavior.
3. Lock `ShipRun` composition and ship endpoints.
4. Strip Ship Room expectations down to the MVP UI.
5. Verify CLI + coordinator still follow the same path.
6. Tighten tests and write one short operator doc.

---

## Phase 1 — Freeze the MVP state model

**Why**

Before changing behavior, make the intended state machine boring and explicit. Right now the repo has extra surfaces; the MVP needs a smaller contract.

**Files to touch**

- `backend/models/db.py`
- `backend/api/routes.py`
- `backend/tests/test_unit.py`
- `backend/tests/test_agenthub.py`
- `plans/agenthub_mvp_plan.md`

**Concrete changes**

- In `backend/models/db.py`, treat these as the active MVP records:
  - `Project.shipped_frontier`
  - `AgentJob`
  - `TicketAttempt`
  - `ShipRun`
- Do **not** remove `CompositeWorkspace` yet; just make sure nothing in the MVP path requires it.
- In `TicketAttempt`, keep the MVP path on:
  - `proposed`
  - `accepted`
  - `rejected`
  - `superseded`
  - `failed`
  - `shipped`
  Legacy-compatible `validating`, `composed`, and `release_pr_open` states still exist in the live code, but the MVP path should not depend on them.
- In `ShipRun`, treat the MVP path as:
  - `queued`
  - `composing`
  - `failed`
  - `ready_to_ship`
  - `shipping`
  - `shipped`
  The live callbacks still accept `running` and `compose_failed` for compatibility, so do not remove those code paths in this cleanup pass.
- In `backend/api/routes.py`, verify the attempt endpoints are the canonical human controls:
  - `GET /api/projects/:project_id/tickets/:ticket_id/attempts`
  - `POST /api/projects/:project_id/tickets/:ticket_id/attempts/:attempt_id/accept`
  - `POST /api/projects/:project_id/tickets/:ticket_id/attempts/:attempt_id/reject`
- Ensure swarm completion writes `TicketAttempt` state and does **not** imply ticket shipped state or ticket-level PR review. Legacy PR-shaped flows, if present elsewhere, are not part of the MVP contract.

**Tests**

- Add/update unit tests for valid attempt transitions in `backend/tests/test_unit.py`.
- Add/update API tests in `backend/tests/test_agenthub.py` for:
  - multiple attempts on one ticket
  - accept supersedes prior accepted attempt
  - reject returns correct state

**Verification**

Run:
```bash
cd ~/Documents/codingProj/terarchitect
pytest backend/tests/test_unit.py backend/tests/test_agenthub.py -q
python3 -m py_compile backend/models/db.py backend/api/routes.py
```

---

## Phase 2 — Lock frontier and base selection

**Why**

If base selection is fuzzy, everything after it becomes haunted.

**Files to touch**

- `backend/api/routes.py`
- `coordinator/coordinator.py`
- `backend/tests/test_agenthub.py`
- `backend/tests/test_integration.py`

**Concrete changes**

- Keep `Project.shipped_frontier` as the default base for independent tickets.
- Preserve explicit base selection on worker dispatch.
- In `coordinator/coordinator.py`, verify claimed jobs continue forwarding the selected frontier/base through env, especially:
  - `AGENTHUB_ROOT_HASH`
- In the worker/job response path from `backend/api/routes.py`, make the base-selection order explicit:
  1. no unshipped deps -> `project.shipped_frontier`
  2. one accepted unshipped dep -> accepted dep commit hash
  3. already shipped deps -> current `project.shipped_frontier`
  4. multiple accepted unshipped deps -> block in MVP with a clear error/reason
- Do **not** invoke workspace composition as a hidden dependency for the MVP path.
- If current code already supports fancy multi-parent composition, leave it in place but keep the documented MVP path blocked/simple.

**Tests**

In `backend/tests/test_agenthub.py` and `backend/tests/test_integration.py`, cover:

- base from frontier when no deps exist
- base from accepted parent when one dep exists
- base from frontier when parent is already shipped
- blocked response for unsupported multi-parent unshipped deps
- frontier refresh causes newly eligible queued work to dispatch

**Verification**

Run:
```bash
cd ~/Documents/codingProj/terarchitect
pytest backend/tests/test_agenthub.py backend/tests/test_integration.py -q
python3 -m py_compile coordinator/coordinator.py backend/api/routes.py
```

---

## Phase 3 — Make ShipRun the only ship path

**Why**

This is the actual product change: stop shipping ticket-by-ticket and ship one composed wave.

**Files to touch**

- `backend/api/routes.py`
- `backend/models/db.py`
- `coordinator/coordinator.py`
- `cli/commands/ship.py`
- `backend/tests/test_e2e.py`
- `backend/tests/test_integration.py`

**Concrete changes**

- Keep these endpoints as the only MVP shipping API surface:
  - `GET /api/projects/:project_id/ship/waves`
  - `GET /api/projects/:project_id/ship/waves/:wave_num`
  - `POST /api/projects/:project_id/ship/waves/:wave_num/compose`
  - `POST /api/projects/:project_id/ship/waves/:wave_num/ship`
  - `POST /api/projects/:project_id/ship/waves/:wave_num/feedback`
- In `ship_wave_compose`, require:
  - all tickets in the wave have accepted attempts
  - dependency validation passes before queueing
  - one active ship run per wave
- In `coordinator/coordinator.py`, keep `/api/worker/ship-run/next` as the coordinator claim path.
- Ensure the worker-composed callback transitions a ship run to `ready_to_ship` with:
  - `composed_commit_hash`
  - `base_main_hash`
  - `changed_files`
  - `test_status`
  - `test_output`
- In `ship_wave_ship`, enforce:
  - ship only from `ready_to_ship`
  - update `shipped_commit_hash`
  - advance `project.shipped_frontier`
  - mark selected attempts as `shipped`
- `cli/commands/ship.py` should remain a thin wrapper over the API. Do not add a second ship workflow there.

**Tests**

Use `backend/tests/test_e2e.py` as the anchor for the happy path already present:

- compose wave
- report composed via worker callback
- ship wave
- frontier advances
- attempts get correct final states

Add/verify integration coverage for:

- compose returns existing active run instead of duplicating
- compose fails with missing accepted attempts
- ship fails when run is not `ready_to_ship`
- ship fails when composition validation is stale

**Verification**

Run:
```bash
cd ~/Documents/codingProj/terarchitect
pytest backend/tests/test_e2e.py backend/tests/test_integration.py -q
python3 -m py_compile backend/api/routes.py coordinator/coordinator.py cli/commands/ship.py
```

---

## Phase 4 — Trim Ship Room to the MVP surface

**Why**

The UI already has extra surfaces. MVP Ship Room should show the release boundary, not every future research program.

**Files to touch**

- `frontend/src/pages/ShipRoomPage.tsx`
- `frontend/src/pages/AttemptDetailPage.tsx`
- `frontend/src/utils/api.ts`
- `frontend/src/App.tsx`
- `frontend/src/__tests__/ShipRoom.test.tsx`
- `frontend/src/__tests__/AttemptDetail.test.tsx`

**Concrete changes**

- In `ShipRoomPage.tsx`, make the critical UI states obvious:
  - current frontier
  - waves
  - accepted counts
  - active/latest `ShipRun`
  - release PR link
  - compose failure/test failure output
- Keep only these user actions in the MVP narrative:
  - review attempts
  - accept/reject attempt
  - compose wave
  - retry compose
  - ship wave
  - send feedback
- `AttemptDetailPage.tsx` should remain a lightweight attempt inspector for:
  - attempt number
  - commit hash
  - base hash
  - summary
  - test status/output
- In `frontend/src/utils/api.ts`, keep the Ship Room calls aligned to the existing backend endpoints:
  - `getShipWaves`
  - `getShipWaveDetail`
  - `composeWave`
  - `shipWave`
  - `sendWaveFeedback`
  - `getTicketAttempts`
- Do not make EvidencePanel, timelines, graph views, or workspace surfaces required for the happy path. If they stay mounted, the page must still be understandable without them.

**Tests**

Update `frontend/src/__tests__/ShipRoom.test.tsx` to assert:

- frontier appears in the header
- ready-to-ship run shows release PR link
- compose-failed state shows error text
- shipped state is distinct from accepted state
- no ticket-level PR language is shown

Update `frontend/src/__tests__/AttemptDetail.test.tsx` to assert:

- attempt metadata renders
- attempt summary renders
- test output renders when requested

**Verification**

Run:
```bash
cd ~/Documents/codingProj/terarchitect/frontend
npm test -- --runInBand ShipRoom.test.tsx AttemptDetail.test.tsx
npm run build
```

---

## Phase 5 — Keep CLI and operator flow aligned

**Why**

The UI is not enough; operators need one obvious control path.

**Files to touch**

- `cli/commands/ship.py`
- `plans/README.md`
- `docs/RUNBOOK.md`
- optionally `README.md` if it still oversells non-MVP surfaces

**Concrete changes**

- Keep `ta ship` centered on the same candidate/`ShipRun` flow as the UI:
  - `ta ship candidates <project_id>`
  - `ta ship candidate <project_id> <candidate_id>`
  - `ta ship compose-candidate <project_id> <candidate_id>`
  - `ta ship run <project_id> <run_id>`
  - `ta ship ship-run <project_id> <run_id>`
  - `ta ship ship-candidate <project_id> <candidate_id>`
  - `ta ship feedback <project_id> <candidate_id> "message"`
- Update docs so they describe one operator path:
  1. agent completes work
  2. human accepts attempt
  3. compose the promotion candidate
  4. inspect `ShipRun`
  5. ship/merge final boundary
- Explicitly say ticket-level PR review is not part of swarm mode.

**Tests**

- If CLI tests already exist, update them; if not, keep this phase doc-only.
- Prefer API verification over building a new CLI test harness.

**Verification**

Run:
```bash
cd ~/Documents/codingProj/terarchitect
python3 -m py_compile cli/commands/ship.py
```

Manual smoke check:
```bash
ta ship candidates <project_id>
ta ship candidate <project_id> <candidate_id>
ta ship compose-candidate <project_id> <candidate_id>
```

---

## Phase 6 — Final focused regression pass

**Why**

This repo already contains later-phase machinery. The last step is proving the MVP path works without needing all of it.

**Files to touch**

- `backend/tests/test_agenthub.py`
- `backend/tests/test_integration.py`
- `backend/tests/test_e2e.py`
- `frontend/src/__tests__/ShipRoom.test.tsx`
- `frontend/src/__tests__/AttemptDetail.test.tsx`

**Concrete changes**

- Remove or rewrite any test assumptions that require:
  - Composite Workspace
  - blessed workspace promotion
  - evidence gating for MVP happy path
  - specialized timeline/graph behavior
- Keep one full happy-path e2e test for:
  - accepted attempt -> compose -> ready_to_ship -> ship -> frontier advances
- Keep one obvious failure-path set for:
  - missing accepted attempt
  - compose conflict / compose failure
  - stale or invalid ship state
  - unsupported multi-parent dependency base

**Tests**

Run the focused MVP suite:
```bash
cd ~/Documents/codingProj/terarchitect
pytest \
  backend/tests/test_unit.py \
  backend/tests/test_agenthub.py \
  backend/tests/test_integration.py \
  backend/tests/test_e2e.py -q
cd frontend
npm test -- --runInBand ShipRoom.test.tsx AttemptDetail.test.tsx
npm run build
```

**Verification**

Final manual rule:

- start from a swarm project
- complete one ticket into a `TicketAttempt`
- accept it
- compose its wave
- observe `ready_to_ship`
- ship it
- confirm new `shipped_frontier`
- confirm no ticket PR was needed anywhere

---

## Do Not Expand Scope

Ignore these unless the MVP path is already working end-to-end:

- `CompositeWorkspace` as a required dependency
- no-main / snapshot productization
- EvidencePanel-driven gates as required ship blockers
- canonical AgentHub timeline/event platform cleanup
- graph-specific UX or graph verification
- multi-repo composition
- automated repair loops
- mutation/property/browser/replay/LLM-review pipelines
- generalized policy/waiver/approval frameworks

If a proposed change does not directly improve:

- `TicketAttempt`
- base selection
- `ShipRun`
- Ship Room
- final ship to `main`

…then it is not part of this checklist. That is future-you’s problem.
