"""ticket subcommand: list | create | show | update | run | cancel | logs"""

import os
import subprocess
import sys
import time
from cli._api import API, APIError
from cli._config import load_config_file
from cli._output import die, print_json, print_table, short_id

_POLL_INTERVAL = 5   # seconds between status checks for --wait
_WAIT_TIMEOUT  = 3600  # max seconds to wait


def register(subparsers) -> None:
    p = subparsers.add_parser("ticket", help="Manage tickets")
    sub = p.add_subparsers(dest="ticket_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list
    li = sub.add_parser("list", help="List tickets for a project")
    li.add_argument("project_id")

    # create
    cr = sub.add_parser("create", help="Create one or more tickets")
    cr.add_argument("project_id")
    cr.add_argument("--file", "-f", metavar="FILE",
                    help="JSON/YAML file with a ticket object or array of tickets")
    cr.add_argument("--title", "-t", help="Ticket title")
    cr.add_argument("--description", "-d", help="Ticket description")
    cr.add_argument("--column", default="backlog",
                    help="Column ID (default: backlog)")
    cr.add_argument("--priority", default="medium",
                    choices=["low", "medium", "high"],
                    help="Priority (default: medium)")

    # show
    sh = sub.add_parser("show", help="Show ticket details")
    sh.add_argument("project_id")
    sh.add_argument("ticket_id")

    # update
    up = sub.add_parser("update", help="Update a ticket")
    up.add_argument("project_id")
    up.add_argument("ticket_id")
    up.add_argument("--title")
    up.add_argument("--description")
    up.add_argument("--column", dest="column_id")
    up.add_argument("--priority", choices=["low", "medium", "high"])
    up.add_argument("--status")

    # run
    ru = sub.add_parser("run", help="Enqueue a ticket for agent execution")
    ru.add_argument("project_id")
    ru.add_argument("ticket_id")
    ru.add_argument("--wait", action="store_true",
                    help="Poll until the job completes or fails")
    ru.add_argument("--run-local", action="store_true",
                    help="Run agent directly on this host (dev mode, no coordinator needed)")

    # cancel
    ca = sub.add_parser("cancel", help="Request cancellation of a running ticket")
    ca.add_argument("project_id")
    ca.add_argument("ticket_id")

    # logs
    lo = sub.add_parser("logs", help="Fetch execution logs for a ticket")
    lo.add_argument("project_id")
    lo.add_argument("ticket_id")
    lo.add_argument("--raw", action="store_true", help="Include raw_output field")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.ticket_cmd
    if cmd == "list":
        _cmd_list(args, api)
    elif cmd == "create":
        _cmd_create(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "update":
        _cmd_update(args, api)
    elif cmd == "run":
        _cmd_run(args, api)
    elif cmd == "cancel":
        _cmd_cancel(args, api)
    elif cmd == "logs":
        _cmd_logs(args, api)


# ---------------------------------------------------------------------------

def _cmd_list(args, api: API) -> None:
    try:
        tickets = api.get(f"/api/projects/{args.project_id}/tickets")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(tickets)
        return
    rows = [
        {
            "id": short_id(t.get("id", "")),
            "title": t.get("title", ""),
            "column": t.get("column_id", ""),
            "priority": t.get("priority", ""),
            "running": "yes" if t.get("is_running") else "",
        }
        for t in (tickets or [])
    ]
    print_table(rows, [
        ("id", "ID"),
        ("title", "TITLE"),
        ("column", "COLUMN"),
        ("priority", "PRIORITY"),
        ("running", "RUNNING"),
    ])


def _cmd_create(args, api: API) -> None:
    if args.file:
        data = load_config_file(args.file)
        ticket_defs = data if isinstance(data, list) else [data]
    elif args.title:
        ticket_defs = [{
            "title": args.title,
            "description": getattr(args, "description", None),
            "column_id": args.column,
            "priority": args.priority,
            "status": "todo",
        }]
    else:
        die("Provide --title or --file")

    created = []
    for td in ticket_defs:
        payload = {
            "title": td.get("title", ""),
            "column_id": td.get("column_id", td.get("column", "backlog")),
            "description": td.get("description"),
            "priority": td.get("priority", "medium"),
            "status": td.get("status", "todo"),
            "associated_node_ids": td.get("associated_node_ids", []),
            "associated_edge_ids": td.get("associated_edge_ids", []),
            "depends_on_ticket_ids": td.get("depends_on_ticket_ids", []),
        }
        try:
            ticket = api.post(f"/api/projects/{args.project_id}/tickets", payload)
            created.append(ticket)
        except APIError as e:
            print(f"Failed to create '{td.get('title')}': {e}", file=sys.stderr)

    if args.output == "json":
        print_json(created if len(created) != 1 else created[0])
        return
    for t in created:
        print(f"Created ticket {t['id']}: {t.get('title')}")


def _cmd_show(args, api: API) -> None:
    try:
        ticket = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(ticket)
        return
    for k, v in ticket.items():
        if v is not None:
            print(f"  {k}: {v}")


def _cmd_update(args, api: API) -> None:
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.description:
        payload["description"] = args.description
    if getattr(args, "column_id", None):
        payload["column_id"] = args.column_id
    if getattr(args, "priority", None):
        payload["priority"] = args.priority
    if getattr(args, "status", None):
        payload["status"] = args.status
    if not payload:
        die("No fields to update.")
    try:
        ticket = api.patch(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}", payload
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(ticket)
        return
    print(f"Updated ticket {args.ticket_id}")


def _cmd_run(args, api: API) -> None:
    if args.run_local:
        _run_local(args, api)
        return

    # Enqueue by moving to in_progress
    try:
        ticket = api.patch(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}",
            {"column_id": "in_progress"},
        )
    except APIError as e:
        die(str(e))

    print(f"Ticket {args.ticket_id} enqueued (column: in_progress)")

    if not args.wait:
        print("The coordinator will pick it up shortly. Use --wait to poll.")
        return

    # Poll until no longer running / no longer in_progress
    print("Waiting for agent to complete", end="", flush=True)
    deadline = time.time() + _WAIT_TIMEOUT
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        print(".", end="", flush=True)
        try:
            ticket = api.get(
                f"/api/projects/{args.project_id}/tickets/{args.ticket_id}"
            )
        except APIError:
            continue
        if not ticket.get("is_running") and ticket.get("column_id") != "in_progress":
            print()
            col = ticket.get("column_id", "?")
            print(f"Done. Ticket moved to: {col}")
            if args.output == "json":
                print_json(ticket)
            return
    print()
    die("Timed out waiting for ticket to complete.")


def _run_local(args, api: API) -> None:
    """Spawn agent.agent_runner directly on this host (dev mode)."""
    # Fetch project for REPO_URL
    try:
        project = api.get(f"/api/projects/{args.project_id}")
    except APIError as e:
        die(str(e))

    repo_url = project.get("github_url") or ""
    api_url = api.base_url

    # Repo root is two levels above this file: cli/../
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    env = {
        **os.environ,
        "TICKET_ID": args.ticket_id,
        "PROJECT_ID": args.project_id,
        "TERARCHITECT_API_URL": api_url,
        "REPO_URL": repo_url,
    }

    print(f"Running agent locally for ticket {args.ticket_id}")
    result = subprocess.run(
        [sys.executable, "-m", "agent.agent_runner", "ticket"],
        cwd=repo_root,
        env=env,
    )
    sys.exit(result.returncode)


def _cmd_cancel(args, api: API) -> None:
    try:
        api.post(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/cancel")
    except APIError as e:
        die(str(e))
    print(f"Cancellation requested for ticket {args.ticket_id}")


def _cmd_logs(args, api: API) -> None:
    try:
        logs = api.get(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/logs"
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(logs)
        return
    if not logs:
        print("(no logs yet)")
        return
    for entry in logs:
        ts = (entry.get("created_at") or "")[:19].replace("T", " ")
        step = (entry.get("step") or "").ljust(28)
        summary = entry.get("summary") or ""
        ok = "✓" if entry.get("success", True) else "✗"
        print(f"[{ts}] {ok} {step}  {summary}")
        if args.raw and entry.get("raw_output"):
            print("    " + entry["raw_output"].replace("\n", "\n    "))
