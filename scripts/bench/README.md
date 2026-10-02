# Terarchitect comparison harness

Records side-by-side outcomes for **Terarchitect** (`ta` spine) vs a **baseline** Cursor CLI agent on the same ticket prompts. This directory is the harness only—not a full benchmark execution environment (no coordinator, AgentHub, or live agent runs in CI).

## What it measures (per ticket)

| Field | Meaning |
|-------|---------|
| `wall_time_sec` | End-to-end wall time for the mode’s run (including CI when not dry-run) |
| `cost_usd` / `tokens_in` / `tokens_out` | Parsed from agent CLI output when present |
| `ci_pass` | `make ci-python` in the relevant tree |
| `shipped` | Terarchitect: inferred from operator-loop / `ta run` output; baseline: always `no` |
| `files_changed`, `lines_added`, `lines_removed` | `git diff --numstat` vs `origin/main` (or vs `HEAD` in scratch worktrees) |

Results are written to `results.csv` and `results.md` (markdown table).

## Ticket specs

Edit `tickets.yaml`. Each ticket needs `id`, `title`, and `prompt`. For **terarchitect** mode, set `terarchitect.project_id`, `terarchitect.ticket_id`, and (for operator-loop) `terarchitect.attempt_id`.

## Usage

From the repo root (after `make setup-venv`):

```bash
# Plan a baseline run (no agent, no CI)
make bench-dry-run

# Or directly:
.venv/bin/python scripts/bench/run_compare.py --mode baseline --dry-run

# Baseline: agent in a scratch git worktree from origin/main (requires `agent` on PATH)
.venv/bin/python scripts/bench/run_compare.py --mode baseline \
  --output-dir bench-results/my-run

# Terarchitect: uses `ta run` when available, else `ta ship operator-loop` when IDs are set
.venv/bin/python scripts/bench/run_compare.py --mode terarchitect \
  scripts/bench/tickets.yaml
```

Options:

- `--dry-run` — load YAML and print planned commands only
- `--ta-bin /path/to/ta` — default is `python -m cli` from `--repo-root`
- `--repo-root` — defaults to the Terarchitect checkout containing this script

## Modes

### `baseline`

1. `git fetch origin main` and `git worktree add` at `origin/main`
2. `agent -p --force --model composer-2.5 --output-format text "<prompt>"`
3. `make ci-python` in the worktree
4. Diff stats, then remove the worktree

### `terarchitect`

1. If `ta run --help` succeeds → `ta --json run <project_id> "<title>" --description "<prompt>" [--attempt-count N] --timeout T` (needs `project_id` in YAML or `--project-id`). The agent-result envelope gives shipped status and frontier before/after; the shipped commit is fetched from AgentHub (`TERARCHITECT_AGENTHUB_URL`, `AGENTHUB_API_KEY`) into a scratch worktree for `make ci-python` and diff stats.
2. Else if `project_id`, `ticket_id`, and `attempt_id` are set → `ta ship operator-loop …`
3. Else exit with a clear **not yet supported** error (full unattended `ta ticket run` → ship is not wired here)

## Tests

Unit tests live in `tests/test_bench_harness.py` (mocked subprocess; no real agent calls). They run under `make ci-python`.
