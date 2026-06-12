"""
Terarchitect CLI — entry point.

Usage:
    python -m cli [--api-url URL] [--output json] COMMAND SUBCOMMAND [args]

Commands:
    project    list | create | show | doctor | update | delete | import-agenthub-root
               | migration status | migration set-frontier | migration backfill-ticket-bases
               | migration import-agenthub-root
    ticket     list | create | show | update | run | cancel | logs
               | attempts | accept-attempt | reject-attempt
               (Intent fields: --rationale, --acceptance-criteria, --constraints, --intent-status)
    attempt    list | show | files | diff
    status     <project-id> --ticket <ticket-id>
    context    <project-id> --ticket <ticket-id> [--agent]
    chain      alias for status
    ship       candidates | candidate | compose-candidate | run | ship-run | ship-candidate | feedback
    workspace  leaves | list | create | show | compose | analyze | bless | promote | discard
    graph      get | set
    plan       <project-id>  — generate tickets from graph + notes via LLM

Product model:
    Tickets are intents: goal, rationale, acceptance criteria, constraints, architecture scope.
    Agents publish attempts to AgentHub (not GitHub PRs).
    Ship Room turns accepted attempts into promotion candidates and ShipRuns.
    Workspace lets you compose, preview, bless, and optionally promote candidate states
    without shipping to main.

Environment:
    TERARCHITECT_API_URL   Backend base URL (default: http://localhost:5010)
"""

import argparse

from cli._api import API, APIError
from cli._config import get_api_url
from cli._output import die
from cli.commands import attempt, context, graph, plan, project, ship, status, ticket, workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ta",
        description="Terarchitect CLI — manage intents, agents, Ship Room, and Workspace.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--api-url",
        metavar="URL",
        default=None,
        help="Backend base URL (overrides TERARCHITECT_API_URL, default: http://localhost:5010)",
    )
    parser.add_argument(
        "--output",
        choices=["human", "json"],
        default="human",
        help="Output format (default: human)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="_global_json",
        help="Alias for --output json",
    )

    subparsers = parser.add_subparsers(dest="group", metavar="COMMAND")
    subparsers.required = True

    project.register(subparsers)
    ticket.register(subparsers)
    attempt.register(subparsers)
    status.register(subparsers)
    status.register_alias(subparsers)
    context.register(subparsers)
    ship.register(subparsers)
    workspace.register(subparsers)
    graph.register(subparsers)
    plan.register(subparsers)
    return parser


def normalize_output_args(args: argparse.Namespace) -> None:
    if getattr(args, "_global_json", False) or getattr(args, "json", False):
        args.output = "json"


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    normalize_output_args(args)
    api = API(args.api_url or get_api_url())
    try:
        args.func(args, api)
    except APIError as exc:
        die(exc, output=args.output)


if __name__ == "__main__":
    main()
