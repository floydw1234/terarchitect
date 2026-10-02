"""Top-level `ta run` — create ticket, dispatch, wait, and ship."""

from __future__ import annotations

import time

from cli._api import API, APIError
from cli._operator_loop import run_operator_ship_loop
from cli._output import die, print_json, print_receipt, short_id
from cli.commands import ticket as ticket_cmd

_POLL_INTERVAL = 5
_DEFAULT_TIMEOUT = 3600


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "run",
        help="Create a ticket from a goal, dispatch attempts, wait, and ship the winner",
    )
    p.add_argument("project_id", help="Project ID")
    p.add_argument("goal", help="Ticket title / goal text")
    p.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT,
        help=f"Max seconds to wait for completion and ship (default {_DEFAULT_TIMEOUT})",
    )
    p.add_argument(
        "--attempt-count",
        type=int,
        default=None,
        help="Parallel attempt count (default: project/ticket default, usually 3)",
    )
    p.set_defaults(func=_cmd_run)


def _ticket_batch_settled(api: API, project_id: str, ticket_id: str) -> bool:
    try:
        ticket = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
    except APIError:
        return False
    if ticket.get("is_running"):
        return False
    if ticket.get("column_id") == "in_progress":
        return False
    return True


def _pick_validated_attempt_id(api: API, project_id: str, ticket_id: str) -> str | None:
    try:
        attempts = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}/attempts")
    except APIError:
        return None
    for item in attempts or []:
        status = (item.get("status") or "").lower()
        if status == "validated" or item.get("validated"):
            return str(item.get("id") or "")
    return None


def _wait_for_ship(
    api: API,
    project_id: str,
    ticket_id: str,
    frontier_before: str | None,
    deadline: float,
) -> dict | None:
    while time.time() < deadline:
        project = ticket_cmd._get_project(api, project_id, output="json")
        frontier_after = ticket_cmd._get_shipped_frontier(project)
        if frontier_before and frontier_after and frontier_after != frontier_before:
            return {
                "status": "shipped",
                "shipped_frontier_before": frontier_before,
                "shipped_frontier_after": frontier_after,
                "shipped_frontier": frontier_after,
            }
        try:
            attempts = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}/attempts")
        except APIError:
            attempts = []
        if any((a.get("status") or "").lower() == "shipped" for a in attempts or []):
            project = ticket_cmd._get_project(api, project_id, output="json")
            frontier_after = ticket_cmd._get_shipped_frontier(project)
            return {
                "status": "shipped",
                "shipped_frontier_before": frontier_before,
                "shipped_frontier_after": frontier_after,
                "shipped_frontier": frontier_after,
            }
        time.sleep(_POLL_INTERVAL)
    return None


def _cmd_run(args, api: API) -> None:
    project = ticket_cmd._get_project(api, args.project_id, output="json")
    frontier_before = ticket_cmd._get_shipped_frontier(project)

    try:
        ticket = api.post(
            f"/api/projects/{args.project_id}/tickets",
            {
                "column_id": "backlog",
                "title": args.goal,
                "intent_status": "ready",
            },
        )
    except APIError as e:
        die(e, output=args.output)

    ticket_id = str(ticket.get("id") or "")
    if not ticket_id:
        die("Ticket create returned no id.", output=args.output)

    body: dict = {"column_id": "in_progress"}
    if args.attempt_count is not None:
        try:
            api.patch(
                f"/api/projects/{args.project_id}/tickets/{ticket_id}",
                {"default_attempt_count": args.attempt_count},
            )
        except APIError as e:
            die(e, output=args.output)
    try:
        api.patch(f"/api/projects/{args.project_id}/tickets/{ticket_id}", body)
    except APIError as e:
        die(e, output=args.output)

    deadline = time.time() + max(1, int(args.timeout))
    while time.time() < deadline:
        if _ticket_batch_settled(api, args.project_id, ticket_id):
            break
        time.sleep(_POLL_INTERVAL)
    else:
        die("Timed out waiting for ticket attempts to finish.", output=args.output)

    shipped = _wait_for_ship(api, args.project_id, ticket_id, frontier_before, deadline)
    if shipped is not None:
        payload = {
            "project_id": args.project_id,
            "ticket_id": ticket_id,
            "goal": args.goal,
            **shipped,
        }
        if args.output == "json":
            print_json(payload)
            return
        print_receipt(
            "Run shipped",
            fields=[
                ("Ticket", short_id(ticket_id)),
                ("Frontier", (shipped.get("shipped_frontier") or "")[:12]),
            ],
        )
        return

    attempt_id = _pick_validated_attempt_id(api, args.project_id, ticket_id)
    if not attempt_id:
        die(
            "Ticket finished without a validated attempt to ship.",
            output=args.output,
        )

    try:
        result = run_operator_ship_loop(
            api,
            project_id=args.project_id,
            ticket_id=ticket_id,
            attempt_id=attempt_id,
            sync_compose=True,
            expect_frontier=frontier_before,
        )
    except APIError as e:
        die(e, output=args.output)

    if (result.get("status") or "").lower() != "shipped" and not result.get("shipped_commit_hash"):
        die(
            APIError(1, "Run did not reach a shipped frontier.", detail=str(result.get("status"))),
            output=args.output,
        )

    payload = {
        "project_id": args.project_id,
        "ticket_id": ticket_id,
        "goal": args.goal,
        "attempt_id": attempt_id,
        **result,
    }
    if args.output == "json":
        print_json(payload)
        return
    print_receipt(
        "Run shipped",
        fields=[
            ("Ticket", short_id(ticket_id)),
            ("Attempt", short_id(attempt_id)),
            ("Frontier", (result.get("shipped_frontier_after") or "")[:12]),
        ],
    )
