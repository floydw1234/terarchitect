"""ship subcommand: inspect promotion candidates, compose ShipRuns, and ship."""

from cli._api import API, APIError
from cli._output import die, print_json, print_receipt, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "ship",
        help="Ship Room flow: review candidates, compose ShipRuns, inspect runs, then ship",
    )
    sub = p.add_subparsers(dest="ship_cmd", metavar="SUBCOMMAND")
    sub.required = True

    ca = sub.add_parser("candidates", help="List promotion candidates and their latest ShipRun state")
    ca.add_argument("project_id")
    ca.add_argument("--json", action="store_true", help="Print JSON for this command")

    cd = sub.add_parser("candidate", help="Show promotion candidate detail")
    cd.add_argument("project_id")
    cd.add_argument("candidate_id")
    cd.add_argument("--json", action="store_true", help="Print JSON for this command")

    cc = sub.add_parser("compose-candidate", help="Compose a promotion candidate into a ShipRun")
    cc.add_argument("project_id")
    cc.add_argument("candidate_id")
    cc.add_argument("--json", action="store_true", help="Print JSON for this command")

    rn = sub.add_parser("run", help="Show one ShipRun")
    rn.add_argument("project_id")
    rn.add_argument("run_id")
    rn.add_argument("--json", action="store_true", help="Print JSON for this command")

    sr = sub.add_parser("ship-run", help="Ship a ready ShipRun and advance the shipped frontier")
    sr.add_argument("project_id")
    sr.add_argument("run_id")
    sr.add_argument("--method", default="merge", choices=["merge", "squash", "rebase"],
                    help="Merge method (default: merge)")
    sr.add_argument("--json", action="store_true", help="Print JSON for this command")

    sc = sub.add_parser("ship-candidate", help="Ship the ready ShipRun for a promotion candidate")
    sc.add_argument("project_id")
    sc.add_argument("candidate_id")
    sc.add_argument("--method", default="merge", choices=["merge", "squash", "rebase"],
                    help="Merge method (default: merge)")
    sc.add_argument("--json", action="store_true", help="Print JSON for this command")

    doctor = sub.add_parser("doctor", help="Preflight Ship Room backend/runtime readiness for one project")
    doctor.add_argument("project_id")
    doctor.add_argument("--json", action="store_true", help="Print JSON for this command")

    hp = sub.add_parser("happy-path", help="Create or reuse a candidate from an accepted attempt and progress it")
    hp.add_argument("project_id")
    hp.add_argument("--ticket", dest="ticket_id", required=True, help="Ticket ID with an accepted attempt")
    hp.add_argument("--method", default="merge", choices=["merge", "squash", "rebase"],
                    help="Merge method when shipping (default: merge)")
    hp.add_argument("--json", action="store_true", help="Print JSON for this command")

    fb = sub.add_parser("feedback", help="Send operator feedback on a promotion candidate when supported")
    fb.add_argument("project_id")
    fb.add_argument("candidate_id")
    fb.add_argument("message", help="Feedback message")
    fb.add_argument("--ticket", dest="target_ticket_id", default=None,
                    help="Target a specific ticket ID instead of the candidate channel")
    fb.add_argument("--json", action="store_true", help="Print JSON for this command")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.ship_cmd
    if cmd == "candidates":
        _cmd_candidates(args, api)
    elif cmd == "candidate":
        _cmd_candidate(args, api)
    elif cmd == "compose-candidate":
        _cmd_compose_candidate(args, api)
    elif cmd == "run":
        _cmd_run(args, api)
    elif cmd == "ship-run":
        _cmd_ship_run(args, api)
    elif cmd == "ship-candidate":
        _cmd_ship_candidate(args, api)
    elif cmd == "doctor":
        _cmd_doctor(args, api)
    elif cmd == "happy-path":
        _cmd_happy_path(args, api)
    elif cmd == "feedback":
        _cmd_feedback(args, api)


def _want_json(args) -> bool:
    return getattr(args, "json", False) or getattr(args, "output", "human") == "json"


def _candidate_label(candidate: dict) -> str:
    return f"candidate {short_id(candidate.get('id', ''))}"


def _ship_run_line(run: dict) -> str:
    candidate_id = run.get("promotion_candidate_id")
    candidate_bit = f"  candidate={short_id(candidate_id)}" if candidate_id else ""
    return f"ShipRun {short_id(run['id'])}  status={run['status']}{candidate_bit}"


def _fetch_candidate_detail(api: API, project_id: str, candidate_id: str) -> dict:
    return api.get(f"/api/projects/{project_id}/ship/candidates/{candidate_id}")


