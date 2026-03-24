"""review subcommand: list | show | comment | approve | merge"""

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser("review", help="PR review actions")
    sub = p.add_subparsers(dest="review_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list
    li = sub.add_parser("list", help="List tickets with open PRs")
    li.add_argument("project_id")

    # show
    sh = sub.add_parser("show", help="Show PR summary, commits, and comments")
    sh.add_argument("project_id")
    sh.add_argument("ticket_id")

    # comment
    co = sub.add_parser("comment", help="Post a comment on the PR")
    co.add_argument("project_id")
    co.add_argument("ticket_id")
    co.add_argument("--body", "-b", required=True, help="Comment body")

    # approve
    ap = sub.add_parser("approve", help="Approve the PR")
    ap.add_argument("project_id")
    ap.add_argument("ticket_id")
    ap.add_argument("--body", "-b", help="Optional approval message")

    # merge
    me = sub.add_parser("merge", help="Merge the PR")
    me.add_argument("project_id")
    me.add_argument("ticket_id")
    me.add_argument(
        "--method",
        choices=["merge", "squash", "rebase"],
        default="squash",
        help="Merge method (default: squash)",
    )

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.review_cmd
    if cmd == "list":
        _cmd_list(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "comment":
        _cmd_comment(args, api)
    elif cmd == "approve":
        _cmd_approve(args, api)
    elif cmd == "merge":
        _cmd_merge(args, api)


# ---------------------------------------------------------------------------

def _cmd_list(args, api: API) -> None:
    try:
        entries = api.get(f"/api/projects/{args.project_id}/review")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(entries)
        return
    rows = [
        {
            "id": short_id(e.get("id", "")),
            "title": e.get("title", ""),
            "pr": str(e.get("pr_number") or ""),
            "state": e.get("pr_state", ""),
            "merged": "yes" if e.get("merged") else "no",
        }
        for e in (entries or [])
    ]
    print_table(rows, [
        ("id", "TICKET ID"),
        ("title", "TITLE"),
        ("pr", "PR #"),
        ("state", "STATE"),
        ("merged", "MERGED"),
    ])


def _cmd_show(args, api: API) -> None:
    try:
        review = api.get(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/review"
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(review)
        return

    pr_url = review.get("pr_url", "")
    pr_num = review.get("pr_number", "")
    state = review.get("pr_state", "")
    merged = "yes" if review.get("merged") else "no"

    print(f"PR #{pr_num}: {pr_url}")
    print(f"State: {state}  |  Merged: {merged}")
    print()

    summary = (review.get("summary") or "").strip()
    if summary:
        print("Summary:")
        for line in summary.splitlines():
            print(f"  {line}")
        print()

    commits = review.get("commits") or []
    if commits:
        print(f"Commits ({len(commits)}):")
        for c in commits:
            sha = (c.get("sha") or "")[:8]
            msg = (c.get("message") or "").splitlines()[0][:72]
            print(f"  {sha}  {msg}")
        print()

    test_files = review.get("test_files") or []
    if test_files:
        desc = review.get("tests_description") or ""
        print(f"Test files ({len(test_files)})" + (f": {desc}" if desc else "") + ":")
        for tf in test_files:
            names = ", ".join(tf.get("test_names") or [])[:60]
            print(f"  {tf.get('path')}  [{names}]")
        print()

    comments = review.get("comments") or []
    if comments:
        print(f"Comments ({len(comments)}):")
        for c in comments:
            author = c.get("author", "?")
            ts = (c.get("created_at") or "")[:10]
            body = (c.get("body") or "").strip()[:200]
            print(f"  {author} [{ts}]: {body}")


def _cmd_comment(args, api: API) -> None:
    try:
        api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/review/comment",
            {"body": args.body},
        )
    except APIError as e:
        die(str(e))
    print("Comment posted.")


def _cmd_approve(args, api: API) -> None:
    payload = {}
    if getattr(args, "body", None):
        payload["body"] = args.body
    try:
        api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/review/approve",
            payload,
        )
    except APIError as e:
        die(str(e))
    print("PR approved.")


def _cmd_merge(args, api: API) -> None:
    try:
        api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/review/merge",
            {"merge_method": args.method},
        )
    except APIError as e:
        die(str(e))
    print(f"PR merged ({args.method}).")
