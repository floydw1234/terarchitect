"""ticket subcommand: list | create | show | update | run | cancel | logs"""

import os
import subprocess
import sys
import time
import urllib.parse

from agenthub_preflight import AgenthubPreflightError, prepare_local_job
from cli._api import API, APIError
from cli._config import load_config_file
from cli._output import die, print_json, print_receipt, print_table, short_id
from cli.commands.attempt import _normalize_attempt_files_payload

_POLL_INTERVAL = 5   # seconds between status checks for --wait
_WAIT_TIMEOUT  = 3600  # max seconds to wait


def _titleize_status(value: str) -> str:
    text = (value or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def _find_latest_ticket_receipt(logs: list[dict]) -> dict | None:
    for entry in reversed(logs or []):
        receipt = entry.get("receipt")
        if isinstance(receipt, dict):
            return receipt
    return None


def _render_ticket_receipt(receipt: dict, *, default_title: str) -> None:
    status = (receipt.get("status") or "").strip().lower()
    title = receipt.get("message") or (f"Ticket run {status}" if status else default_title)
    print_receipt(
        title,
        fields=[
            ("Status", status or "unknown"),
            ("Attempt", receipt.get("attempt_hash") or "unknown"),
            ("Commit", (receipt.get("agenthub_commit_hash") or "unknown")[:12] if receipt.get("agenthub_commit_hash") else "unknown"),
            ("Base", (receipt.get("base_hash") or "unknown")[:12] if receipt.get("base_hash") else "unknown"),
            ("Workdir", receipt.get("runner_workdir") or "unknown"),
            ("Evidence", receipt.get("evidence_summary") or "unknown"),
        ],
        next_commands=receipt.get("next_actions") or None,
    )


def _render_structured_ticket_event(entry: dict) -> bool:
    event = entry.get("event")
    if not isinstance(event, dict):
        return False
    title = f"{_titleize_status(event.get('phase', 'ticket'))} {(event.get('status') or 'update').replace('_', ' ').lower()}".strip()
    fields = [("When", event.get("timestamp") or entry.get("created_at") or "unknown")]
    if event.get("detail"):
        fields.append(("Detail", event.get("detail")))
    if event.get("hint"):
        fields.append(("Hint", event.get("hint")))
    print_receipt(
        title,
        fields=fields,
        next_commands=event.get("next_commands") or None,
    )
    return True


def register(subparsers) -> None:
    p = subparsers.add_parser("ticket", help="Manage tickets")
    sub = p.add_subparsers(dest="ticket_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list
    li = sub.add_parser("list", help="List tickets for a project")
    li.add_argument("project_id")

    # create
    cr = sub.add_parser("create", help="Create one or more tickets")
    cr.add_argument("project_id")
    cr.add_argument("--file", "-f", metavar="FILE",
                    help="JSON/YAML file with a ticket object or array of tickets")
    cr.add_argument("--title", "-t", help="Ticket title")
    cr.add_argument("--description", "-d", help="Ticket description")
    cr.add_argument("--rationale", help="Why this intent matters")
    cr.add_argument("--acceptance-criteria", dest="acceptance_criteria",
                    help="What done looks like")
    cr.add_argument("--constraints", help="Limits and non-goals for the agent")
    cr.add_argument("--column", default="backlog",
                    help="Column ID (default: backlog)")
    cr.add_argument("--priority", default="medium",
                    choices=["low", "medium", "high"],
                    help="Priority (default: medium)")
    cr.add_argument("--intent-status", dest="intent_status",
                    choices=["draft", "ready", "active", "blocked", "archived"],
                    default="ready",
                    help="Intent status (default: ready)")
    cr.add_argument("--base-leaf-id", dest="base_leaf_id",
                    help="Explicit AgentHub base leaf for this ticket")

    # show
    sh = sub.add_parser("show", help="Show ticket details")
    sh.add_argument("project_id")
    sh.add_argument("ticket_id")

    # update
    up = sub.add_parser("update", help="Update a ticket")
    up.add_argument("project_id")
    up.add_argument("ticket_id")
    up.add_argument("--title")
    up.add_argument("--description")
    up.add_argument("--rationale")
    up.add_argument("--acceptance-criteria", dest="acceptance_criteria")
    up.add_argument("--constraints")
    up.add_argument("--column", dest="column_id")
    up.add_argument("--priority", choices=["low", "medium", "high"])
    up.add_argument("--intent-status", dest="intent_status",
                    choices=["draft", "ready", "active", "blocked", "archived"])
    up.add_argument("--status")

    # run
    ru = sub.add_parser("run", help="Enqueue a ticket for agent execution")
    ru.add_argument("project_id")
    ru.add_argument("ticket_id")
    ru.add_argument("--wait", action="store_true",
                    help="Poll until the job completes or fails")
    ru.add_argument("--run-local", action="store_true",
                    help="Run agent directly on this host (dev mode, no coordinator needed)")

    rcf = sub.add_parser(
        "rerun-current-frontier",
        help="Reset ticket base to project.accepted_frontier_id and enqueue competing attempts",
    )
    rcf.add_argument("project_id")
    rcf.add_argument("ticket_id")
    rcf.add_argument(
        "--attempt-count",
        type=int,
        default=3,
        help="Number of attempts to enqueue from the current frontier (default: 3)",
    )

    # cancel
    ca = sub.add_parser("cancel", help="Request cancellation of a running ticket")
    ca.add_argument("project_id")
    ca.add_argument("ticket_id")

    # logs
    lo = sub.add_parser("logs", help="Fetch execution logs for a ticket")
    lo.add_argument("project_id")
    lo.add_argument("ticket_id")
    lo.add_argument("--raw", action="store_true", help="Include raw_output field")

    at = sub.add_parser("attempts", help="List attempts for a ticket")
    at.add_argument("project_id")
    at.add_argument("ticket_id")
    at.add_argument("--json", action="store_true", help="Print JSON output")

    aa = sub.add_parser("accept-attempt", help="Accept a ticket attempt")
    aa.add_argument("project_id")
    aa.add_argument("ticket_id")
    aa.add_argument("attempt_id")
    aa.add_argument("--reason", help="Operator note recorded client-side for context")
    aa.add_argument("--json", action="store_true", help="Print JSON output")

    ev = sub.add_parser("evaluate-attempts", help="Review competing attempts with deterministic agent-friendly output")
    ev.add_argument("project_id")
    ev.add_argument("ticket_id")
    ev.add_argument("--attempt", dest="attempt_ids", action="append", default=[],
                    help="Specific attempt ID to include; may be repeated")
    ev.add_argument("--latest", type=int, default=None,
                    help="Limit evaluation to the latest N attempts after filtering")
    ev.add_argument("--include-diff", action="store_true",
                    help="Include diff payloads for each considered attempt")
    ev.add_argument("--include-files", action="store_true",
                    help="Include structured changed-file metadata for each considered attempt")
    ev.add_argument("--max-diff-bytes", type=int, default=65536,
                    help="Maximum diff payload bytes per attempt (default: 65536)")
    ev.add_argument("--json", action="store_true", help="Print JSON output")

    cw = sub.add_parser("choose-winner", help="Choose the winning attempt without integrating it")
    cw.add_argument("project_id")
    cw.add_argument("ticket_id")
    cw.add_argument("attempt_id")
    cw.add_argument("--reason", help="Operator note recorded client-side for context")
    cw.add_argument("--dry-run", action="store_true", help="Run local preflights without mutating backend state")
    cw.add_argument("--expect-frontier", help="Fail unless project.accepted_frontier_id matches this hash")
    cw.add_argument("--json", action="store_true", help="Print JSON output")

    aw = sub.add_parser("accept-winner", help="Accept/integrate the chosen winner attempt")
    aw.add_argument("project_id")
    aw.add_argument("ticket_id")
    aw.add_argument("attempt_id")
    aw.add_argument("--expect-frontier", help="Fail unless project.accepted_frontier_id matches this hash")
    aw.add_argument("--json", action="store_true", help="Print JSON output")

    ra = sub.add_parser("reject-attempt", help="Reject a ticket attempt")
    ra.add_argument("project_id")
    ra.add_argument("ticket_id")
    ra.add_argument("attempt_id")
    ra.add_argument("--reason", required=True, help="Reason sent to the backend")
    ra.add_argument("--json", action="store_true", help="Print JSON output")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.ticket_cmd
    if cmd == "list":
        _cmd_list(args, api)
    elif cmd == "create":
        _cmd_create(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "update":
        _cmd_update(args, api)
    elif cmd == "run":
        _cmd_run(args, api)
    elif cmd == "rerun-current-frontier":
        _cmd_rerun_current_frontier(args, api)
    elif cmd == "cancel":
        _cmd_cancel(args, api)
    elif cmd == "logs":
        _cmd_logs(args, api)
    elif cmd == "attempts":
        _cmd_attempts(args, api)
    elif cmd == "accept-attempt":
        _cmd_accept_attempt(args, api)
    elif cmd == "evaluate-attempts":
        _cmd_evaluate_attempts(args, api)
    elif cmd == "choose-winner":
        _cmd_choose_winner(args, api)
    elif cmd == "accept-winner":
        _cmd_accept_winner(args, api)
    elif cmd == "reject-attempt":
        _cmd_reject_attempt(args, api)


# ---------------------------------------------------------------------------

def _cmd_list(args, api: API) -> None:
    try:
        tickets = api.get(f"/api/projects/{args.project_id}/tickets")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(tickets)
        return
    rows = [
        {
            "id": short_id(t.get("id", "")),
            "title": t.get("title", ""),
            "state": t.get("display_state") or t.get("column_id", ""),
            "intent": t.get("intent_status", "ready"),
            "priority": t.get("priority", ""),
            "attempt": (t.get("latest_attempt") or {}).get("short_commit_hash") or "",
        }
        for t in (tickets or [])
    ]
    print_table(rows, [
        ("id", "ID"),
        ("title", "TITLE"),
        ("state", "STATE"),
        ("intent", "INTENT"),
        ("priority", "PRIORITY"),
        ("attempt", "LATEST"),
    ])


def _apply_json_flag(args) -> None:
    if getattr(args, "json", False):
        args.output = "json"


def _cmd_create(args, api: API) -> None:
    if args.file:
        data = load_config_file(args.file)
        ticket_defs = data if isinstance(data, list) else [data]
    elif args.title:
        ticket_defs = [{
            "title": args.title,
            "description": getattr(args, "description", None),
            "rationale": getattr(args, "rationale", None),
            "acceptance_criteria": getattr(args, "acceptance_criteria", None),
            "constraints": getattr(args, "constraints", None),
            "base_leaf_id": getattr(args, "base_leaf_id", None),
            "column_id": args.column,
            "priority": args.priority,
            "intent_status": getattr(args, "intent_status", "ready"),
            "status": "todo",
        }]
    else:
        die("Provide --title or --file", output=args.output)

    created = []
    for td in ticket_defs:
        payload = {
            "title": td.get("title", ""),
            "column_id": td.get("column_id", td.get("column", "backlog")),
            "description": td.get("description"),
            "priority": td.get("priority", "medium"),
            "status": td.get("status", "todo"),
            "associated_node_ids": td.get("associated_node_ids", []),
            "associated_edge_ids": td.get("associated_edge_ids", []),
            "depends_on_ticket_ids": td.get("depends_on_ticket_ids", []),
            # Intent fields
            "intent_status": td.get("intent_status", "ready"),
            "rationale": td.get("rationale"),
            "acceptance_criteria": td.get("acceptance_criteria"),
            "constraints": td.get("constraints"),
            "base_leaf_id": td.get("base_leaf_id"),
        }
        try:
            ticket = api.post(f"/api/projects/{args.project_id}/tickets", payload)
            created.append(ticket)
        except APIError as e:
            print(f"Failed to create '{td.get('title')}': {e}", file=sys.stderr)

    if args.output == "json":
        print_json(created if len(created) != 1 else created[0])
        return
    for t in created:
        print(f"Created ticket {t['id']}: {t.get('title')}")


def _cmd_show(args, api: API) -> None:
    try:
        ticket = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(ticket)
        return
    for k, v in ticket.items():
        if v is not None:
            print(f"  {k}: {v}")


def _render_attempt_row(attempt: dict) -> dict[str, str]:
    return {
        "id": short_id(attempt.get("id", "")),
        "status": attempt.get("status", ""),
        "commit": attempt.get("short_commit_hash") or "",
        "base": (attempt.get("base_hash") or "")[:12],
        "attempt": str(attempt.get("attempt_num", "")),
        "tests": attempt.get("test_status") or "",
    }


def _query_string(params: dict[str, object]) -> str:
    encoded = urllib.parse.urlencode(
        [(key, value) for key, value in params.items() if value is not None]
    )
    return f"?{encoded}" if encoded else ""


def _get_project(api: API, project_id: str, *, output: str) -> dict:
    try:
        return api.get(f"/api/projects/{project_id}")
    except APIError as e:
        die(e, output=output)
    raise AssertionError("unreachable")


def _get_ticket(api: API, project_id: str, ticket_id: str, *, output: str) -> dict:
    try:
        return api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
    except APIError as e:
        die(e, output=output)
    raise AssertionError("unreachable")


def _get_ticket_attempts(api: API, project_id: str, ticket_id: str, *, output: str) -> list[dict]:
    try:
        attempts = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}/attempts")
    except APIError as e:
        die(e, output=output)
    ordered = list(attempts or [])
    ordered.sort(key=lambda item: (item.get("attempt_num") or -1, item.get("id") or ""), reverse=True)
    return ordered


def _get_project_attempt_detail(api: API, project_id: str, attempt_id: str, *, output: str) -> dict:
    try:
        return api.get(f"/api/projects/{project_id}/attempts/{attempt_id}")
    except APIError as e:
        die(e, output=output)
    raise AssertionError("unreachable")


def _attempt_status_value(attempt: dict) -> str:
    return (attempt.get("status") or "").strip().lower()


def _attempt_validated(attempt: dict) -> bool:
    return bool(attempt.get("validated"))


def _attempt_integrated(attempt: dict) -> bool:
    return bool(attempt.get("integrated") or attempt.get("accepted"))


def _attempt_is_integrated_like(attempt: dict) -> bool:
    return _attempt_integrated(attempt) or _attempt_status_value(attempt) in {
        "accepted",
        "composed",
        "integrated",
        "release_pr_open",
        "shipped",
    }


def _find_integrated_sibling_attempt(attempts: list[dict], attempt_id: str) -> dict | None:
    for sibling in attempts:
        sibling_id = sibling.get("id") or sibling.get("attempt_id")
        if sibling_id == attempt_id:
            continue
        if _attempt_is_integrated_like(sibling):
            return sibling
    return None


def _stale_reason_is_indeterminate(stale_reason: str | None) -> bool:
    return bool(stale_reason and stale_reason.lower().startswith("cannot determine"))


def _stale_acceptance_reason(attempt: dict, project: dict) -> str | None:
    stale = attempt.get("stale")
    stale_reason = (attempt.get("stale_reason") or "").strip() or None
    frontier = (project.get("accepted_frontier_id") or "").strip() or None
    base_hash = (attempt.get("base_hash") or "").strip() or None
    if stale is None:
        if frontier and base_hash:
            if base_hash != frontier:
                return stale_reason or "attempt.base_hash differs from project.accepted_frontier_id."
            return None
        return stale_reason or "Cannot determine attempt staleness."
    if _stale_reason_is_indeterminate(stale_reason):
        return stale_reason
    if stale is True:
        return stale_reason or "Attempt is stale."
    if stale_reason and frontier and base_hash and base_hash != frontier:
        return stale_reason
    return None


def _attempt_is_reviewable_candidate(attempt: dict) -> bool:
    return _attempt_validated(attempt) and not _attempt_integrated(attempt)


def _attempt_can_choose_winner(attempt: dict, attempts: list[dict]) -> bool:
    if not (_attempt_validated(attempt) or _attempt_status_value(attempt) == "validated"):
        return False
    if _attempt_is_integrated_like(attempt):
        return False
    attempt_id = attempt.get("attempt_id") or attempt.get("id")
    return _find_integrated_sibling_attempt(attempts, attempt_id) is None


def _build_review_commands(
    project_id: str,
    ticket_id: str,
    attempt_id: str,
    *,
    include_diff: bool,
    include_files: bool,
    max_diff_bytes: int | None,
) -> list[str]:
    commands = [
        f"ta attempt show {project_id} {attempt_id}",
        f"ta ticket attempts {project_id} {ticket_id}",
    ]
    if include_files:
        commands.append(f"ta attempt files {project_id} {attempt_id}")
    if include_diff:
        diff_cmd = f"ta attempt diff {project_id} {attempt_id}"
        if max_diff_bytes is not None:
            diff_cmd += f" --max-bytes {max_diff_bytes}"
        commands.append(diff_cmd)
    return commands


def _build_action_commands(
    project_id: str,
    ticket_id: str,
    attempt: dict,
    *,
    allow_choose_winner: bool = False,
    frontier: str | None = None,
) -> dict[str, str]:
    attempt_id = attempt.get("attempt_id") or attempt.get("id")
    commands = {
        "evaluate": f"ta ticket evaluate-attempts {project_id} {ticket_id} --attempt {attempt_id}",
        "reject": f"ta ticket reject-attempt {project_id} {ticket_id} {attempt_id} --reason \"needs revision\"",
    }
    if allow_choose_winner:
        commands["choose_winner"] = f"ta ticket choose-winner {project_id} {ticket_id} {attempt_id}"
    if attempt.get("is_winner"):
        accept_cmd = f"ta ticket accept-winner {project_id} {ticket_id} {attempt_id}"
        if frontier:
            accept_cmd += f" --expect-frontier {frontier}"
        commands["accept_winner"] = accept_cmd
    return commands


def _score_attempt(attempt: dict, frontier: str | None) -> tuple[int, list[str], list[str]]:
    status = _attempt_status_value(attempt)
    score = 0
    reasons: list[str] = []
    risks: list[str] = []
    if _attempt_validated(attempt):
        score += 60
        reasons.append("validated")
    else:
        score -= 40
        risks.append("not validated")
    if status in {"attempt_ready", "accepted", "composed", "release_pr_open", "shipped"}:
        score += 15
        reasons.append(f"status={status}")
    if status in {"failed", "rejected", "superseded"}:
        score -= 45
        risks.append(f"status={status}")
    stale = attempt.get("stale")
    if stale is False:
        score += 20
        reasons.append("not stale")
    elif stale is True:
        score -= 25
        risks.append(attempt.get("stale_reason") or "stale")
    if attempt.get("agenthub_commit_hash"):
        score += 10
        reasons.append("commit present")
    else:
        score -= 20
        risks.append("missing commit hash")
    if frontier and attempt.get("base_hash") == frontier:
        score += 10
        reasons.append("base matches current frontier")
    elif frontier and attempt.get("base_hash"):
        risks.append("base differs from current frontier")
    if attempt.get("is_winner"):
        score += 5
        reasons.append("already chosen winner")
    if _attempt_integrated(attempt):
        score += 5
        risks.append("already integrated")
    return score, reasons, risks


def _evaluate_attempt_payload(
    attempt: dict,
    *,
    sibling_attempts: list[dict],
    project_id: str,
    ticket_id: str,
    frontier: str | None,
    include_diff: bool,
    include_files: bool,
    max_diff_bytes: int | None,
) -> dict:
    attempt_id = attempt.get("attempt_id") or attempt.get("id")
    score, reasons, risks = _score_attempt(attempt, frontier)
    payload = {
        "attempt_id": attempt_id,
        "attempt_num": attempt.get("attempt_num"),
        "status": attempt.get("status"),
        "validated": _attempt_validated(attempt),
        "is_winner": bool(attempt.get("is_winner")),
        "integrated": _attempt_integrated(attempt),
        "stale": attempt.get("stale"),
        "stale_reason": attempt.get("stale_reason"),
        "base_hash": attempt.get("base_hash"),
        "base_matches_frontier": bool(frontier and attempt.get("base_hash") == frontier),
        "agenthub_commit_hash": attempt.get("agenthub_commit_hash"),
        "summary": attempt.get("summary"),
        "changed_files": list(attempt.get("changed_files") or []),
        "review_commands": _build_review_commands(
            project_id,
            ticket_id,
            attempt_id,
            include_diff=include_diff,
            include_files=include_files,
            max_diff_bytes=max_diff_bytes,
        ),
        "action_commands": _build_action_commands(
            project_id,
            ticket_id,
            attempt,
            allow_choose_winner=_attempt_can_choose_winner(attempt, sibling_attempts),
            frontier=frontier,
        ),
        "recommendation": {
            "score": score,
            "reasons": reasons,
            "risks": risks,
        },
    }
    return payload


def _render_evaluation_diff(api: API, project_id: str, attempt_id: str, *, max_diff_bytes: int | None) -> dict:
    path = f"/api/projects/{project_id}/attempts/{attempt_id}/diff" + _query_string({"max_bytes": max_diff_bytes})
    try:
        data = api.get(path)
    except APIError as e:
        return {
            "diff": None,
            "diff_bytes": 0,
            "diff_truncated": False,
            "diff_error": e.message,
        }
    if isinstance(data, dict):
        return {
            "diff": data.get("diff"),
            "diff_bytes": data.get("bytes", 0),
            "diff_truncated": bool(data.get("truncated")),
            "diff_error": data.get("unavailable_reason"),
        }
    text = str(data)
    return {
        "diff": text,
        "diff_bytes": len(text.encode("utf-8")),
        "diff_truncated": False,
        "diff_error": None,
    }


def _render_evaluation_files(api: API, project_id: str, attempt_id: str) -> dict:
    path = f"/api/projects/{project_id}/attempts/{attempt_id}/files"
    try:
        data = api.get(path)
    except APIError as e:
        return {"files": [], "files_error": e.message}
    artifact = _normalize_attempt_files_payload(data)
    files = list(artifact.get("files") or [])
    unavailable_reason = artifact.get("unavailable_reason")
    payload = {
        "files": files,
        "files_error": unavailable_reason if unavailable_reason and not files else None,
    }
    if isinstance(data, dict) and "unavailable_reason" in data:
        payload["unavailable_reason"] = unavailable_reason
    if isinstance(data, dict) and "next_actions" in data:
        payload["next_actions"] = list(artifact.get("next_actions") or [])
    if isinstance(data, dict) and "commit_available" in data:
        payload["commit_available"] = artifact.get("commit_available")
    return payload


def _filter_attempts(attempts: list[dict], attempt_ids: list[str], latest: int | None) -> list[dict]:
    selected = list(attempts)
    if attempt_ids:
        wanted = set(attempt_ids)
        selected = [attempt for attempt in selected if (attempt.get("id") or attempt.get("attempt_id")) in wanted]
    if latest is not None:
        if latest < 1:
            return []
        selected = selected[:latest]
    return selected


def _preflight_attempt(
    args,
    api: API,
    *,
    require_winner: bool = False,
    forbid_integrated: bool = False,
) -> tuple[dict, dict, dict, list[dict]]:
    project = _get_project(api, args.project_id, output=args.output)
    _get_ticket(api, args.project_id, args.ticket_id, output=args.output)
    attempts = _get_ticket_attempts(api, args.project_id, args.ticket_id, output=args.output)
    attempt_index = {
        (item.get("id") or item.get("attempt_id")): item
        for item in attempts
    }
    if args.attempt_id not in attempt_index:
        die(
            f"Attempt {args.attempt_id} is not part of ticket {args.ticket_id}.",
            output=args.output,
        )
    attempt = _get_project_attempt_detail(api, args.project_id, args.attempt_id, output=args.output)
    if attempt.get("ticket_id") != args.ticket_id:
        die(
            f"Attempt {args.attempt_id} does not belong to ticket {args.ticket_id}.",
            output=args.output,
        )
    frontier = (project.get("accepted_frontier_id") or "").strip() or None
    expected = (getattr(args, "expect_frontier", None) or "").strip() or None
    if expected and frontier != expected:
        die(
            f"Expected frontier {expected} but project.accepted_frontier_id is {frontier or 'unset'}.",
            output=args.output,
        )
    if not _attempt_validated(attempt):
        die(
            f"Attempt {args.attempt_id} is not validated and cannot be promoted.",
            output=args.output,
        )
    if forbid_integrated and (_attempt_integrated(attempt) or _attempt_status_value(attempt) in {"shipped", "release_pr_open", "composed"}):
        die(
            f"Attempt {args.attempt_id} is already integrated or shipped and cannot be chosen again.",
            output=args.output,
        )
    if require_winner and not attempt.get("is_winner"):
        die(
            f"Attempt {args.attempt_id} is not the chosen winner yet. Run choose-winner first.",
            output=args.output,
        )
    return project, {"id": args.ticket_id}, attempt, attempts


def _cmd_attempts(args, api: API) -> None:
    _apply_json_flag(args)
    try:
        attempts = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(attempts)
        return
    if not attempts:
        print("No attempts found for this ticket.")
        return
    print_table([_render_attempt_row(a) for a in attempts], [
        ("id", "ID"),
        ("status", "STATUS"),
        ("commit", "COMMIT"),
        ("base", "BASE"),
        ("attempt", "ATTEMPT"),
        ("tests", "TESTS"),
    ])
    latest = attempts[0]
    print("")
    print("Next:")
    print(f"  ta attempt show {args.project_id} {latest.get('id')}")
    print(f"  ta attempt diff {args.project_id} {latest.get('id')}")
    print(f"  ta ticket evaluate-attempts {args.project_id} {args.ticket_id} --latest 1 --include-diff --include-files")
    if _attempt_can_choose_winner(latest, attempts):
        print(f"  ta ticket choose-winner {args.project_id} {args.ticket_id} {latest.get('id')}")
    if latest.get("is_winner"):
        print(f"  ta ticket accept-winner {args.project_id} {args.ticket_id} {latest.get('id')}")
    if latest.get("status") not in {"rejected", "shipped", "superseded", "failed"}:
        print(f"  ta ticket reject-attempt {args.project_id} {args.ticket_id} {latest.get('id')} --reason \"needs revision\"")


def _cmd_update(args, api: API) -> None:
    payload = {}
    if args.title:
        payload["title"] = args.title
    if args.description:
        payload["description"] = args.description
    if getattr(args, "column_id", None):
        payload["column_id"] = args.column_id
    if getattr(args, "priority", None):
        payload["priority"] = args.priority
    if getattr(args, "status", None):
        payload["status"] = args.status
    if getattr(args, "intent_status", None):
        payload["intent_status"] = args.intent_status
    if getattr(args, "rationale", None):
        payload["rationale"] = args.rationale
    if getattr(args, "acceptance_criteria", None):
        payload["acceptance_criteria"] = args.acceptance_criteria
    if getattr(args, "constraints", None):
        payload["constraints"] = args.constraints
    if not payload:
        die("No fields to update.", output=args.output)
    try:
        ticket = api.patch(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}", payload
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(ticket)
        return
    print(f"Updated ticket {args.ticket_id}")


def _cmd_accept_attempt(args, api: API) -> None:
    _apply_json_flag(args)
    if args.reason and args.output != "json":
        print("Note: --reason is not sent by the current accept endpoint; recording it only in CLI output.")
    try:
        attempt = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts/{args.attempt_id}/accept",
            {},
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(attempt)
        return
    print_receipt(
        f"Accepted attempt {short_id(attempt.get('id', args.attempt_id))} for ticket {short_id(args.ticket_id)}.",
        fields=[
            ("Status", attempt.get("status")),
            ("Commit", attempt.get("short_commit_hash") or attempt.get("agenthub_commit_hash") or "unavailable"),
        ],
        next_commands=[
            f"ta attempt show {args.project_id} {attempt.get('id', args.attempt_id)}",
            f"ta ship candidates {args.project_id}",
        ],
    )


def _cmd_evaluate_attempts(args, api: API) -> None:
    _apply_json_flag(args)
    project = _get_project(api, args.project_id, output=args.output)
    _get_ticket(api, args.project_id, args.ticket_id, output=args.output)
    ticket_attempts = _get_ticket_attempts(api, args.project_id, args.ticket_id, output=args.output)
    selected = _filter_attempts(ticket_attempts, list(args.attempt_ids or []), args.latest)
    frontier = (project.get("accepted_frontier_id") or "").strip() or None

    evaluated: list[dict] = []
    for item in selected:
        attempt_id = item.get("id") or item.get("attempt_id")
        detail = _get_project_attempt_detail(api, args.project_id, attempt_id, output=args.output)
        payload = _evaluate_attempt_payload(
            detail,
            sibling_attempts=ticket_attempts,
            project_id=args.project_id,
            ticket_id=args.ticket_id,
            frontier=frontier,
            include_diff=bool(args.include_diff),
            include_files=bool(args.include_files),
            max_diff_bytes=getattr(args, "max_diff_bytes", None),
        )
        if args.include_files:
            payload.update(_render_evaluation_files(api, args.project_id, attempt_id))
        if args.include_diff:
            payload.update(_render_evaluation_diff(api, args.project_id, attempt_id, max_diff_bytes=getattr(args, "max_diff_bytes", None)))
        evaluated.append(payload)

    evaluated.sort(
        key=lambda item: (
            item["recommendation"]["score"],
            item.get("attempt_num") or -1,
            item.get("attempt_id") or "",
        ),
        reverse=True,
    )
    best = evaluated[0] if evaluated else None
    requested_artifacts = bool(args.include_diff or args.include_files)
    files_unavailable = bool(args.include_files and any(item.get("files_error") for item in evaluated))
    diff_unavailable = bool(args.include_diff and any(item.get("diff_error") for item in evaluated))
    artifacts_unavailable = files_unavailable or diff_unavailable
    review_complete = requested_artifacts and not artifacts_unavailable
    next_commands = [
        f"ta ticket attempts {args.project_id} {args.ticket_id}",
        f"ta ticket evaluate-attempts {args.project_id} {args.ticket_id} --include-diff --include-files",
    ]
    recommendation = {
        "attempt_id": best.get("attempt_id") if best else None,
        "score": best["recommendation"]["score"] if best else None,
        "reasons": list(best["recommendation"]["reasons"]) if best else [],
        "risks": list(best["recommendation"]["risks"]) if best else [],
        "review_complete": review_complete,
        "next_command": (
            f"ta ticket choose-winner {args.project_id} {args.ticket_id} {best.get('attempt_id')}"
            if best and best["action_commands"].get("choose_winner") and not artifacts_unavailable
            else None
        ),
    }
    if recommendation["next_command"]:
        next_commands.append(recommendation["next_command"])
    payload = {
        "project_id": args.project_id,
        "ticket_id": args.ticket_id,
        "attempt_count": len(evaluated),
        "review_complete": review_complete,
        "frontier_id": frontier,
        "attempts": evaluated,
        "recommendation": recommendation,
        "next_commands": next_commands,
    }
    if args.output == "json":
        print_json(payload)
        return
    rows = [
        {
            "attempt": item.get("attempt_num"),
            "id": short_id(item.get("attempt_id", "")),
            "status": item.get("status"),
            "score": str(item["recommendation"]["score"]),
            "winner": "yes" if item.get("is_winner") else "",
            "stale": "yes" if item.get("stale") else "",
        }
        for item in evaluated
    ]
    print_table(rows, [
        ("attempt", "ATTEMPT"),
        ("id", "ID"),
        ("status", "STATUS"),
        ("score", "SCORE"),
        ("winner", "WINNER"),
        ("stale", "STALE"),
    ])
    if best:
        print("")
        print_receipt(
            f"Recommended attempt {short_id(best.get('attempt_id', ''))}",
            fields=[
                ("Score", best["recommendation"]["score"]),
                ("Status", best.get("status") or "unknown"),
                ("Review", "complete" if review_complete else "incomplete"),
            ],
            next_commands=next_commands,
        )


def _cmd_choose_winner(args, api: API) -> None:
    _apply_json_flag(args)
    project, _ticket, attempt, attempts = _preflight_attempt(
        args,
        api,
        require_winner=False,
        forbid_integrated=True,
    )
    integrated_sibling = _find_integrated_sibling_attempt(attempts, args.attempt_id)
    if integrated_sibling is not None:
        sibling_id = integrated_sibling.get("id") or integrated_sibling.get("attempt_id") or "unknown"
        sibling_status = _attempt_status_value(integrated_sibling) or "unknown"
        die(
            f"Ticket {args.ticket_id} already has an integrated sibling attempt ({sibling_id}, status {sibling_status}).",
            output=args.output,
        )
    frontier = (project.get("accepted_frontier_id") or "").strip() or None
    next_command = f"ta ticket accept-winner {args.project_id} {args.ticket_id} {args.attempt_id}"
    if frontier:
        next_command += f" --expect-frontier {frontier}"
    if args.dry_run:
        payload = {
            "project_id": args.project_id,
            "ticket_id": args.ticket_id,
            "attempt_id": args.attempt_id,
            "dry_run": True,
            "frontier_changed": False,
            "accepted_frontier_id": frontier,
            "validated": True,
            "is_winner": bool(attempt.get("is_winner")),
            "next_command": next_command,
            "reason": args.reason,
        }
        if args.output == "json":
            print_json(payload)
            return
        print_receipt(
            f"Dry run: choose winner {short_id(args.attempt_id)}",
            fields=[
                ("Validated", "yes"),
                ("Frontier changed", "false"),
            ],
            next_commands=[next_command],
        )
        return
    body = {"reason": args.reason} if args.reason else {}
    try:
        response = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts/{args.attempt_id}/choose-winner",
            body,
        )
    except APIError as e:
        die(e, output=args.output)
    payload = {
        **response,
        "project_id": args.project_id,
        "ticket_id": args.ticket_id,
        "attempt_id": args.attempt_id,
        "dry_run": False,
        "frontier_changed": False,
        "accepted_frontier_id": (response.get("project") or {}).get("accepted_frontier_id") or response.get("accepted_frontier_id") or frontier,
        "next_command": next_command,
    }
    if args.output == "json":
        print_json(payload)
        return
    print_receipt(
        f"Winner chosen for ticket {short_id(args.ticket_id)}",
        fields=[
            ("Attempt", short_id(args.attempt_id)),
            ("Frontier changed", "false"),
        ],
        next_commands=[next_command],
    )


def _cmd_accept_winner(args, api: API) -> None:
    _apply_json_flag(args)
    project, _ticket, attempt, _attempts = _preflight_attempt(
        args,
        api,
        require_winner=True,
        forbid_integrated=False,
    )
    stale_reason = _stale_acceptance_reason(attempt, project)
    if stale_reason:
        die(
            f"Attempt {args.attempt_id} is stale and cannot be accepted locally. {stale_reason}",
            output=args.output,
        )
    previous_frontier_id = (project.get("accepted_frontier_id") or "").strip() or None
    was_already_integrated = _attempt_is_integrated_like(attempt)
    try:
        response = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts/{args.attempt_id}/accept",
            {},
        )
    except APIError as e:
        die(e, output=args.output)
    accepted_frontier_id = (
        response.get("accepted_frontier_id")
        or (response.get("project") or {}).get("accepted_frontier_id")
        or response.get("agenthub_commit_hash")
    )
    frontier_changed = (not was_already_integrated) and accepted_frontier_id != previous_frontier_id
    payload = {
        **response,
        "project_id": args.project_id,
        "ticket_id": args.ticket_id,
        "attempt_id": args.attempt_id,
        "frontier_changed": frontier_changed,
        "accepted_frontier_id": accepted_frontier_id,
        "next_commands": [
            f"ta attempt show {args.project_id} {args.attempt_id}",
            f"ta ship candidates {args.project_id}",
        ],
        "previous_frontier_id": previous_frontier_id,
    }
    if args.output == "json":
        print_json(payload)
        return
    print_receipt(
        f"Accepted winner {short_id(args.attempt_id)} for ticket {short_id(args.ticket_id)}.",
        fields=[
            ("Frontier changed", "true" if frontier_changed else "false"),
            ("Accepted frontier", accepted_frontier_id or "unknown"),
        ],
        next_commands=payload["next_commands"],
    )