def _cmd_candidates(args, api: API) -> None:
    try:
        candidates = api.get(f"/api/projects/{args.project_id}/ship/candidates")
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(candidates)
        return
    if not candidates:
        print("No promotion candidates found. Accept ticket attempts first, then create or review candidate sets in Ship Room.")
        return

    detailed: list[dict] = []
    for candidate in candidates:
        try:
            detailed.append(_fetch_candidate_detail(api, args.project_id, candidate["id"]))
        except APIError:
            detailed.append(candidate)

    rows = []
    for candidate in detailed:
        membership = candidate.get("membership") or {}
        latest_run = candidate.get("latest_ship_run") or {}
        validation = candidate.get("validation_summary") or {}
        blockers = validation.get("blockers") or candidate.get("validation_errors") or []
        rows.append({
            "candidate": short_id(candidate.get("id", "")),
            "status": candidate.get("status", ""),
            "attempts": str(len(membership.get("attempts") or candidate.get("attempts") or candidate.get("selected_attempt_ids") or [])),
            "tickets": str(len(membership.get("tickets") or [])) or "0",
            "run": latest_run.get("status") or "—",
            "frontier": (candidate.get("base_root_hash") or "")[:12] or "—",
            "blockers": str(len(blockers)),
        })
    print_table(rows, [
        ("candidate", "CANDIDATE"),
        ("status", "STATUS"),
        ("attempts", "ATTEMPTS"),
        ("tickets", "TICKETS"),
        ("run", "RUN"),
        ("frontier", "BASE"),
        ("blockers", "BLOCKERS"),
    ])
    print("")
    first = detailed[0]
    print(f"Next: ta ship candidate {args.project_id} {first.get('id')}")


def _print_candidate_detail(detail: dict) -> None:
    membership = detail.get("membership") or {}
    latest_run = detail.get("latest_ship_run")
    validation = detail.get("validation_summary") or {}
    blockers = detail.get("validation_errors") or validation.get("blockers") or []

    print(f"\n{_candidate_label(detail)}")
    print(f"  Status:      {detail.get('status') or 'unknown'}")
    print(f"  Base root:   {(detail.get('base_root_hash') or 'not set')[:16]}")
    print(f"  Attempts:    {len(membership.get('attempts') or detail.get('attempts') or [])}")
    print(f"  Tickets:     {len(membership.get('tickets') or [])}")
    if detail.get("composed_commit_hash"):
        print(f"  Composed:    {detail['composed_commit_hash'][:12]}")
    if latest_run:
        print(f"  Latest run:  {short_id(latest_run['id'])}  status={latest_run['status']}")

    print(f"\n  Blockers ({len(blockers)}):")
    if blockers:
        for blocker in blockers:
            print(f"    - {blocker}")
    else:
        print("    none")

    attempts = membership.get("attempts") or detail.get("attempts") or []
    print(f"\n  Attempts ({len(attempts)}):")
    for attempt in attempts:
        print(
            f"    {short_id(attempt.get('id', ''), 12)}  "
            f"ticket={short_id(attempt.get('ticket_id', ''), 12)}  "
            f"status={attempt.get('status', '?')}  "
            f"commit={(attempt.get('agenthub_commit_hash') or '?')[:12]}"
        )

    tickets = membership.get("tickets") or []
    if tickets:
        print(f"\n  Tickets ({len(tickets)}):")
        for ticket in tickets:
            print(f"    {short_id(ticket.get('id', ''), 12)}  {ticket.get('title', '')[:52]}")

    print("\n  Next:")
    print(f"    ta ship compose-candidate {detail['project_id']} {detail['id']}")
    if latest_run:
        print(f"    ta ship run {detail['project_id']} {latest_run['id']}")
        if latest_run.get("status") == "ready_to_ship":
            print(f"    ta ship ship-candidate {detail['project_id']} {detail['id']}")


def _cmd_candidate(args, api: API) -> None:
    try:
        detail = _fetch_candidate_detail(api, args.project_id, args.candidate_id)
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(detail)
        return
    _print_candidate_detail(detail)


