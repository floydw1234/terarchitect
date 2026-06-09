"""ship subcommand: inspect waves, compose ShipRuns, and ship at the wave boundary."""

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "ship",
        help="Ship Room wave flow: inspect, compose, inspect ShipRun, then ship/merge",
    )
    sub = p.add_subparsers(dest="ship_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # waves — list waves with ship run status
    wa = sub.add_parser("waves", help="List project waves and their ShipRun status")
    wa.add_argument("project_id")
    wa.add_argument("--explain", action="store_true", help="Include blockers, next actions, and dependency issues")
    wa.add_argument("--json", action="store_true", help="Print JSON for this command")

    # show — wave detail
    sh = sub.add_parser("show", help="Show wave detail: accepted attempts and ShipRun state")
    sh.add_argument("project_id")
    sh.add_argument("wave_num", type=int)
    sh.add_argument("--json", action="store_true", help="Print JSON for this command")

    rv = sub.add_parser("review", help="Safe pre-ship checklist for a wave")
    rv.add_argument("project_id")
    rv.add_argument("wave_num", type=int)
    rv.add_argument("--json", action="store_true", help="Print JSON for this command")

    df = sub.add_parser("diff", help="Show the composed diff preview for a wave")
    df.add_argument("project_id")
    df.add_argument("wave_num", type=int)
    df.add_argument("--max-bytes", type=int, default=None, help="Cap returned diff text bytes")
    df.add_argument("--json", action="store_true", help="Print JSON for this command")

    dc = sub.add_parser("dry-compose", help="Preview compose safety without creating a ship run")
    dc.add_argument("project_id")
    dc.add_argument("wave_num", type=int)
    dc.add_argument("--json", action="store_true", help="Print JSON for this command")

    # compose — queue a ship run for a wave
    co = sub.add_parser("compose", help="Compose the accepted attempts for a wave into a ShipRun")
    co.add_argument("project_id")
    co.add_argument("wave_num", type=int)

    # feedback — post feedback to a wave's AgentHub channel
    fb = sub.add_parser("feedback", help="Send operator feedback on a wave before final ship")
    fb.add_argument("project_id")
    fb.add_argument("wave_num", type=int)
    fb.add_argument("message", help="Feedback message")
    fb.add_argument("--ticket", dest="target_ticket_id", default=None,
                    help="Target a specific ticket ID instead of the whole wave")

    # merge-pr — perform the final wave-boundary ship/merge
    mp = sub.add_parser("merge-pr", help="Ship a ready wave and advance the shipped frontier")
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
    elif cmd == "review":
        _cmd_review(args, api)
    elif cmd == "diff":
        _cmd_diff(args, api)
    elif cmd == "dry-compose":
        _cmd_dry_compose(args, api)
    elif cmd == "compose":
        _cmd_compose(args, api)
    elif cmd == "feedback":
        _cmd_feedback(args, api)
    elif cmd == "merge-pr":
        _cmd_merge_pr(args, api)


def _cmd_waves(args, api: API) -> None:
    try:
        suffix = "?explain=1" if args.explain else ""
        waves = api.get(f"/api/projects/{args.project_id}/ship/waves{suffix}")
    except APIError as e:
        die(str(e))
    if _want_json(args):
        print_json(waves)
        return
    if not waves:
        print("No waves found. Wait for agent attempts to be accepted before composing a wave.")
        return
    rows = []
    for w in waves:
        run = w.get("ship_run") or {}
        rows.append({
            "wave": str(w["wave_num"]),
            "tickets": f"{w.get('accepted_count', 0)}/{w.get('ticket_count', 0)} accepted",
            "status": run.get("status") or "—",
            "compose": "yes" if w.get("can_compose") else "no" if args.explain else "—",
            "ship": "yes" if w.get("can_ship") else "no" if args.explain else "—",
            "pr": (run.get("release_pr_url") or "")[:50],
            "shipped": (run.get("shipped_commit_hash") or "")[:10],
        })
    print_table(rows, [
        ("wave", "WAVE"),
        ("tickets", "ACCEPTED"),
        ("status", "STATUS"),
        ("compose", "COMPOSE"),
        ("ship", "SHIP"),
        ("pr", "RELEASE PR"),
        ("shipped", "SHIPPED"),
    ])
    if args.explain:
        for w in waves:
            print(f"\nWave {w['wave_num']}")
            blockers = w.get("blockers") or []
            next_actions = w.get("next_actions") or []
            if blockers:
                print("  Blockers:")
                for blocker in blockers:
                    print(f"    - {blocker}")
            else:
                print("  Blockers: none")
            if next_actions:
                print("  Next actions:")
                for action in next_actions:
                    print(f"    - {action}")


def _cmd_show(args, api: API) -> None:
    try:
        detail = api.get(f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}")
    except APIError as e:
        die(str(e))
    if _want_json(args):
        print_json(detail)
        return

    _print_wave_detail(detail, title=f"Wave {detail['wave_num']}")


def _cmd_review(args, api: API) -> None:
    try:
        detail = api.get(f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}")
    except APIError as e:
        die(str(e))
    review = {
        "wave_num": detail["wave_num"],
        "all_done": detail.get("all_done", False),
        "can_compose": detail.get("can_compose", False),
        "can_ship": detail.get("can_ship", False),
        "blockers": detail.get("blockers", []),
        "next_actions": detail.get("next_actions", []),
        "stale_details": detail.get("stale_details", []),
        "validation": detail.get("validation", {}),
        "ship_run": detail.get("ship_run"),
        "tickets": detail.get("tickets", []),
    }
    if _want_json(args):
        print_json(review)
        return

    print(f"\nWave {detail['wave_num']} review")
    print(f"  All done:    {detail.get('all_done', False)}")
    print(f"  Can compose: {detail.get('can_compose', False)}")
    print(f"  Can ship:    {detail.get('can_ship', False)}")
    blockers = detail.get("blockers") or []
    print(f"  Blockers:    {len(blockers)}")
    for blocker in blockers:
        print(f"    - {blocker}")
    next_actions = detail.get("next_actions") or []
    if next_actions:
        print("\n  Next actions:")
        for action in next_actions:
            print(f"    - {action}")


def _cmd_diff(args, api: API) -> None:
    path = f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}/diff"
    if args.max_bytes:
        path += f"?max_bytes={args.max_bytes}"
    try:
        payload = api.get(path)
    except APIError as e:
        die(str(e))
    if _want_json(args):
        print_json(payload)
        return

    print(f"\nWave {payload['wave_num']} diff")
    print(f"  Base:     {(payload.get('base_hash') or 'unknown')[:12]}")
    print(f"  Composed: {(payload.get('composed_commit_hash') or 'unknown')[:12]}")
    files = payload.get("changed_files") or []
    print(f"  Files:    {len(files)}")
    for path in files[:20]:
        print(f"    - {path}")
    if payload.get("note"):
        print(f"\n  Note: {payload['note']}")
    diff_text = payload.get("diff")
    if diff_text:
        print("\n  Diff preview:\n")
        print(diff_text.rstrip())
        if payload.get("truncated"):
            print("\n  [truncated]")