def _cmd_reject_attempt(args, api: API) -> None:
    _apply_json_flag(args)
    try:
        attempt = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/attempts/{args.attempt_id}/reject",
            {"reason": args.reason},
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(attempt)
        return
    print_receipt(
        f"Rejected attempt {short_id(attempt.get('id', args.attempt_id))} for ticket {short_id(args.ticket_id)}.",
        fields=[
            ("Status", attempt.get("status")),
            ("Reason", args.reason),
        ],
        next_commands=[
            f"ta ticket attempts {args.project_id} {args.ticket_id}",
            f"ta ticket run {args.project_id} {args.ticket_id}",
        ],
    )


def _cmd_run(args, api: API) -> None:
    if args.run_local:
        _run_local(args, api)
        return

    # Enqueue by moving to in_progress
    try:
        ticket = api.patch(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}",
            {"column_id": "in_progress"},
        )
    except APIError as e:
        die(e, output=args.output)

    print_receipt(
        f"Ticket {short_id(args.ticket_id)} enqueued.",
        fields=[("Column", "in_progress")],
        next_commands=None if args.wait else [f"ta ticket logs {args.project_id} {args.ticket_id}"],
    )

    if not args.wait:
        return

    # Poll until no longer running / no longer in_progress
    print("Waiting for agent to complete", end="", flush=True)
    deadline = time.time() + _WAIT_TIMEOUT
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        print(".", end="", flush=True)
        try:
            ticket = api.get(
                f"/api/projects/{args.project_id}/tickets/{args.ticket_id}"
            )
        except APIError:
            continue
        if not ticket.get("is_running") and ticket.get("column_id") != "in_progress":
            print()
            col = ticket.get("column_id", "?")
            try:
                logs = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/logs")
            except APIError:
                logs = []
            receipt = _find_latest_ticket_receipt(logs)
            if receipt:
                _render_ticket_receipt(receipt, default_title=f"Ticket moved to: {col}")
            else:
                print_receipt(
                    f"Ticket moved to: {col}",
                    fields=[("Column", col)],
                    next_commands=[f"ta ticket logs {args.project_id} {args.ticket_id}"],
                )
            if args.output == "json":
                print_json(ticket)
            return
    print()
    die("Timed out waiting for ticket to complete.", output=args.output)