def _print_run_detail(detail: dict) -> None:
    print(f"\n{_ship_run_line(detail)}")
    print(f"  Base:       {(detail.get('base_main_hash') or 'unknown')[:12]}")
    print(f"  Composed:   {(detail.get('composed_commit_hash') or 'unknown')[:12]}")
    if detail.get("shipped_commit_hash"):
        print(f"  Shipped:    {detail['shipped_commit_hash'][:12]}")
    if detail.get("release_pr_url"):
        print(f"  PR:         {detail['release_pr_url']}")
    if detail.get("release_branch"):
        print(f"  Branch:     {detail['release_branch']}")
    if detail.get("test_status"):
        print(f"  Tests:      {detail['test_status']}")
    if detail.get("error"):
        print(f"  Error:      {detail['error'][:120]}")
    candidate = detail.get("candidate")
    if candidate:
        print(f"  Candidate:  {short_id(candidate['id'])}")

    files = detail.get("changed_files") or []
    print(f"\n  Changed files ({len(files)}):")
    for path in files[:20]:
        print(f"    - {path}")

    validation_errors = detail.get("validation_errors") or []
    print(f"\n  Validation ({len(validation_errors)}):")
    if validation_errors:
        for error in validation_errors:
            print(f"    - {error}")
    else:
        print("    none")


def _cmd_run(args, api: API) -> None:
    try:
        detail = api.get(f"/api/projects/{args.project_id}/ship/runs/{args.run_id}")
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(detail)
        return
    _print_run_detail(detail)


def _cmd_compose_candidate(args, api: API) -> None:
    try:
        run = api.post(f"/api/projects/{args.project_id}/ship/candidates/{args.candidate_id}/compose", {})
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(run)
        return
    print(_ship_run_line(run))
    print("Coordinator will compose the candidate. Inspect the ShipRun before shipping.")


def _cmd_ship_run(args, api: API) -> None:
    try:
        run = api.post(
            f"/api/projects/{args.project_id}/ship/runs/{args.run_id}/ship",
            {"merge_method": args.method},
        )
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(run)
        return
    print(f"Shipped ShipRun {short_id(run['id'])}.")
    if run.get("shipped_commit_hash"):
        print(f"  New frontier: {run['shipped_commit_hash'][:12]}")
    if run.get("release_pr_url"):
        print(f"  PR:           {run['release_pr_url']}")


def _cmd_ship_candidate(args, api: API) -> None:
    try:
        run = api.post(
            f"/api/projects/{args.project_id}/ship/candidates/{args.candidate_id}/ship",
            {"merge_method": args.method},
        )
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(run)
        return
    print(f"Shipped {_candidate_label({'id': args.candidate_id})}.")
    if run.get("shipped_commit_hash"):
        print(f"  New frontier: {run['shipped_commit_hash'][:12]}")
    if run.get("release_pr_url"):
        print(f"  PR:           {run['release_pr_url']}")


def _cmd_doctor(args, api: API) -> None:
    try:
        report = api.get(f"/api/projects/{args.project_id}/ship/doctor")
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(report)
        return

    print(f"Ship doctor: {str(report.get('status') or 'unknown').upper()}")
    for check in report.get("checks") or []:
        print(f"  {check.get('name')}: {str(check.get('status') or '').upper()}  {check.get('summary') or ''}")
    if report.get("next_commands"):
        print("")
        print("Next:")
        for command in report["next_commands"]:
            print(f"  {command}")


def _cmd_happy_path(args, api: API) -> None:
    try:
        receipt = api.post(
            f"/api/projects/{args.project_id}/ship/happy-path",
            {"ticket_id": args.ticket_id, "merge_method": args.method},
        )
    except APIError as e:
        die(e, output=args.output)
    if _want_json(args):
        print_json(receipt)
        return

    fields = [
        ("Ticket", args.ticket_id),
        ("Status", receipt.get("status") or "unknown"),
    ]
    if receipt.get("attempt_id"):
        fields.append(("Attempt", short_id(receipt["attempt_id"], 12)))
    if receipt.get("candidate_id"):
        fields.append(("Candidate", short_id(receipt["candidate_id"], 12)))
    if receipt.get("ship_run_id"):
        fields.append(("ShipRun", short_id(receipt["ship_run_id"], 12)))
    if receipt.get("shipped_commit_hash"):
        fields.append(("Frontier", receipt["shipped_commit_hash"][:12]))
    print_receipt(
        "Ship happy path",
        fields=fields,
        next_commands=receipt.get("next_commands") or [],
    )


def _cmd_feedback(args, api: API) -> None:
    try:
        detail = _fetch_candidate_detail(api, args.project_id, args.candidate_id)
    except APIError as e:
        die(e, output=args.output)

    body = {"message": args.message}
    if args.target_ticket_id:
        body["target_ticket_id"] = args.target_ticket_id
    try:
        result = api.post(
            f"/api/projects/{args.project_id}/ship/candidates/{args.candidate_id}/feedback", body
        )
    except APIError as e:
        die(str(e))
    if _want_json(args):
        print_json(result)
        return
    print(f"Feedback posted for {_candidate_label(detail)}.")
