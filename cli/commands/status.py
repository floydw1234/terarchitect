"""status subcommand: show the ticket work ledger."""

from cli._api import API
from cli._output import die, print_json, print_receipt


def register(subparsers) -> None:
    parser = subparsers.add_parser("status", help="Show the work ledger for one ticket")
    parser.add_argument("project_id")
    parser.add_argument("--ticket", dest="ticket_id", required=True, help="Ticket ID")
    parser.set_defaults(func=run)


def register_alias(subparsers) -> None:
    parser = subparsers.add_parser("chain", help="Alias for `ta status ... --ticket ...`")
    parser.add_argument("project_id")
    parser.add_argument("--ticket", dest="ticket_id", required=True, help="Ticket ID")
    parser.set_defaults(func=run)


def run(args, api: API) -> None:
    try:
        ledger = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/ledger")
    except Exception as exc:
        die(exc, output=args.output)
    if args.output == "json":
        print_json(ledger)
        return

    accepted_attempt = ledger.get("accepted_attempt") or {}
    candidate = ledger.get("promotion_candidate") or {}
    ship_run = ledger.get("ship_run") or {}
    evidence = ledger.get("evidence_summary") or {}
    print_receipt(
        "Ticket status",
        fields=[
            ("Ticket", (ledger.get("ticket") or {}).get("id", "") or "—"),
            ("Attempt", accepted_attempt.get("id", "") or "—"),
            ("Candidate", candidate.get("id", "") or "—"),
            ("ShipRun", ship_run.get("id", "") or "—"),
            ("Evidence", f"{evidence.get('bundle_count', 0)} bundle(s), {evidence.get('run_count', 0)} run(s)"),
        ],
        next_commands=ledger.get("next_commands") or [],
    )
    print("")
    print("Timeline:")
    for item in ledger.get("timeline") or []:
        label = item.get("label") or item.get("kind")
        kind = item.get("kind") or "event"
        suffix = f" ({item['status']})" if item.get("status") else ""
        print(f"  [{kind}] {label}{suffix}")

