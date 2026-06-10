"""context subcommand: show the ticket agent context packet."""

from urllib.parse import urlencode

from cli._api import API
from cli._output import die, print_json, print_receipt


def register(subparsers) -> None:
    parser = subparsers.add_parser("context", help="Show the agent/operator context packet for one ticket")
    parser.add_argument("project_id")
    parser.add_argument("--ticket", dest="ticket_id", required=True, help="Ticket ID")
    parser.add_argument("--agent", action="store_true", help="Include worker-context fields")
    parser.set_defaults(func=run)


def run(args, api: API) -> None:
    query = urlencode({"agent": "true" if args.agent else "false"})
    try:
        payload = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/context?{query}")
    except Exception as exc:
        die(exc, output=args.output)
    if args.output == "json":
        print_json(payload)
        return

    paths = payload.get("paths") or {}
    print_receipt(
        "Agent context",
        fields=[
            ("Ticket", (payload.get("ticket") or {}).get("id", "")),
            ("Project", (payload.get("project") or {}).get("name", "")),
            ("Attempts", len(payload.get("attempts") or [])),
            ("Runner WD", paths.get("runner_workdir_hint") or "—"),
        ],
        next_commands=payload.get("next_commands") or [],
    )
    print("")
    print("Channels:")
    channels = payload.get("channels") or {}
    for key in ("project", "ticket", "wave"):
        if channels.get(key):
            print(f"  {key}: {channels[key]}")
    print("")
    print("Recent events:")
    for event in payload.get("recent_events") or []:
        print(f"  [{event.get('event_type')}] {event.get('message')}")
    print("")
    print("Paths:")
    print(f"  Project:     {paths.get('project_path') or '—'}")
    print(f"  Runner WD:   {paths.get('runner_workdir_hint') or '—'}")
    for hint in paths.get("recovery_artifact_hints") or []:
        print(f"  Recovery:    {hint}")
