# Terarchitect CLI Guide

This guide defines the shared CLI and API conventions that new Terarchitect commands should follow.

## Command naming

- Use a stable top-level command group such as `project`, `ticket`, `attempt`, `ship`, `workspace`, `graph`, or `plan`.
- Prefer verb-oriented subcommands: `list`, `show`, `create`, `update`, `delete`, `run`, `compose-candidate`.
- Keep backend-specific escape hatches out of operator-facing names unless there is no shared abstraction yet.

## Output modes

- `--output human|json` is the canonical global output selector.
- `--json` is a global alias for `--output json`.
- Command-local `--json` flags may remain temporarily for compatibility, but new commands should rely on the global flags first.
- JSON success output must stay parseable on `stdout`.
- Warnings and errors belong on `stderr`.

## Error envelope

Backend errors should preserve structured JSON fields when available:

- `error` or `message`
- `detail`
- `hint`
- `request_id`
- `phase`
- `next_commands`

`cli._api.APIError` is the shared carrier for that envelope. Commands should pass `APIError` objects to the shared renderer instead of flattening them to strings.

### Human rendering

Human-mode failures should be concise and actionable:

- primary message
- optional detail
- optional hint
- optional phase
- optional request ID
- optional `Next:` commands

Avoid bare output such as `API 502` when the backend returned richer context.

### JSON rendering

- JSON-mode failures should emit a structured error object on `stderr`.
- Exit nonzero on failure.
- Keep `stdout` free for machine-readable success payloads.

Example shape:

```json
{
  "error": {
    "status": 502,
    "message": "compose failed",
    "detail": "Validation blockers remain.",
    "hint": "Review the candidate blockers and retry compose.",
    "request_id": "req-123",
    "phase": "compose",
    "next_commands": [
      "ta ship candidates <project_id>"
    ]
  }
}
```

## Receipts and next actions

Use shared receipt helpers for operator-facing success output:

- a short title
- a compact field list
- a `Next:` section with copy-pastable commands

Current shared helpers live in `cli._output`:

- `print_receipt(...)`
- `print_table(...)`
- `print_json(...)`
- `die(...)`

This keeps later product-hardening lanes on one format without forcing deep refactors in every command up front.

## Adding commands

When adding a command:

1. Register it from `cli/__main__.py` through the appropriate command module.
2. Respect `args.output` for all success paths.
3. Route API failures through the shared renderer.
4. Prefer receipt helpers for human summaries and next actions.
5. Keep JSON payloads stable and machine-readable.

If a command needs a compatibility-only local `--json` flag, normalize it into `args.output = "json"` and keep the global alias working.

## Tests

Shared CLI changes should include focused tests for:

- parser behavior for `--output` and global `--json`
- structured `APIError` parsing from backend JSON
- human and JSON error rendering
- receipt and next-action helpers when behavior changes

Current targeted test entrypoints:

- `tests/test_cli_attempt.py`
- `tests/test_cli_output.py`
- `tests/test_cli_api_errors.py`

Run them with:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_cli_attempt.py tests/test_cli_output.py tests/test_cli_api_errors.py
```
