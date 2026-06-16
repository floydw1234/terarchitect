# Explicit Competing Attempts

Terarchitect already has first-class **inspectable attempts**:

- `GET /api/projects/<project_id>/tickets/<ticket_id>/attempts`
- `GET /api/projects/<project_id>/attempts`
- `GET /api/projects/<project_id>/attempts/<attempt_id>`
- `GET /api/projects/<project_id>/attempts/<attempt_id>/files`
- `GET /api/projects/<project_id>/attempts/<attempt_id>/diff`

Those routes expose ordinary `TicketAttempt` rows after agent work finishes.

**Explicit competing attempts** are narrower: they intentionally rerun one ticket from the same current accepted frontier and fan out multiple fresh jobs so an operator can compare alternatives before accepting one.

## API contract

**Entry point**

- `POST /api/projects/<project_id>/tickets/<ticket_id>/rerun-from-current-frontier`

**Body**

- `{}` or `{"attempt_count": 1}`: normal rerun from the current frontier
- `{"attempt_count": 2}` through `{"attempt_count": 5}`: enqueue that many competing attempts for the same ticket

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

## What it does

1. Reads the project's current accepted frontier.
2. Copies that frontier onto the ticket as the fresh `base_leaf_id`.
3. Moves the ticket back to `in_progress`.
4. Enqueues one or more `AgentJob` rows for the same ticket.
5. Each worker completion still creates a normal `TicketAttempt`.
6. Review those attempts through the existing attempt list/detail/files/diff surfaces.
7. Accept one verified attempt to advance the project's accepted frontier.

This is intentionally not a new review object. Competing runs still land as ordinary `TicketAttempt` records, and Ship Room still works from one accepted attempt per ticket.

## Limits and current implementation edges

- Current cap: `5` competing attempts per rerun request.
- The fan-out is per ticket, not per project or per wave.
- All competing attempts share the same current accepted frontier when they start.
- The API blocks reruns while another job for that ticket is still pending or running.
- `AgentJob` does not yet store rich competing-attempt metadata; sibling jobs are mainly distinguished by job id and creation order.

## Safety caveats

- Use this only when you want deliberate alternatives for the same stale or uncertain ticket.
- Each attempt still consumes real worker capacity, publishes a real AgentHub leaf, and may run tests or package installs.
- Do not accept a competing attempt just because it completed first. Inspect the diff, file list, summary, and test output for each attempt.
- Acceptance rules do not change: one accepted attempt becomes the ticket's winner and advances `project.accepted_frontier_id`.
- Older accepted work for the same ticket is still superseded when a newer attempt is accepted.

## UI status

Known frontend direction for this feature is a stale-ticket action in Kanban:

- `Rerun from frontier` for one fresh retry
- `Run competing attempts` for a small dialog that starts `2` or `3` attempts

If that UI has not landed in your build yet, use the POST route directly and inspect the resulting attempts through the existing attempt detail pages and project/ticket attempt APIs.
