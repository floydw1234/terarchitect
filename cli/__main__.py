"""
Terarchitect CLI — entry point.

Usage:
    python -m cli [--api-url URL] [--output json] COMMAND SUBCOMMAND [args]

Commands:
    project   list | create | show | update | delete
    ticket    list | create | show | update | run | cancel | logs
    review    list | show | comment | approve | merge
    graph     get | set
    plan      <project-id>  — generate tickets from graph + notes via LLM

Environment:
    TERARCHITECT_API_URL   Backend base URL (default: http://localhost:5010)
"""

import argparse
import sys

from cli._api import API
from cli._config import get_api_url
from cli.commands import graph, merge, plan, project, review, ticket


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ta",
        description="Terarchitect CLI — manage projects, tickets, and reviews from the terminal.",
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
    review.register(subparsers)
    graph.register(subparsers)
    plan.register(subparsers)
    merge.register(subparsers)

    args = parser.parse_args()
    api = API(args.api_url or get_api_url())
    args.func(args, api)


if __name__ == "__main__":
    main()