def _cmd_rerun_current_frontier(args, api: API) -> None:
    try:
        ticket = api.post(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/rerun-from-current-frontier",
            {"attempt_count": args.attempt_count},
        )
    except APIError as e:
        die(e, output=args.output)

    attempt_count = ticket.get("attempt_count", args.attempt_count)
    print_receipt(
        f"Ticket {short_id(args.ticket_id)} enqueued from current frontier.",
        fields=[
            ("Attempts", attempt_count),
            ("Column", ticket.get("column_id") or "unknown"),
            ("Base", (ticket.get("base_leaf_id") or "unknown")[:12]),
        ],
        next_commands=[
            f"ta ticket attempts {args.project_id} {args.ticket_id}",
            f"ta ticket logs {args.project_id} {args.ticket_id}",
        ],
    )


def _run_local(args, api: API) -> None:
    """Spawn agent.agent_runner directly on this host (dev mode)."""
    # Fetch project and ticket so local debug runs still honor explicit DAG lineage.
    try:
        project = api.get(f"/api/projects/{args.project_id}")
        ticket = api.get(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}")
    except APIError as e:
        die(e, output=args.output)

    repo_url = project.get("github_url") or ""
    api_url = api.base_url
    try:
        prepared = prepare_local_job(
            {
                "project_id": args.project_id,
                "ticket_id": args.ticket_id,
                "repo_url": repo_url,
                "execution_mode": "local",
                "git_mode": project.get("git_mode") or "swarm",
                "project_path": project.get("project_path"),
                "base_leaf_id": ticket.get("base_leaf_id"),
            }
        )
    except AgenthubPreflightError as e:
        die(str(e), output=args.output)

    # Repo root is two levels above this file: cli/../
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    env = {
        **os.environ,
        "TICKET_ID": args.ticket_id,
        "PROJECT_ID": args.project_id,
        "TERARCHITECT_API_URL": api_url,
        "REPO_URL": repo_url,
    }
    if prepared.get("base_hash"):
        env["BASE_HASH"] = prepared["base_hash"]
    if prepared.get("agenthub_root_hash"):
        env["AGENTHUB_ROOT_HASH"] = prepared["agenthub_root_hash"]

    print(f"Running agent locally for ticket {args.ticket_id}")
    result = subprocess.run(
        [sys.executable, "-m", "agent.agent_runner", "ticket"],
        cwd=repo_root,
        env=env,
    )
    sys.exit(result.returncode)


