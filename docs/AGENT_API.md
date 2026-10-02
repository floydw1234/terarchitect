# Agent API — machine-readable `ta` results

Terarchitect’s CLI is built for agents that hand off a goal and poll for outcomes. Several status commands emit a **stable JSON envelope** on stdout when you pass `--output json` or the global `--json` flag. Errors and connection failures still use the existing JSON error envelope on **stderr** with a non-zero exit code.

## Commands

| Command | Purpose |
|---------|---------|
| `ta status <project> --ticket <ticket_id> --json` | Ticket-level ledger: acceptance, candidate, ShipRun, evidence |
| `ta attempt show <project> <attempt_id> --json` | Single attempt readiness |
| `ta ship run <project> <run_id> --json` | ShipRun composition / ship state |
| `ta ship operator-loop <project> <ticket> <attempt> --json` | End-to-end promote-and-ship result |

Human-readable output is unchanged when `--json` is not set.

## Schema (version 1)

Every success response includes the same top-level keys:

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | `1` | Increment when breaking shape changes |
| `status` | string | `shipped`, `failed`, `needs_input`, or `running` |
| `project_id` | string \| null | Project UUID |
| `ticket_id` | string \| null | Ticket UUID when applicable |
| `attempt_id` | string \| null | Attempt UUID when applicable |
| `ship_run_id` | string \| null | ShipRun UUID when applicable |
| `candidate_id` | string \| null | Promotion candidate UUID when applicable |
| `shipped_frontier_before` | string \| null | AgentHub hash before the observed transition |
| `shipped_frontier_after` | string \| null | Current `project.shipped_frontier` or ship tip |
| `validation_summary` | object | Evidence counts, test status, validation flags |
| `failure_reason` | string \| null | Set when `status` is `failed` |
| `needs` | array | What the caller should do next (see below) |
| `next_commands` | array of strings | Suggested `ta …` commands (may overlap with `needs`) |

### `status` values

- **`shipped`** — Work reached a shipped frontier or terminal ship success.
- **`failed`** — Terminal failure (validation, compose, or attempt rejected). Exit code **1**.
- **`needs_input`** — Waiting on an operator/agent step (choose winner, accept, compose, ship). Exit code **0**.
- **`running`** — Job or ShipRun still in progress. Exit code **0**.

### `needs` entries

Each item is an object:

```json
{
  "action": "choose_winner",
  "message": "Validated attempt is ready for winner selection.",
  "command": "ta ticket choose-winner <project> <ticket> <attempt>"
}
```

`command` is omitted when there is no single CLI fix (for example, waiting on a running job).

## Examples

### Ticket shipped (operator-loop complete)

```json
{
  "schema_version": 1,
  "status": "shipped",
  "project_id": "11111111-1111-1111-1111-111111111111",
  "ticket_id": "22222222-2222-2222-2222-222222222222",
  "attempt_id": "33333333-3333-3333-3333-333333333333",
  "ship_run_id": "44444444-4444-4444-4444-444444444444",
  "candidate_id": "55555555-5555-5555-5555-555555555555",
  "shipped_frontier_before": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "shipped_frontier_after": "cccccccccccccccccccccccccccccccccccccccc",
  "validation_summary": {
    "validated": true,
    "attempt_status": "validated",
    "shipped_frontier_at_evaluate": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "failure_reason": null,
  "needs": [],
  "next_commands": [
    "ta project show 11111111-1111-1111-1111-111111111111",
    "ta ticket attempts 11111111-1111-1111-1111-111111111111 22222222-2222-2222-2222-222222222222",
    "ta ship run 11111111-1111-1111-1111-111111111111 44444444-4444-4444-4444-444444444444"
  ]
}
```

### Attempt waiting on winner selection

```json
{
  "schema_version": 1,
  "status": "needs_input",
  "project_id": "11111111-1111-1111-1111-111111111111",
  "ticket_id": "22222222-2222-2222-2222-222222222222",
  "attempt_id": "33333333-3333-3333-3333-333333333333",
  "ship_run_id": null,
  "candidate_id": null,
  "shipped_frontier_before": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "shipped_frontier_after": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "validation_summary": {
    "validated": true,
    "attempt_status": "validated",
    "test_status": "passed"
  },
  "failure_reason": null,
  "needs": [
    {
      "action": "choose_winner",
      "message": "Validated attempt awaits winner selection.",
      "command": "ta ticket choose-winner 11111111-1111-1111-1111-111111111111 22222222-2222-2222-2222-222222222222 33333333-3333-3333-3333-333333333333"
    }
  ],
  "next_commands": []
}
```

### ShipRun ready to ship

```json
{
  "schema_version": 1,
  "status": "needs_input",
  "project_id": "11111111-1111-1111-1111-111111111111",
  "ticket_id": null,
  "attempt_id": null,
  "ship_run_id": "44444444-4444-4444-4444-444444444444",
  "candidate_id": "55555555-5555-5555-5555-555555555555",
  "shipped_frontier_before": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "shipped_frontier_after": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "validation_summary": {
    "ship_run_status": "ready_to_ship",
    "test_status": "passed"
  },
  "failure_reason": null,
  "needs": [
    {
      "action": "ship_run",
      "message": "ShipRun is ready to ship.",
      "command": "ta ship ship-run 11111111-1111-1111-1111-111111111111 44444444-4444-4444-4444-444444444444"
    }
  ],
  "next_commands": [
    "ta ship run 11111111-1111-1111-1111-111111111111 44444444-4444-4444-4444-444444444444",
    "ta ship candidates 11111111-1111-1111-1111-111111111111"
  ]
}
```

### Failed attempt (non-zero exit)

```json
{
  "schema_version": 1,
  "status": "failed",
  "project_id": "11111111-1111-1111-1111-111111111111",
  "ticket_id": "22222222-2222-2222-2222-222222222222",
  "attempt_id": "33333333-3333-3333-3333-333333333333",
  "ship_run_id": null,
  "candidate_id": null,
  "shipped_frontier_before": null,
  "shipped_frontier_after": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "validation_summary": {
    "validated": false,
    "attempt_status": "failed",
    "validation_error": "pytest failed"
  },
  "failure_reason": "pytest failed",
  "needs": [],
  "next_commands": []
}
```

## Building results in code

Library helpers live in `cli/_agent_result.py`. A future `ta run` command can reuse `build_result()` and the `result_from_*` mappers:

```python
from cli._agent_result import build_result, emit_agent_result

payload = build_result(
    status="needs_input",
    project_id=project_id,
    ticket_id=ticket_id,
    needs=[{"action": "wait", "message": "Job still running"}],
)
emit_agent_result(payload)
```

Do not parse human tables from stdout; use `--json` only and keep stderr separate for errors.
