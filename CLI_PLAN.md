# Terarchitect CLI — High-Level Plan

## Goal

Add a **top-level `cli/`** (or equivalent) folder that provides a CLI so users can perform the same operations they do in the UI—create projects from config, manage graphs and tickets from JSON, run tickets, and handle PR review/approve/merge—without opening the dashboard.

---

## Scope (UI Parity)

The CLI should mirror these UI-backed capabilities:

| Capability | UI behavior | CLI equivalent |
|------------|-------------|-----------------|
| **Projects** | Create project (name, description, github_url, execution_mode, project_path, is_existing_repo) | Create project from options or from a **config file** (e.g. YAML/JSON) |
| **Graph** | Edit graph (nodes/edges) in Graph Editor | **Create/update graph from JSON file** (e.g. `cli graph load <project-id> --file graph.json`) |
| **Tickets** | Create ticket (column_id, title, description, node/edge ids, priority, status) | **Create ticket from JSON** (single or batch from file) |
| **Run ticket** | Move ticket to “In Progress” → app enqueues job → coordinator runs agent | **Run ticket**: move to in_progress (enqueue) and optionally **wait/poll** until job completes or **trigger direct run** (dev mode) |
| **Review** | View PR summary, commits, test files, comments | **Review PR**: fetch and display review data for a ticket |
| **Approve PR** | Approve the ticket’s PR (optional body) | **Approve PR** for a ticket |
| **Merge PR** | Merge the ticket’s PR (merge/squash/rebase) | **Merge PR** for a ticket |
| **Other** | List projects, get/update project, list tickets, update ticket, cancel run, notes, settings | List/get/update where useful for scripting |

“Create project from config” is interpreted as: **create a project using a config file** that specifies at least name and optionally description, github_url, execution_mode, project_path, is_existing_repo, and optionally path to default tickets JSON or inline default tickets.

---

## Proposed Layout

- **Location**: top-level folder `cli/` in the repo root (sibling to `backend/`, `frontend/`, `agent/`, `coordinator/`).
- **Entrypoint**: single executable entrypoint, e.g. `cli/__main__.py` or a `terarchitect` (or `ta`) console script, so users run:
  - `python -m cli ...` from repo root, or
  - `terarchitect project create ...` if installed as a package.
- **API usage**: CLI talks to the **existing Flask API** only (same base URL as the UI, e.g. `TERARCHITECT_API_URL` or default `http://localhost:5010`). No direct DB or Flask app imports in the CLI; keeps CLI decoupled and usable against a remote app.
- **Auth**: For worker-style operations (e.g. if we add “run ticket locally” that calls worker context or complete endpoints), support `TERARCHITECT_WORKER_API_KEY`; for PR approve/merge/comment the backend uses stored GitHub token / `gh` CLI (same as UI). CLI need not hold tokens; it just calls the API.

---

## Commands (High Level)

1. **Project**
   - `project list` — list projects (GET /api/projects).
   - `project create [--config FILE] [--name NAME] [--description DESC] [--github-url URL] [--execution-mode docker|local] [--project-path PATH] [--existing-repo]` — create project; either from config file or from flags. Config file can specify `default_tickets_path` or `default_tickets` inline to create tickets (backend already supports default_tickets from a path for new projects).
   - `project show <project-id>` — get project (GET /api/projects/<id>).
   - `project update <project-id> [options]` — update project (PUT).
   - `project delete <project-id> [--confirm NAME]` — delete project (DELETE with confirm).

2. **Graph**
   - `graph get <project-id>` — output current graph JSON (GET /api/projects/<id>/graph).
   - `graph set <project-id> --file FILE` — set graph from JSON file (PUT). File format: `{"nodes": [...], "edges": [...]}` (match backend/UI shape).

3. **Ticket**
   - `ticket list <project-id>` — list tickets (GET /api/projects/<id>/tickets).
   - `ticket create <project-id> [--file FILE] | [--title TITLE --column COLUMN_ID ...]` — create one ticket from flags or one/many from JSON file (POST per ticket; file can be single object or array).
   - `ticket show <project-id> <ticket-id>` — get ticket (GET).
   - `ticket update <project-id> <ticket-id> [options]` — update ticket (PATCH).
   - `ticket run <project-id> <ticket-id>` — move ticket to “In Progress” (PATCH column_id to in_progress), which enqueues the job (same as UI). Optional flags: `--wait` to poll until job completes/fails, `--run-local` to run agent on host (e.g. `python -m agent.agent_runner ticket` with env from API) for dev.
   - `ticket cancel <project-id> <ticket-id>` — request cancel (POST cancel).
   - `ticket logs <project-id> <ticket-id>` — fetch and print execution logs (GET logs).

