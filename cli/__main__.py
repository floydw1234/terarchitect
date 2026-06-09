"""
Terarchitect CLI — entry point.

Usage:
    python -m cli [--api-url URL] [--output json] COMMAND SUBCOMMAND [args]

Commands:
    project    list | create | show | update | delete
    ticket     list | create | show | update | run | cancel | logs
               | attempts | accept-attempt | reject-attempt
               (Intent fields: --rationale, --acceptance-criteria, --constraints, --intent-status)
    attempt    list | show | files | diff
    ship       waves | show | compose | feedback | merge-pr
    workspace  leaves | list | create | show | compose | analyze | bless | promote | discard
    graph      get | set
    plan       <project-id>  — generate tickets from graph + notes via LLM

Product model:
    Tickets are intents: goal, rationale, acceptance criteria, constraints, architecture scope.
    Agents publish attempts to AgentHub (not GitHub PRs).
    Ship Room composes accepted attempts into one release PR per wave.
    Workspace lets you compose, preview, bless, and optionally promote candidate states
    without shipping to main.

Environment:
    TERARCHITECT_API_URL   Backend base URL (default: http://localhost:5010)
"""

import argparse
import sys

from cli._api import API
from cli._config import get_api_url
from cli.commands import attempt, graph, plan, project, ship, ticket, workspace


def main() -> None:
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

    subparsers = parser.add_subparsers(dest="group", metavar="COMMAND")
    subparsers.required = True

    project.register(subparsers)
    ticket.register(subparsers)
    attempt.register(subparsers)
    ship.register(subparsers)
    workspace.register(subparsers)
    graph.register(subparsers)
    plan.register(subparsers)

    args = parser.parse_args()
    api = API(args.api_url or get_api_url())
    args.func(args, api)


if __name__ == "__main__":
    main()
