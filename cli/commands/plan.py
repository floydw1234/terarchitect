"""plan subcommand: generate tickets from a project's graph + notes using an LLM."""

import os
import subprocess
import sys

from cli._api import API, APIError
from cli._output import die


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "plan",
        help="Generate tickets from a project's graph + notes using an LLM",
    )
    p.add_argument("project_id", help="Project UUID")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated plan without posting tickets to the backend",
    )
    p.add_argument(
        "--max-tickets",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of tickets to generate (default: 20)",
    )
    p.add_argument(
        "--llm-url",
        metavar="URL",
        help="LLM completions endpoint (overrides DIRECTOR_LLM_URL)",
    )
    p.add_argument(
        "--model",
        metavar="NAME",
        help="LLM model name (overrides DIRECTOR_MODEL)",
    )
    p.add_argument(
        "--api-key",
        metavar="KEY",
        help="LLM API key (overrides DIRECTOR_API_KEY)",
    )
    p.add_argument(
        "--provider",
        choices=["openai", "anthropic"],
        help="LLM provider (overrides DIRECTOR_PROVIDER; default: openai)",
    )
    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    # Verify the project exists before launching the planner
    try:
        project = api.get(f"/api/projects/{args.project_id}")
    except APIError as e:
        die(str(e))

    print(f"Planning tickets for project: {project.get('name')} ({args.project_id})")

    # Build env for the planner subprocess
    env = os.environ.copy()
    env["PROJECT_ID"] = args.project_id
    env["TERARCHITECT_API_URL"] = api.base_url
    env["PLANNER_MAX_TICKETS"] = str(args.max_tickets)
    env["PLANNER_DRY_RUN"] = "1" if args.dry_run else "0"

    if args.llm_url:
        env["DIRECTOR_LLM_URL"] = args.llm_url
    if args.model:
        env["DIRECTOR_MODEL"] = args.model
    if args.api_key:
        env["DIRECTOR_API_KEY"] = args.api_key
    if args.provider:
        env["DIRECTOR_PROVIDER"] = args.provider

    # Check LLM config is present
    if not env.get("DIRECTOR_LLM_URL"):
        die(
            "DIRECTOR_LLM_URL is not set.\n"
            "Set it via env or pass --llm-url.\n"
            "Example: export DIRECTOR_LLM_URL=https://api.openai.com/v1/chat/completions"
        )

    result = subprocess.run(
        [sys.executable, "-m", "agent.planner"],
        env=env,
        text=True,
    )
    sys.exit(result.returncode)
