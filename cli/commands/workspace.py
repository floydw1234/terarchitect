"""workspace subcommand: manage Composite Workspaces (Phase 9)."""

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "workspace",
        help="Composite Workspace: compose, preview, bless, and promote candidate codebase states",
    )
    sub = p.add_subparsers(dest="ws_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # leaves — list accepted attempts available for selection
    le = sub.add_parser("leaves", help="List accepted attempts available for workspace selection")
    le.add_argument("project_id")

    # list — list workspaces for a project
    li = sub.add_parser("list", help="List Composite Workspaces for a project")
    li.add_argument("project_id")

    # create — create and compose a new workspace
    cr = sub.add_parser("create", help="Create a Composite Workspace from selected attempts")
    cr.add_argument("project_id")
    cr.add_argument("--attempt", "-a", dest="attempt_ids", action="append", required=True,
                    metavar="ATTEMPT_ID",
                    help="Accepted attempt ID to include (repeat for multiple)")
    cr.add_argument("--no-compose", action="store_true",
                    help="Create as draft without triggering composition")

    # show — workspace detail
    sh = sub.add_parser("show", help="Show workspace detail")
    sh.add_argument("project_id")
    sh.add_argument("workspace_id")

    # compose — trigger composition for an existing workspace
    co = sub.add_parser("compose", help="Trigger async composition for a workspace")
    co.add_argument("project_id")
    co.add_argument("workspace_id")

    # analyze — check compatibility before composing
    an = sub.add_parser("analyze", help="Analyze compatibility of selected attempts")
    an.add_argument("project_id")
    an.add_argument("--attempt", "-a", dest="attempt_ids", action="append", required=True,
                    metavar="ATTEMPT_ID",
                    help="Accepted attempt ID to analyze (repeat for multiple)")

    # bless — bless as preferred candidate
    bl = sub.add_parser("bless",
                        help="Bless this workspace as the preferred candidate (does not ship to main)")
    bl.add_argument("project_id")
    bl.add_argument("workspace_id")

    # promote — promote to ShipRun (compatibility export)
    pr = sub.add_parser("promote",
                        help="Promote this workspace to a ShipRun for compatibility export")
    pr.add_argument("project_id")
    pr.add_argument("workspace_id")

    # discard
    di = sub.add_parser("discard", help="Discard a workspace")
    di.add_argument("project_id")
    di.add_argument("workspace_id")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.ws_cmd
    if cmd == "leaves":
        _cmd_leaves(args, api)
    elif cmd == "list":
        _cmd_list(args, api)
    elif cmd == "create":
        _cmd_create(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "compose":
        _cmd_compose(args, api)
    elif cmd == "analyze":
        _cmd_analyze(args, api)
    elif cmd == "bless":
        _cmd_bless(args, api)
    elif cmd == "promote":
        _cmd_promote(args, api)
    elif cmd == "discard":
        _cmd_discard(args, api)


def _cmd_leaves(args, api: API) -> None:
    """List accepted attempts across all tickets — the selectable leaves."""
    try:
        tickets = api.get(f"/api/projects/{args.project_id}/tickets")
    except APIError as e:
        die(str(e))
    rows = []
    for t in (tickets or []):
        # Use accepted_attempt (not latest_attempt) — the ticket may have retried and failed
        # while still having a valid earlier accepted attempt selectable for the workspace.
        aa = t.get("accepted_attempt")
        if not aa:
            continue
        rows.append({
            "attempt_id": aa.get("id", "")[:18],
            "commit": aa.get("short_commit_hash") or "?",
            "wave": str(aa.get("wave_num", "?")),
            "status": aa.get("status", ""),
            "stale": "yes" if aa.get("stale") else "",
            "ticket": t.get("title", "")[:40],
        })
    if args.output == "json":
        print_json(rows)
        return
    if not rows:
        print("No accepted attempts found. Complete tickets with agents first.")
        return
    print_table(rows, [
        ("attempt_id", "ATTEMPT ID"),
        ("commit", "COMMIT"),
        ("wave", "WAVE"),
        ("status", "STATUS"),
        ("stale", "STALE"),
        ("ticket", "TICKET"),
    ])


def _cmd_list(args, api: API) -> None:
    try:
        wss = api.get(f"/api/projects/{args.project_id}/workspaces")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(wss)
        return
    if not wss:
        print("No workspaces. Use 'ta workspace create' to compose a candidate state.")
        return
    rows = [
        {
            "id": short_id(w["id"]),
            "status": w["status"],
            "hash": w.get("short_composed_hash") or "—",
            "files": str(len(w.get("changed_files") or [])),
            "tests": w.get("test_status") or "—",
        }
        for w in wss
    ]
    print_table(rows, [
        ("id", "ID"), ("status", "STATUS"),
        ("hash", "COMPOSED"), ("files", "FILES"), ("tests", "TESTS"),
    ])


def _cmd_create(args, api: API) -> None:
    attempt_ids = args.attempt_ids or []
    try:
        ws = api.post(
            f"/api/projects/{args.project_id}/workspaces",
            {"attempt_ids": attempt_ids},
        )
    except APIError as e:
        die(str(e))
    if args.no_compose:
        print(f"Workspace created (draft): {ws['id']}")
        return
    # Trigger composition immediately
    try:
        ws = api.post(f"/api/projects/{args.project_id}/workspaces/{ws['id']}/compose", {})
    except APIError as e:
        die(f"Workspace created but compose failed: {e}")
    if args.output == "json":
        print_json(ws)
        return
    print(f"Workspace {ws['id']} composing — use 'ta workspace show' to check progress.")


def _cmd_show(args, api: API) -> None:
    try:
        ws = api.get(
            f"/api/projects/{args.project_id}/workspaces/{args.workspace_id}?include_test_output=true"
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(ws)
        return
    print(f"\nWorkspace {short_id(ws['id'])}")
    print(f"  Status:   {ws['status']}")
    print(f"  Composed: {ws.get('short_composed_hash') or '—'}")
    print(f"  Tests:    {ws.get('test_status') or '—'}")
    print(f"  Files:    {len(ws.get('changed_files') or [])}")
    if ws.get("conflict_summary"):
        print(f"  Conflict: {ws['conflict_summary'][:120]}")
    if ws.get("changed_files"):
        print(f"\n  Changed files:")
        for f in (ws["changed_files"] or [])[:20]:
            print(f"    {f}")
        if len(ws.get("changed_files") or []) > 20:
            print(f"    ... and {len(ws['changed_files']) - 20} more")


def _cmd_compose(args, api: API) -> None:
    try:
        ws = api.post(
            f"/api/projects/{args.project_id}/workspaces/{args.workspace_id}/compose", {}
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(ws)
        return
    print(f"Workspace {args.workspace_id} queued for composition.")
    print("Coordinator will pick it up. Use 'ta workspace show' to check progress.")


def _cmd_analyze(args, api: API) -> None:
    attempt_ids = args.attempt_ids or []
    try:
        report = api.post(
            f"/api/projects/{args.project_id}/workspaces/analyze",
            {"attempt_ids": attempt_ids},
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(report)
        return
    ok = report.get("ok", False)
    issues = report.get("issues") or []
    print(f"\nCompatibility: {'OK' if ok else 'Issues found'}")
    if issues:
        for issue in issues:
            marker = "ERROR  " if issue["level"] == "error" else "WARNING"
            print(f"  [{marker}] {issue['message']}")
    else:
        print("  No issues found — safe to compose.")
    print(f"\nDependency order: {len(report.get('dep_order', []))} attempt(s)")


def _cmd_bless(args, api: API) -> None:
    try:
        ws = api.post(
            f"/api/projects/{args.project_id}/workspaces/{args.workspace_id}/bless", {}
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(ws)
        return
    print(f"Workspace {args.workspace_id} is now the Blessed Candidate.")
    print("Future agents building new tickets will start from this composite's commit hash.")
    print("Note: blessing does not imply production or deployment.")


def _cmd_promote(args, api: API) -> None:
    try:
        result = api.post(
            f"/api/projects/{args.project_id}/workspaces/{args.workspace_id}/promote", {}
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(result)
        return
    run = result.get("ship_run") or {}
    print(f"Workspace promoted for export.")
    print(f"  ShipRun: {run.get('id', '?')}  wave={run.get('wave_num')}  status={run.get('status')}")
    print("Coordinator will compose a release branch and open a PR.")
    print("Use 'ta ship waves' to track progress.")


def _cmd_discard(args, api: API) -> None:
    try:
        api.post(
            f"/api/projects/{args.project_id}/workspaces/{args.workspace_id}/discard", {}
        )
    except APIError as e:
        die(str(e))
    print(f"Workspace {args.workspace_id} discarded.")