4. **Review (PR)**
   - `review list <project-id>` — list tickets with PRs and status (GET /api/projects/<id>/review).
   - `review show <project-id> <ticket-id>` — show PR summary, commits, test files, comments (GET ticket review).
   - `review comment <project-id> <ticket-id> --body "..."` — post comment on PR (POST review/comment).
   - `review approve <project-id> <ticket-id> [--body "..."]` — approve PR (POST review/approve).
   - `review merge <project-id> <ticket-id> [--method merge|squash|rebase]` — merge PR (POST review/merge).

5. **Optional / later**
   - **Kanban**: `kanban get/set <project-id>` if we want to edit columns from CLI.
   - **Notes**: list/create/update/delete notes (CRUD) for scripting.
   - **Settings**: get/check/update app settings (e.g. for automation or bootstrap).

---

## Config File Shape (Create Project “from config”)

Example **project config** (YAML or JSON) for `project create --config project.yaml`:

```yaml
name: "My Project"
description: "Optional description"
github_url: "https://github.com/org/repo"
execution_mode: "docker"   # or "local"
project_path: null        # for local mode: path on host
is_existing_repo: false   # if true, no default_tickets created

# Optional: override path to default tickets JSON (backend has default_tickets.json)
# default_tickets_path: "./backend/config/default_tickets.json"
# Or inline default tickets (same shape as default_tickets.json):
# default_tickets:
#   - title: "Project setup"
#     description: "..."
#     priority: "medium"
#     status: "todo"
#     associated_node_ids: ["*"]
#     associated_edge_ids: ["*"]
```

Backend today creates default tickets only when `is_existing_repo` is false and reads from a fixed path. For “create from config” we have two options:

- **A)** CLI sends the same POST as the UI; if we want “custom default tickets” from config, we either (1) add an optional `default_tickets` array to POST body and have the backend accept it, or (2) have the CLI create the project then POST tickets in a loop from the config file. Option (2) needs no backend change.
- **B)** Keep backend as-is: CLI creates project with existing fields, then if config has `default_tickets`, CLI creates tickets via POST /api/projects/<id>/tickets. No backend change.

Recommendation: **B** for minimal backend change; document the config shape and that “default_tickets” in config is applied by the CLI after project creation.

---

## Technical Choices

- **Language**: Python 3.11+ to match backend/agent, reuse `requests` (or `urllib`) for HTTP. No React/Node in CLI.
- **Argument parsing**: `argparse` or `click` (or Typer) for subcommands and flags. Prefer one dependency and clear help.
- **Output**: Human-friendly stdout (tables, key-value, or JSON). Support `--output json` for scripting (e.g. `ticket list --output json`).
- **Base URL**: Env `TERARCHITECT_API_URL` (default `http://localhost:5010`). Same as coordinator/agent.
- **Errors**: Non-zero exit code on API errors; print message to stderr; optional `--verbose` for debug.

---

## Out of Scope (for this plan)

- **No direct DB access** from CLI; everything via REST.
- **No new API routes** required for the first version (use existing routes). Optional later: backend could add `default_tickets` in POST /api/projects body if we want one-shot create.
- **Coordinator / agent execution**: “Run ticket” means “enqueue” by default (PATCH to in_progress). Optional `--run-local` can spawn `python -m agent.agent_runner ticket` with env vars (TICKET_ID, PROJECT_ID, TERARCHITECT_API_URL, REPO_URL, etc.) for developers who don’t run the coordinator.
- **PR review polling**: Remain in the backend; CLI only triggers “comment”, “approve”, “merge” and “review show/list”.

---

## File Structure (suggestion)

```
cli/
  __main__.py          # Entry: parse subcommand, dispatch to commands
  _api.py              # Low-level HTTP client (base URL, get/post/put/patch/delete)
  _config.py           # Load project config (YAML/JSON), env (TERARCHITECT_API_URL)
  commands/
    __init__.py
    project.py         # project list | create | show | update | delete
    graph.py           # graph get | set
    ticket.py          # ticket list | create | show | update | run | cancel | logs
    review.py          # review list | show | comment | approve | merge
  # Optional: formatters (table, json) in _output.py
```

---

## Success Criteria

- User can create a project from a config file and optionally create tickets from the same file.
- User can load a graph from a JSON file and set it for a project.
- User can create one or more tickets from a JSON file.
- User can “run” a ticket (enqueue), then review/approve/merge the PR from the CLI.
- All of the above work against the existing Flask API; no backend changes required for the minimal set (optional backend enhancement for `default_tickets` in project create).
- Clear `--help` and optional `--output json` for scripting.

---

## Next Steps After Review

1. Create `cli/` and `_api.py` + `_config.py`.
2. Implement project commands (list, create from config/flags, show, update, delete).
3. Implement graph get/set and ticket list/create/show/update/run/cancel/logs.
4. Implement review list/show/comment/approve/merge.
5. Add tests under `tests/` for CLI (e.g. invoke CLI with mock or live API, or snapshot JSON output).
6. Document in main README or `docs/CLI.md` (usage, env vars, config file format).