def _cmd_dry_compose(args, api: API) -> None:
    try:
        payload = api.get(f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}/dry-compose")
    except APIError as e:
        die(str(e))
    if _want_json(args):
        print_json(payload)
        return

    print(f"\nWave {payload['wave_num']} dry compose")
    print(f"  Safe to compose: {payload.get('safe_to_compose', False)}")
    print(f"  All done:        {payload.get('all_done', False)}")
    blockers = payload.get("blockers") or []
    if blockers:
        print("  Blockers:")
        for blocker in blockers:
            print(f"    - {blocker}")
    else:
        print("  Blockers: none")
    commit_hashes = payload.get("commit_hashes") or []
    if commit_hashes:
        print("  Commit hashes:")
        for commit_hash in commit_hashes:
            print(f"    - {commit_hash[:12]}")
    next_actions = payload.get("next_actions") or []
    if next_actions:
        print("  Next actions:")
        for action in next_actions:
            print(f"    - {action}")


def _print_wave_detail(detail: dict, *, title: str) -> None:
    run = detail.get("ship_run") or {}
    print(f"\n{title}")
    print(f"  All done:     {detail.get('all_done', False)}")
    print(f"  Can compose:  {detail.get('can_compose', False)}")
    print(f"  Can ship:     {detail.get('can_ship', False)}")
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
    blockers = detail.get("blockers") or []
    print(f"\n  Blockers ({len(blockers)}):")
    for blocker in blockers:
        print(f"    - {blocker}")
    next_actions = detail.get("next_actions") or []
    print(f"\n  Next actions ({len(next_actions)}):")
    for action in next_actions:
        print(f"    - {action}")
    print(f"\n  Tickets ({len(detail.get('tickets', []))}):")
    for t in detail.get("tickets", []):
        state = (t.get("display_state") or t.get("column_id") or "?")
        attempt = t.get("latest_attempt")
        hash_str = f"  {attempt['short_commit_hash']}" if attempt and attempt.get("short_commit_hash") else ""
        print(f"    {short_id(t['id'])}  wave={t.get('wave_num', '?')}  {t['title'][:44]}  [{state}]{hash_str}")
        if t.get("dependency_reason"):
            print(f"      why: {t['dependency_reason']}")
        for blocker in t.get("blockers") or []:
            print(f"      blocker: {blocker}")
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
    if _want_json(args):
        print_json(run)
        return
    print(f"Ship run queued: {run['id']}  wave={run['wave_num']}  status={run['status']}")
    print("Coordinator will compose the wave. Inspect the ShipRun with 'ta ship show' before the final ship/merge step.")


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
    print("Feedback posted to the wave.")


def _cmd_merge_pr(args, api: API) -> None:
    try:
        run = api.post(
            f"/api/projects/{args.project_id}/ship/waves/{args.wave_num}/ship",
            {"merge_method": args.method},
        )
    except APIError as e:
        die(str(e))
    if _want_json(args):
        print_json(run)
        return
    print(f"Shipped wave {args.wave_num} at the final boundary.")
    if run.get("shipped_commit_hash"):
        print(f"  New frontier: {run['shipped_commit_hash'][:12]}")
    if run.get("release_pr_url"):
        print(f"  PR:           {run['release_pr_url']}")


def _want_json(args) -> bool:
    return getattr(args, "json", False) or getattr(args, "output", "human") == "json"
