"""ticket subcommand: list | create | show | update | run | cancel | logs"""

import os
import subprocess
import sys
import time

from cli._api import API, APIError
from cli._config import load_config_file
from cli._output import die, print_json, print_receipt, print_table, short_id

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
    cr.add_argument("--rationale", help="Why this intent matters")
    cr.add_argument("--acceptance-criteria", dest="acceptance_criteria",
                    help="What done looks like")
    cr.add_argument("--constraints", help="Limits and non-goals for the agent")
    cr.add_argument("--column", default="backlog",
                    help="Column ID (default: backlog)")
    cr.add_argument("--priority", default="medium",
                    choices=["low", "medium", "high"],
                    help="Priority (default: medium)")
    cr.add_argument("--intent-status", dest="intent_status",
                    choices=["draft", "ready", "active", "blocked", "archived"],
                    default="ready",
                    help="Intent status (default: ready)")

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
    up.add_argument("--rationale")
    up.add_argument("--acceptance-criteria", dest="acceptance_criteria")
    up.add_argument("--constraints")
    up.add_argument("--column", dest="column_id")
    up.add_argument("--priority", choices=["low", "medium", "high"])
    up.add_argument("--intent-status", dest="intent_status",
                    choices=["draft", "ready", "active", "blocked", "archived"])
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

    at = sub.add_parser("attempts", help="List attempts for a ticket")
    at.add_argument("project_id")
    at.add_argument("ticket_id")
    at.add_argument("--json", action="store_true", help="Print JSON output")

    aa = sub.add_parser("accept-attempt", help="Accept a ticket attempt")
    aa.add_argument("project_id")
    aa.add_argument("ticket_id")
    aa.add_argument("attempt_id")
    aa.add_argument("--reason", help="Operator note recorded client-side for context")
    aa.add_argument("--json", action="store_true", help="Print JSON output")

    ra = sub.add_parser("reject-attempt", help="Reject a ticket attempt")
    ra.add_argument("project_id")
    ra.add_argument("ticket_id")
    ra.add_argument("attempt_id")
    ra.add_argument("--reason", required=True, help="Reason sent to the backend")
    ra.add_argument("--json", action="store_true", help="Print JSON output")

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
    elif cmd == "attempts":
        _cmd_attempts(args, api)
    elif cmd == "accept-attempt":
        _cmd_accept_attempt(args, api)
    elif cmd == "reject-attempt":
        _cmd_reject_attempt(args, api)


# ---------------------------------------------------------------------------

def _cmd_list(args, api: API) -> None:
    try:
        tickets = api.get(f"/api/projects/{args.project_id}/tickets")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(tickets)
        return
    rows = [
        {
            "id": short_id(t.get("id", "")),
            "title": t.get("title", ""),
            "state": t.get("display_state") or t.get("column_id", ""),
            "intent": t.get("intent_status", "ready"),
            "priority": t.get("priority", ""),
            "attempt": (t.get("latest_attempt") or {}).get("short_commit_hash") or "",
        }
        for t in (tickets or [])
    ]
    print_table(rows, [
        ("id", "ID"),
        ("title", "TITLE"),
        ("state", "STATE"),
        ("intent", "INTENT"),
        ("priority", "PRIORITY"),
        ("attempt", "LATEST"),
    ])


def _apply_json_flag(args) -> None:
    if getattr(args, "json", False):
        args.output = "json"


def _cmd_create(args, api: API) -> None:
    if args.file:
        data = load_config_file(args.file)
        ticket_defs = data if isinstance(data, list) else [data]
    elif args.title:
        ticket_defs = [{
            "title": args.title,
            "description": getattr(args, "description", None),
            "rationale": getattr(args, "rationale", None),
            "acceptance_criteria": getattr(args, "acceptance_criteria", None),
            "constraints": getattr(args, "constraints", None),
            "column_id": args.column,
            "priority": args.priority,
            "intent_status": getattr(args, "intent_status", "ready"),
            "status": "todo",
        }]
    else:
        die("Provide --title or --file", output=args.output)

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
            # Intent fields
            "intent_status": td.get("intent_status", "ready"),
            "rationale": td.get("rationale"),
            "acceptance_criteria": td.get("acceptance_criteria"),
            "constraints": td.get("constraints"),
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
        die(e, output=args.output)
    if args.output == "json":
        print_json(ticket)
        return
    for k, v in ticket.items():
        if v is not None:
            print(f"  {k}: {v}")