def _cmd_cancel(args, api: API) -> None:
    try:
        api.post(f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/cancel")
    except APIError as e:
        die(e, output=args.output)
    print(f"Cancellation requested for ticket {args.ticket_id}")


def _cmd_logs(args, api: API) -> None:
    try:
        logs = api.get(
            f"/api/projects/{args.project_id}/tickets/{args.ticket_id}/logs"
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(logs)
        return
    if not logs:
        print("(no logs yet)")
        return
    for entry in logs:
        if isinstance(entry.get("receipt"), dict):
            _render_ticket_receipt(entry["receipt"], default_title=entry.get("summary") or "Ticket run receipt")
            if args.raw and entry.get("raw_output"):
                print("    " + entry["raw_output"].replace("\n", "\n    "))
            continue
        if _render_structured_ticket_event(entry):
            if args.raw and entry.get("raw_output"):
                print("    " + entry["raw_output"].replace("\n", "\n    "))
            continue
        ts = (entry.get("created_at") or "")[:19].replace("T", " ")
        step = (entry.get("step") or "").ljust(28)
        summary = entry.get("summary") or ""
        ok = "✓" if entry.get("success", True) else "✗"
        print(f"[{ts}] {ok} {step}  {summary}")
        if args.raw and entry.get("raw_output"):
            print("    " + entry["raw_output"].replace("\n", "\n    "))
