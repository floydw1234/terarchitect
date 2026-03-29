"""merge subcommand: inspect wave status and trigger merge runs."""

import os
import subprocess
import sys

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "merge",
        help="Manage swarm-mode wave merges",
    )
    sub = p.add_subparsers(dest="merge_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # waves — show wave breakdown + merge status
    wa = sub.add_parser("waves", help="Show wave breakdown and merge status")
    wa.add_argument("project_id")

    # runs — list merge run history
    ru = sub.add_parser("runs", help="List merge runs for a project")
    ru.add_argument("project_id")

    # trigger — manually queue a merge run
    tr = sub.add_parser("trigger", help="Manually queue a merge run for the current wave")
    tr.add_argument("project_id")
    tr.add_argument("--wave", type=int, default=None, metavar="N",
                    help="Specific wave number to merge (default: auto-detect)")

    # run-local — claim and execute a merge run on this host
    rl = sub.add_parser("run-local", help="Claim and execute the next queued merge run on this host")
    rl.add_argument("project_id", nargs="?", help="(unused — for display only)")
    rl.add_argument("--test-command", metavar="CMD",
                    help="Shell command to run tests (overrides MERGE_TEST_COMMAND)")
    rl.add_argument("--branch-prefix", metavar="PREFIX", default=None,
                    help="Branch name prefix (default: 'wave')")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.merge_cmd
    if cmd == "waves":
        _cmd_waves(args, api)
    elif cmd == "runs":
        _cmd_runs(args, api)
    elif cmd == "trigger":
        _cmd_trigger(args, api)
    elif cmd == "run-local":
        _cmd_run_local(args, api)


def _cmd_waves(args, api: API) -> None:
    try:
        waves = api.get(f"/api/projects/{args.project_id}/merge/waves")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(waves)
        return
    for wave in waves:
        w = wave["wave_num"]
        run = wave.get("merge_run")
        run_status = run["status"] if run else "—"
        pr = (run or {}).get("pr_url") or ""
        tickets = wave.get("tickets", [])
        done = sum(1 for t in tickets if t.get("column_id") == "done")
        print(f"\nWave {w}  [{done}/{len(tickets)} done]  merge: {run_status}  {pr}")
        for t in tickets:
            col = t.get("column_id", "?")
            mark = "✓" if col == "done" else "·"
            print(f"  {mark} {short_id(t['id'])}  {t.get('title', '')[:60]}  [{col}]")


def _cmd_runs(args, api: API) -> None:
    try:
        runs = api.get(f"/api/projects/{args.project_id}/merge/runs")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(runs)
        return
    rows = [
        {
            "id": short_id(r["id"]),
            "wave": str(r["wave_num"]),
            "status": r["status"],
            "hash": (r.get("commit_hash") or "")[:12],
            "pr": (r.get("pr_url") or "")[:60],
        }
        for r in (runs or [])
    ]
    print_table(rows, [
        ("id", "ID"), ("wave", "WAVE"), ("status", "STATUS"),
        ("hash", "COMMIT"), ("pr", "PR URL"),
    ])


def _cmd_trigger(args, api: API) -> None:
    body = {}
    if args.wave is not None:
        body["wave_num"] = args.wave
    try:
        run = api.post(f"/api/projects/{args.project_id}/merge/trigger", body)
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(run)
        return
    print(f"Merge run queued: {run['id']} (wave {run['wave_num']})")
    print("Run 'ta merge run-local' or wait for the coordinator to pick it up.")


def _cmd_run_local(args, api: API) -> None:
    env = os.environ.copy()
    env["TERARCHITECT_API_URL"] = api.base_url
    if args.test_command:
        env["MERGE_TEST_COMMAND"] = args.test_command
    if args.branch_prefix:
        env["MERGE_BRANCH_PREFIX"] = args.branch_prefix

    result = subprocess.run(
        [sys.executable, "-m", "agent.merger"],
        env=env,
        text=True,
    )
    sys.exit(result.returncode)