def _render_attempt_row(attempt: dict) -> dict[str, str]:
    return {
        "id": short_id(attempt.get("id", "")),
        "status": attempt.get("status", ""),
        "commit": attempt.get("short_commit_hash") or "",
        "base": (attempt.get("base_hash") or "")[:12],
        "attempt": str(attempt.get("attempt_num", "")),
        "tests": attempt.get("test_status") or "",
    }


def _cmd_attempts(args, api: API) -> None:
    _apply_json_flag(args)
    try:
        attempts = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(attempts)
        return
    if not attempts:
        print("No attempts found for this ticket.")
        return
    print_table([_render_attempt_row(a) for a in attempts], [
        ("id", "ID"),
        ("status", "STATUS"),
        ("commit", "COMMIT"),
        ("base", "BASE"),
        ("attempt", "ATTEMPT"),
        ("tests", "TESTS"),
    ])
    latest = attempts[0]
    print("")
    print("Next:")
    print(f"  ta attempt show {args.project_id} {latest.get('id')}")
    print(f"  ta attempt diff {args.project_id} {latest.get('id')}")
    if latest.get("status") not in {"accepted", "composed", "release_pr_open", "shipped"}:
        print(f"  ta ticket accept-attempt {args.project_id} {args.ticket_id} {latest.get('id')}")
    if latest.get("status") not in {"rejected", "shipped", "superseded", "failed"}:
        print(f"  ta ticket reject-attempt {args.project_id} {args.ticket_id} {latest.get('id')} --reason \"needs revision\"")


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
    if getattr(args, "intent_status", None):
        payload["intent_status"] = args.intent_status
    if getattr(args, "rationale", None):
        payload["rationale"] = args.rationale
    if getattr(args, "acceptance_criteria", None):
        payload["acceptance_criteria"] = args.acceptance_criteria
    if getattr(args, "constraints", None):
        payload["constraints"] = args.constraints
    if not payload:
        die("No fields to update.", output=args.output)
    try:
        ticket = api.patch(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}", payload
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(ticket)
        return
    print(f"Updated ticket {args.ticket_id}")


def _cmd_accept_attempt(args, api: API) -> None:
    _apply_json_flag(args)
    if args.reason and args.output != "json":
        print("Note: --reason is not sent by the current accept endpoint; recording it only in CLI output.")
    try:
        attempt = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts/{args.attempt_id}/accept",
            {},
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(attempt)
        return
    print_receipt(
        f"Accepted attempt {short_id(attempt.get('id', args.attempt_id))} for ticket {short_id(args.ticket_id)}.",
        fields=[
            ("Status", attempt.get("status")),
            ("Commit", attempt.get("short_commit_hash") or attempt.get("agenthub_commit_hash") or "unavailable"),
        ],
        next_commands=[
            f"ta attempt show {args.project_id} {attempt.get('id', args.attempt_id)}",
            f"ta ship candidates {args.project_id}",
        ],
    )


def _cmd_reject_attempt(args, api: API) -> None:
    _apply_json_flag(args)
    try:
        attempt = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts/{args.attempt_id}/reject",
            {"reason": args.reason},
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(attempt)
        return
    print_receipt(
        f"Rejected attempt {short_id(attempt.get('id', args.attempt_id))} for ticket {short_id(args.ticket_id)}.",
        fields=[
            ("Status", attempt.get("status")),
            ("Reason", args.reason),
        ],
        next_commands=[
            f"ta ticket attempts {args.project_id} {args.ticket_id}",
            f"ta ticket run {args.project_id} {args.ticket_id}",
        ],
    )


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
        die(e, output=args.output)

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
    die("Timed out waiting for ticket to complete.", output=args.output)


def _run_local(args, api: API) -> None:
    """Spawn agent.agent_runner directly on this host (dev mode)."""
    # Fetch project for REPO_URL
    try:
        project = api.get(f"/api/projects/{args.project_id}")
    except APIError as e:
        die(e, output=args.output)

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
        die(e, output=args.output)
    print(f"Cancellation requested for ticket {args.ticket_id}")


def _cmd_logs(args, api: API) -> None:
    try:
        logs = api.get(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/logs"
        )
    except APIError as e:
        die(e, output=args.output)
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
