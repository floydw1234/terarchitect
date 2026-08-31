# AgentHub-Native MVP Plan

## Purpose

Deliver the smallest production-usable AgentHub-native workflow for Terarchitect:

```text
Ticket defines intent
  -> agent publishes a TicketAttempt
  -> Terarchitect selects a reproducible base
  -> human accepts an attempt
  -> ShipRun composes accepted attempts
  -> Ship Room presents the result
  -> one release PR is opened at the final boundary
  -> merge advances the shipped frontier
```

The MVP proves that agents can work without creating one PR per ticket. It deliberately stops at a reliable release-to-`main` workflow and does not attempt to build the broader no-main product vision.

## Scope

The MVP includes:

- AgentHub/swarm as the normal execution path.
- `TicketAttempt` as the record of agent-produced work.
- A project-level `shipped_frontier` representing the last shipped commit.
- Deterministic worker base selection from the frontier or accepted dependencies.
- Explicit acceptance or rejection of attempts.
- `ShipRun` as the release composition and shipping record.
- Ship Room as the primary human review and release surface.
- Composition of selected accepted attempts onto a short-lived release branch.
- Minimal validation: commit availability, dependency order, composition success, and one configured test command when present.
- One GitHub release PR per `ShipRun`, created only after successful composition.
- Merge-to-main handling that updates the frontier and marks attempts shipped.
- Focused tests for the critical state transitions and failure paths.
- Minimal operational and user documentation.

The MVP may reuse existing models, APIs, coordinator behavior, AgentHub clients, and UI components. Existing advanced functionality does not need to be removed unless it interferes with the MVP path, but it must not be required for the MVP to work.

## Non-Goals

The MVP is not a redesign of every Terarchitect workflow. It does not require:

- Replacing tickets with a new intent model.
- A generalized workflow or orchestration framework.
- Complete AgentHub event sourcing.
- Rich attempt comparison or automated attempt ranking.
- Automatic repair-ticket generation.
- Arbitrary partial-set optimization.
- Extensive observability infrastructure.
- Full structured-mode migration support.
- Broad UI redesign outside Ship Room and essential ticket state.
- Exhaustive test coverage or real-service matrices for every provider.
- Production-grade abstractions for future product directions.

The goal is one coherent path that works end to end, not a platform that anticipates every future path.

## Assumptions

- A `Ticket` remains the planning object and contains enough information for an agent job.
- AgentHub can publish and retrieve commits or leaves by hash.
- Terarchitect remains the authority for attempt status, selection, and shipping state.
- `TicketAttempt` is stored in Terarchitect even when related metadata also exists in AgentHub.
- Each project has one target repository and one target branch, normally `main`.
- `Project.shipped_frontier` is the canonical base for new independent work and represents the last successfully shipped commit.
- Ticket dependencies continue to be computed from existing ticket dependency IDs.
- A ticket may have multiple attempts, but at most one accepted attempt is selected for a given ship operation.
- In-flight attempts are not rebased or rewritten when the frontier advances.
- GitHub is required only for the final release PR boundary. Agent work does not create GitHub PRs.
- A release branch is temporary and belongs to one `ShipRun`.
- Existing advanced verification, workspace, graph, and evidence features may remain in the repository but are disabled, bypassed, or treated as optional for the MVP path.

## Phase 1: Establish the MVP State Model

Make the minimum data model explicit and remove PR-shaped assumptions from the active AgentHub path.

Required state:

- `TicketAttempt` records the ticket, AgentHub commit hash, selected base hash, attempt number, summary, validation result, and status.
- MVP-facing attempt states are `proposed`, `accepted`, `rejected`, `superseded`, `failed`, and `shipped`. The live codebase still accepts legacy-compatible `validating`, `composed`, and `release_pr_open` states, but the MVP path must not require them.
- `Project.shipped_frontier` records the last shipped commit.
- `ShipRun` records the project, promotion candidate, selected attempt IDs, release branch, composed commit, test result, release PR information, failure details, and shipped commit.
- Ship-run statuses on the MVP path are `queued`, `composing`, `failed`, `ready_to_ship`, `shipping`, and `shipped`. The current code still tolerates `running` and `compose_failed` for compatibility with older callbacks.

