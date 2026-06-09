"""attempt subcommand: inspect project attempts and their artifacts."""

import urllib.parse

from cli._api import API, APIError
from cli._output import die, print_json, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser("attempt", help="Inspect attempts across a project")
    sub = p.add_subparsers(dest="attempt_cmd", metavar="SUBCOMMAND")
    sub.required = True

    li = sub.add_parser("list", help="List attempts for a project")
    li.add_argument("project_id")
    li.add_argument("--ticket", dest="ticket_id", help="Filter by ticket ID")
    li.add_argument("--status", help="Filter by attempt status")
    li.add_argument("--json", action="store_true", help="Print JSON output")

    sh = sub.add_parser("show", help="Show one attempt")
    sh.add_argument("project_id")
    sh.add_argument("attempt_id")
    sh.add_argument("--json", action="store_true", help="Print JSON output")

    fi = sub.add_parser("files", help="List files changed in an attempt")
    fi.add_argument("project_id")
    fi.add_argument("attempt_id")
    fi.add_argument("--json", action="store_true", help="Print JSON output")

    df = sub.add_parser("diff", help="Show an attempt diff")
    df.add_argument("project_id")
    df.add_argument("attempt_id")
    df.add_argument("--file", dest="file_path", help="Limit diff to one file path")
    df.add_argument("--max-bytes", type=int, default=None, help="Limit diff payload size")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    _apply_json_flag(args)
    cmd = args.attempt_cmd
    if cmd == "list":
        _cmd_list(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "files":
        _cmd_files(args, api)
    elif cmd == "diff":
        _cmd_diff(args, api)


def _apply_json_flag(args) -> None:
    if getattr(args, "json", False):
        args.output = "json"


def _project_attempts_endpoint(project_id: str, attempt_id: str = "") -> str:
    base = f"/api/projects/{project_id}/attempts"
    return f"{base}/{attempt_id}" if attempt_id else base


def _query_string(params: dict[str, object]) -> str:
    encoded = urllib.parse.urlencode(
        [(key, value) for key, value in params.items() if value is not None]
    )
    return f"?{encoded}" if encoded else ""


def _attempt_endpoint_hint(project_id: str) -> str:
    return (
        "If the backend for this branch does not expose project-scoped attempt endpoints yet, "
        f"use `ta ticket attempts {project_id} <ticket_id>` or update the backend."
    )


def _artifact_endpoint_hint(project_id: str, attempt_id: str) -> str:
    return (
        f"Attempt artifact endpoint unavailable for {short_id(attempt_id)}. "
        f"Inspect the attempt with `ta attempt show {project_id} {attempt_id}` or update the backend."
    )


def _render_attempt_row(attempt: dict) -> dict[str, str]:
    files = attempt.get("changed_files") or attempt.get("files") or []
    if isinstance(files, list):
        changed = ", ".join(files[:3])
        if len(files) > 3:
            changed += f" +{len(files) - 3}"
    else:
        changed = ""
    return {
        "id": short_id(attempt.get("id", "")),
        "ticket": short_id(attempt.get("ticket_id", "")),
        "status": attempt.get("status", ""),
        "commit": attempt.get("short_commit_hash") or short_id(attempt.get("agenthub_commit_hash", ""), 12),
        "wave": str(attempt.get("wave_num", "")),
        "files": changed,
    }


def _print_attempt_summary(attempt: dict, project_id: str) -> None:
    print(f"Attempt {short_id(attempt.get('id', ''))}")
    print(f"  Ticket:      {attempt.get('ticket_id') or 'unknown'}")
    print(f"  Status:      {attempt.get('status') or 'unknown'}")
    print(f"  Commit:      {attempt.get('short_commit_hash') or attempt.get('agenthub_commit_hash') or 'unavailable'}")
    print(f"  Wave:        {attempt.get('wave_num') if attempt.get('wave_num') is not None else 'unavailable'}")
    if attempt.get("attempt_num") is not None:
        print(f"  Attempt #:   {attempt.get('attempt_num')}")
    if attempt.get("test_status"):
        print(f"  Tests:       {attempt.get('test_status')}")
    if attempt.get("stale") is not None:
        print(f"  Stale:       {attempt.get('stale')}")
    if attempt.get("summary"):
        print(f"  Summary:     {attempt.get('summary')}")
    files = attempt.get("changed_files") or attempt.get("files") or []
    if isinstance(files, list) and files:
        print(f"  Files:       {', '.join(files[:5])}")
    elif "changed_files" in attempt or "files" in attempt:
        print("  Files:       unavailable")
    if attempt.get("validation_error"):
        print(f"  Validation:  {attempt.get('validation_error')}")
    print("")
    print("Next:")
    print(f"  ta attempt files {project_id} {attempt.get('id')}")
    print(f"  ta attempt diff {project_id} {attempt.get('id')}")
    ticket_id = attempt.get("ticket_id")
    if ticket_id:
        print(f"  ta ticket attempts {project_id} {ticket_id}")
        if attempt.get("status") not in {"accepted", "composed", "release_pr_open", "shipped"}:
            print(f"  ta ticket accept-attempt {project_id} {ticket_id} {attempt.get('id')}")
        if attempt.get("status") not in {"rejected", "shipped", "superseded", "failed"}:
            print(f"  ta ticket reject-attempt {project_id} {ticket_id} {attempt.get('id')} --reason \"needs revision\"")


def _cmd_list(args, api: API) -> None:
    path = _project_attempts_endpoint(args.project_id) + _query_string(
        {"ticket_id": args.ticket_id, "status": args.status}
    )
    try:
        attempts = api.get(path)
    except APIError as e:
        if e.status in (404, 501):
            die(f"{e}. {_attempt_endpoint_hint(args.project_id)}")
        die(str(e))
    if args.output == "json":
        print_json(attempts)
        return
    if not attempts:
        print("No attempts found for that filter.")
        if args.ticket_id:
            print(f"Try `ta ticket attempts {args.project_id} {args.ticket_id}` to inspect that ticket directly.")
        return
    rows = [_render_attempt_row(attempt) for attempt in attempts]
    print_table(rows, [
        ("id", "ID"),
        ("ticket", "TICKET"),
        ("status", "STATUS"),
        ("commit", "COMMIT"),
        ("wave", "WAVE"),
        ("files", "FILES"),
    ])
    print("")
    print("Next:")
    first = attempts[0]
    print(f"  ta attempt show {args.project_id} {first.get('id')}")
    if first.get("ticket_id"):
        print(f"  ta ticket attempts {args.project_id} {first.get('ticket_id')}")


def _cmd_show(args, api: API) -> None:
    try:
        attempt = api.get(_project_attempts_endpoint(args.project_id, args.attempt_id))
    except APIError as e:
        if e.status in (404, 501):
            die(f"{e}. {_attempt_endpoint_hint(args.project_id)}")
        die(str(e))
    if args.output == "json":
        print_json(attempt)
        return
    _print_attempt_summary(attempt, args.project_id)


def _cmd_files(args, api: API) -> None:
    try:
        data = api.get(_project_attempts_endpoint(args.project_id, args.attempt_id) + "/files")
    except APIError as e:
        if e.status in (404, 501):
            die(f"{e}. {_artifact_endpoint_hint(args.project_id, args.attempt_id)}")
        die(str(e))
    if args.output == "json":
        print_json(data)
        return
    files = data.get("files") if isinstance(data, dict) else data
    if not files:
        print("No changed files reported for this attempt.")
        return
    rows = []
    for item in files:
        if isinstance(item, dict):
            rows.append({
                "path": item.get("path") or item.get("file") or "",
                "status": item.get("status") or "",
                "changes": str(item.get("changes") or item.get("lines") or ""),
            })
        else:
            rows.append({"path": str(item), "status": "", "changes": ""})
    print_table(rows, [
        ("path", "PATH"),
        ("status", "STATUS"),
        ("changes", "CHANGES"),
    ])
    print("")
    print(f"Next: ta attempt diff {args.project_id} {args.attempt_id}")


def _cmd_diff(args, api: API) -> None:
    path = _project_attempts_endpoint(args.project_id, args.attempt_id) + "/diff"
    path += _query_string({"file": args.file_path, "max_bytes": args.max_bytes})
    try:
        diff_text = api.get_text(path, accept="text/plain, application/json")
    except APIError as e:
        if e.status in (404, 501):
            die(f"{e}. {_artifact_endpoint_hint(args.project_id, args.attempt_id)}")
        die(str(e))
    if not diff_text:
        print("No diff returned for this attempt.")
        return
    print(diff_text.rstrip())
