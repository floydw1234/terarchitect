"""merge subcommand: inspect wave status (legacy name kept for backward compat).
For Ship Room operations use: ta ship waves / compose / ship."""

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "merge",
        help="Inspect wave status (legacy — use 'ta ship' for composition and shipping)",
    )
    sub = p.add_subparsers(dest="merge_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # waves — show wave breakdown + ship status
    wa = sub.add_parser("waves", help="Show wave breakdown and ship run status")
    wa.add_argument("project_id")

    # runs — list ship run history
    ru = sub.add_parser("runs", help="List ship runs for a project")
    ru.add_argument("project_id")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.merge_cmd
    if cmd == "waves":
        _cmd_waves(args, api)
    elif cmd == "runs":
        _cmd_runs(args, api)


def _cmd_waves(args, api: API) -> None:
    try:
        waves = api.get(f"/api/projects/{args.project_id}/ship/waves")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(waves)
        return
    for wave in waves:
        w = wave["wave_num"]
        run = wave.get("ship_run")
        run_status = run["status"] if run else "—"
        accepted = wave.get("accepted_count", 0)
        total = wave.get("ticket_count", 0)
        all_done = wave.get("all_done", False)
        done_label = f"{accepted}/{total} accepted" + (" ✓" if all_done else "")
        pr = (run or {}).get("release_pr_url") or ""
        print(f"\nWave {w}  [{done_label}]  ship: {run_status}  {pr}")


def _cmd_runs(args, api: API) -> None:
    try:
        waves = api.get(f"/api/projects/{args.project_id}/ship/waves")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(waves)
        return
    rows = []
    for wave in (waves or []):
        run = wave.get("ship_run")
        if not run:
            continue
        rows.append({
            "id": short_id(run["id"]),
            "wave": str(run["wave_num"]),
            "status": run["status"],
            "pr": (run.get("release_pr_url") or "")[:60],
            "shipped": (run.get("shipped_commit_hash") or "")[:12],
        })
    if not rows:
        print("No ship runs found.")
        return
    print_table(rows, [
        ("id", "ID"), ("wave", "WAVE"), ("status", "STATUS"),
        ("pr", "RELEASE PR"), ("shipped", "SHIPPED"),
    ])