Worker completion in AgentHub mode must create a `TicketAttempt`. It must not store an AgentHub hash in a PR record or imply that the ticket has shipped.

### Acceptance Criteria

- Completing an AgentHub worker job creates a queryable `TicketAttempt`.
- A second attempt for the same ticket does not overwrite the first.
- Accept and reject operations produce valid, auditable state transitions.
- AgentHub execution does not require a per-ticket PR record.
- A project exposes its current `shipped_frontier`.
- A `ShipRun` can identify exactly which attempts it selected.
- Database migrations work for both a new database and an existing supported database.

## Phase 2: Implement Frontier and Base Selection

Make job dispatch reproducible without a persistent `origin/swarm` branch.

Use this MVP base-selection order:

1. A ticket with no unshipped dependency starts from `shipped_frontier`.
2. A ticket with one accepted, unshipped dependency starts from that dependency’s accepted attempt commit.
3. A ticket whose dependencies are already shipped starts from the current `shipped_frontier`.
4. A ticket with multiple accepted, unshipped dependencies remains blocked for the MVP unless one commit already contains the required dependency ancestry.

The selected base hash must be persisted on the job or attempt. The worker must check out that exact hash through AgentHub/git and must not infer its base from a mutable branch.

When a ship completes, update `shipped_frontier` to the merged main commit and re-evaluate queued tickets. Do not alter jobs already running.

### Acceptance Criteria

- Every dispatched AgentHub job contains an explicit base hash.
- Independent tickets use the current shipped frontier.
- A ticket with one accepted parent uses that parent’s leaf.
- Already-shipped dependencies resolve to the current frontier.
- Unsupported multi-parent composition is blocked with a clear reason.
- Workers do not require `origin/swarm`.
- Shipping advances the frontier and unblocks eligible queued tickets.
- Running attempts retain their original base after the frontier advances.

## Phase 3: Build the Minimal ShipRun Pipeline

Implement one release-composition path from accepted attempts to a reviewable release branch.

A compose request must:

1. Select accepted attempts for one promotion candidate.
2. Confirm each selected commit exists and descends from an allowed base.
3. Check that required ticket dependencies are included or already shipped.
4. Create or reset the `ShipRun` release branch from the current target branch.
5. Apply the selected commits in a deterministic order.
6. Stop and record useful conflict details if composition fails.
7. Run the project’s single configured test command when one exists.
8. Record the composed commit and mark the run `ready_to_ship` when successful.
9. Open or update one release PR containing the ticket list, attempt hashes, summary, changed files, and test result.

Repeated compose requests for an active successful run must be idempotent. They must not create duplicate release PRs.

### Acceptance Criteria

- A promotion candidate with accepted attempts can produce a composed release branch.
- Dependency violations are rejected before composition.
- Conflicts and test failures mark the `ShipRun` failed and preserve diagnostics.
- A successful composition records its exact commit hash.
- At most one active release PR exists for a `ShipRun`.
- No selected ticket receives an individual PR.
- Retrying a failed run is explicit and does not corrupt the prior failure record.
- Concurrent compose requests cannot create competing active runs for the same candidate.

## Phase 4: Deliver the Minimal Ship Room

Make Ship Room the primary human surface for the MVP workflow.

The top-level view must show:

- Current shipped frontier.
- Promotion candidates with accepted, pending, failed, ready-to-ship, and shipped state.
- Active or latest `ShipRun`.
- Release PR link and status when one exists.

Candidate detail must show:

- Tickets in the candidate.
- Proposed and accepted attempts.
- Attempt commit, base, summary, status, and minimal validation result.
- Selected attempts for the active `ShipRun`.
- Composition or test failure details.
- Composed commit and release PR status.

Required actions:

- Accept an attempt.
- Reject an attempt.
- Compose accepted attempts.
- Retry a failed composition.
- Open the final release PR when composition succeeds.
- Ship by merging the expected release PR.

### Acceptance Criteria

