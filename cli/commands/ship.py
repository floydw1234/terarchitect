"""ship subcommand: inspect waves, compose release branches, and ship to main."""

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "ship",
        help="Ship Room operations: waves, compose, feedback, merge-pr",
    )
    sub = p.add_subparsers(dest="ship_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # waves — list waves with ship run status
    wa = sub.add_parser("waves", help="List waves and ship run status for a project")
    wa.add_argument("project_id")

    # show — wave detail
    sh = sub.add_parser("show", help="Show full wave detail: tickets, attempts, ship run")
    sh.add_argument("project_id")
    sh.add_argument("wave_num", type=int)

    # compose — queue a ship run for a wave
    co = sub.add_parser("compose", help="Queue a release branch composition for a wave")
    co.add_argument("project_id")
    co.add_argument("wave_num", type=int)

    # feedback — post feedback to a wave's AgentHub channel
    fb = sub.add_parser("feedback", help="Post feedback to a wave's AgentHub channel")
    fb.add_argument("project_id")
    fb.add_argument("wave_num", type=int)
    fb.add_argument("message", help="Feedback message")
    fb.add_argument("--ticket", dest="target_ticket_id", default=None,
                    help="Target a specific ticket ID instead of the whole wave")

    # merge-pr — merge the release PR and ship to main
    mp = sub.add_parser("merge-pr", help="Merge the release PR and advance the shipped frontier")
    mp.add_argument("project_id")
    mp.add_argument("wave_num", type=int)
    mp.add_argument("--method", default="merge", choices=["merge", "squash", "rebase"],
                    help="Merge method (default: merge)")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.ship_cmd
    if cmd == "waves":
        _cmd_waves(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "compose":
        _cmd_compose(args, api)
    elif cmd == "feedback":
        _cmd_feedback(args, api)
    elif cmd == "merge-pr":
        _cmd_merge_pr(args, api)


def _cmd_waves(args, api: API) -> None:
    try:
        waves = api.get(f"/api/projects/{args.project_id}/ship/waves")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(waves)
        return
    if not waves:
        print("No waves found. Complete some tickets to see waves here.")
        return
    rows = []
    for w in waves:
        run = w.get("ship_run") or {}
        rows.append({
            "wave": str(w["wave_num"]),
            "tickets": f"{w.get('accepted_count', 0)}/{w.get('ticket_count', 0)} accepted",
            "status": run.get("status") or "—",
            "pr": (run.get("release_pr_url") or "")[:50],
            "shipped": (run.get("shipped_commit_hash") or "")[:10],
        })
    print_table(rows, [
        ("wave", "WAVE"),
        ("tickets", "ACCEPTED"),
        ("status", "STATUS"),
        ("pr", "RELEASE PR"),
        ("shipped", "SHIPPED"),
    ])


def _cmd_show(args, api: API) -> None:
    try:
        detail = api.get(f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(detail)
        return

    run = detail.get("ship_run") or {}
    print(f"\nWave {detail['wave_num']}")
    print(f"  All done:     {detail.get('all_done', False)}")
    print(f"  Can compose:  {detail.get('can_compose', False)}")
    print(f"  Stale:        {detail.get('stale_count', 0)} attempt(s)")
    print(f"  Frontier:     {(detail.get('shipped_frontier') or 'not set')[:16]}")
    if run:
        print(f"\n  Ship run: {short_id(run['id'])}  status={run['status']}")
        if run.get("release_pr_url"):
            print(f"  PR:       {run['release_pr_url']}")
        if run.get("test_status"):
            print(f"  Tests:    {run['test_status']}")
        if run.get("shipped_commit_hash"):
            print(f"  Shipped:  {run['shipped_commit_hash'][:12]}")
        if run.get("error"):
            print(f"  Error:    {run['error'][:120]}")
    print(f"\n  Tickets ({len(detail.get('tickets', []))}):")
    for t in detail.get("tickets", []):
        state = (t.get("display_state") or t.get("column_id") or "?")
        attempt = t.get("latest_attempt")
        hash_str = f"  {attempt['short_commit_hash']}" if attempt and attempt.get("short_commit_hash") else ""
        print(f"    {short_id(t['id'])}  {t['title'][:50]}  [{state}]{hash_str}")
    print(f"\n  Accepted attempts ({len(detail.get('accepted_attempts', []))}):")
    for a in detail.get("accepted_attempts", []):
        stale = " (stale)" if a.get("stale") else ""
        print(f"    {a.get('short_commit_hash') or '?':12}  wave={a['wave_num']}  "
              f"status={a['status']}{stale}")


def _cmd_compose(args, api: API) -> None:
    try:
        run = api.post(f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}/compose", {})
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(run)
        return
    print(f"Ship run queued: {run['id']}  wave={run['wave_num']}  status={run['status']}")
    print("Coordinator will pick it up and run the shipper. Use 'ta ship show' to check progress.")


def _cmd_feedback(args, api: API) -> None:
    body = {"message": args.message}
    if args.target_ticket_id:
        body["target_ticket_id"] = args.target_ticket_id
    try:
        api.post(
            f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}/feedback", body
        )
    except APIError as e:
        die(str(e))
    print("Feedback posted to AgentHub channel.")


def _cmd_merge_pr(args, api: API) -> None:
    try:
        run = api.post(
            f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}/ship",
            {"merge_method": args.method},
        )
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(run)
        return
    print(f"Shipped wave {args.wave_num}!")
    if run.get("shipped_commit_hash"):
        print(f"  New frontier: {run['shipped_commit_hash'][:12]}")
    if run.get("release_pr_url"):
        print(f"  PR:           {run['release_pr_url']}")
