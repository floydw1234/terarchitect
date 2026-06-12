"""publish subcommand: explicit downstream publication of accepted AgentHub commits."""

from cli._api import API, APIError
from cli._output import die, print_json, print_receipt


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "publish",
        help="Publish an accepted/stable AgentHub commit to a downstream target",
    )
    p.add_argument("project_id", help="Project ID")
    p.add_argument("--target", default="github", help="Publish target (default: github)")
    p.add_argument("--attempt-id", dest="attempt_id", help="Explicit accepted attempt ID to publish")
    p.add_argument("--commit", dest="commit", help="Explicit accepted/stable AgentHub commit to publish")
    p.add_argument("--branch", help="Override target branch (default: project github_ref or main)")
    p.add_argument("--push", action="store_true", help="Actually push to the downstream target (default: dry-run)")
    p.add_argument("--force", action="store_true", help="Allow non-fast-forward replacement when supported")
    p.add_argument("--json", action="store_true", help="Print JSON for this command")
    p.set_defaults(func=_dispatch)


def _want_json(args) -> bool:
    return getattr(args, "json", False) or getattr(args, "output", "human") == "json"


def _dispatch(args, api: API) -> None:
    payload = {
        "target": args.target,
        "push": bool(args.push),
        "force": bool(args.force),
    }
    if args.attempt_id:
        payload["attempt_id"] = args.attempt_id
    if args.commit:
        payload["commit"] = args.commit
    if args.branch:
        payload["branch"] = args.branch

    try:
        result = api.post(f"/api/projects/{args.project_id}/publish", payload)
    except APIError as e:
        die(e, output=args.output)

    if _want_json(args):
        print_json(result)
        return

    mode = "push" if result.get("pushed") else "dry-run"
    print_receipt(
        f"Publish {mode}",
        fields=[
            ("Project", result.get("project_id")),
            ("Target", result.get("target")),
            ("Branch", result.get("branch")),
            ("Remote", result.get("remote")),
            ("Commit", (result.get("selected_commit") or "")[:12]),
            ("Attempt", result.get("selected_attempt_id")),
            ("Fast-forward", result.get("fast_forward")),
            ("Pushed", result.get("pushed")),
        ],
    )