- A user can move from proposed attempts to a release PR without using legacy Review pages.
- Ship Room clearly distinguishes accepted, composed, and shipped work.
- PR links appear only at the `ShipRun` level.
- Failure output is visible without inspecting backend logs.
- Invalid actions are disabled or rejected with a useful message.
- Ship Room remains usable without event timelines, evidence dashboards, or graph views.

## Phase 5: Ship and Advance the Frontier

Complete the compatibility boundary from release PR to `main`.

Before merging, verify that:

- The release PR is open.
- It targets the configured branch.
- Its head branch matches the `ShipRun`.
- Its head commit matches the recorded composed commit.
- The `ShipRun` remains `ready_to_ship`.

Merge the release PR with a non-fast-forward merge where supported. Then:

- Record the resulting main commit as `shipped_commit_hash`.
- Update `Project.shipped_frontier`.
- Mark selected attempts `shipped`.
- Mark the `ShipRun` `shipped`.
- Re-evaluate queued tickets against the new frontier.
- Preserve enough state to diagnose a partially completed ship operation.

### Acceptance Criteria

- A mismatched, closed, or externally changed PR cannot be shipped silently.
- A successful merge advances the frontier exactly once.
- Selected attempts and the `ShipRun` become shipped together.
- Retrying after a response timeout does not merge the release twice.
- New jobs start from the updated frontier.
- The complete happy path works with one project, one repository, and one target branch.

## Phase 6: Minimal Tests and Documentation

Add only tests that protect the MVP’s critical contracts.

Required backend tests:

- Worker completion creates multiple distinct `TicketAttempt` records.
- Attempt accept/reject transitions.
- Frontier and single-dependency base selection.
- Blocking unsupported multi-parent bases.
- Successful composition and configured test execution.
- Conflict and test-failure handling.
- Compose idempotency.
- Release PR branch/head validation.
- Successful ship and frontier advancement.
- Duplicate ship protection.

Required frontend tests:

- Ship Room renders promotion candidates and attempts.
- Accepted attempts can be composed.
- Failure details and release PR status render correctly.
- Shipped state is distinct from accepted state.

Required documentation:

- One short AgentHub workflow overview.
- Worker/coordinator environment requirements.
- How to configure the target branch and optional test command.
- How to recover from failed composition or a stale release PR.
- Explicit statement that agents do not create per-ticket PRs.

### Acceptance Criteria

- The focused backend and Ship Room test suites pass.
- A production frontend build succeeds.
- A documented smoke test completes the full workflow.
- Documentation describes only supported MVP behavior and does not require future platform features.

## Rollout and Cutover

1. Enable the MVP for one test project using a disposable branch and real AgentHub commits.
2. Run the complete flow through attempt publication, acceptance, composition, release PR, merge, and frontier refresh.
3. Enable it for one real single-repository project while retaining an operator-visible rollback path.
4. Make AgentHub mode the default for new projects after the successful pilot.
5. Route AgentHub projects to Ship Room and remove legacy Review entry points from their normal navigation.
6. Stop creating per-ticket PRs in AgentHub mode.
7. Keep existing structured projects unchanged until they are explicitly migrated or retired.

Cutover is complete when a new project can ship a promotion candidate through Ship Room, no ticket-level PR is created, and the merged release commit becomes the base for subsequent work.

## Not In MVP

The following are strictly outside the MVP and must not become prerequisites or expand the implementation schedule:

- Composite Workspace or any no-main workflow.
- A giant Verification Engine or generalized evidence framework.
- A canonical event platform, event-sourced architecture, or complete AgentHub timeline system.
- Graph suites, specialized graph explorers, or graph-driven verification.
- Multi-repository projects or cross-repository composition.
- Snapshots, blessed states, snapshot export, or snapshot-derived roots.
- Fancy policy engines, configurable approval languages, waiver systems, or risk scoring.
- Automated LLM base selection.
- Automatic temporary composition for multiple unshipped dependency parents.
- Browser, replay, mutation, property, LLM-review, or test-adequacy pipelines.
- General-purpose repair orchestration.
- Rich release analytics or organization-wide dashboards.

Any proposal that requires one of these capabilities to complete the AgentHub-native release path is deferred until after the MVP has shipped and been used successfully.
