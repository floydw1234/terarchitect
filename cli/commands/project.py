"""project subcommand: list | create | show | doctor | update | import-agenthub-root | migration | delete"""

import sys
from cli._api import API, APIError
from cli._config import load_config_file
from cli._output import die, print_json, print_receipt, print_table, short_id


def register(subparsers) -> None:
    p = subparsers.add_parser("project", help="Manage projects")
    sub = p.add_subparsers(dest="project_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # list
    sub.add_parser("list", help="List all projects")

    # create
    c = sub.add_parser("create", help="Create a project (from flags or config file)")
    c.add_argument("--config", "-c", metavar="FILE", help="YAML or JSON config file")
    c.add_argument("--name", "-n", help="Project name")
    c.add_argument("--description", "-d", help="Project description")
    c.add_argument("--github-url", metavar="URL", help="GitHub repo URL")
    c.add_argument("--base-ref", metavar="REF", help="GitHub branch or ref to treat as the onboarding base")
    c.add_argument(
        "--import-to-agenthub",
        action="store_true",
        help="Request backend import of the GitHub project into AgentHub when supported",
    )
    c.add_argument(
        "--execution-mode",
        choices=["docker", "local"],
        default="docker",
        help="Agent execution mode (default: docker)",
    )
    c.add_argument("--git-mode", choices=["swarm"], default="swarm",
                   help="Git mode (always swarm)")
    c.add_argument("--project-path", metavar="PATH", help="Host path for local execution mode")
    c.add_argument("--accepted-frontier-id", metavar="ID", help="Canonical AgentHub frontier id for the project")
    c.add_argument("--existing-repo", action="store_true",
                   help="Skip creating default setup ticket (existing repo)")

    # show
    s = sub.add_parser("show", help="Show project details")
    s.add_argument("project_id", help="Project ID")

    # doctor
    d = sub.add_parser("doctor", help="Show operator diagnostics for a project")
    d.add_argument("project_ref", help="Project ID or exact project name")

    # update
    u = sub.add_parser("update", help="Update a project")
    u.add_argument("project_id", help="Project ID")
    u.add_argument("--name", help="New name")
    u.add_argument("--description", help="New description")
    u.add_argument("--github-url", metavar="URL")
    u.add_argument("--execution-mode", choices=["docker", "local"])
    u.add_argument("--git-mode", choices=["swarm"])
    u.add_argument("--project-path", metavar="PATH")
    u.add_argument("--accepted-frontier-id", metavar="ID")

    # delete
    dd = sub.add_parser("delete", help="Delete a project")
    dd.add_argument("project_id", help="Project ID")
    dd.add_argument("--confirm", metavar="NAME",
                    help="Project name to confirm deletion (prompted if omitted)")

    # import-agenthub-root
    imp = sub.add_parser("import-agenthub-root", help="Import a local repo into AgentHub and set the project frontier")
    imp.add_argument("project_id", help="Project ID")
    imp.add_argument("--path", metavar="PATH", help="Optional local repo path override")

    mig = sub.add_parser("migration", help="Admin tools for DAG source-of-truth migration")
    mig_sub = mig.add_subparsers(dest="migration_cmd", metavar="SUBCOMMAND")
    mig_sub.required = True

    mig_status = mig_sub.add_parser("status", help="Inspect DAG migration status for a project")
    mig_status.add_argument("project_id", help="Project ID")

    mig_frontier = mig_sub.add_parser("set-frontier", help="Explicitly set project.accepted_frontier_id")
    mig_frontier.add_argument("project_id", help="Project ID")
    mig_frontier.add_argument("--accepted-frontier-id", required=True, metavar="ID", help="Explicit AgentHub leaf id or hash")

    mig_backfill = mig_sub.add_parser("backfill-ticket-bases", help="Backfill missing ticket base_leaf_id values from project.accepted_frontier_id")
    mig_backfill.add_argument("project_id", help="Project ID")
    mig_backfill.add_argument("--dry-run", action="store_true", help="Report intended updates without writing")

    mig_import = mig_sub.add_parser("import-agenthub-root", help="Reuse the explicit local repo import path for initial AgentHub import")
    mig_import.add_argument("project_id", help="Project ID")
    mig_import.add_argument("--path", metavar="PATH", help="Optional local repo path override")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.project_cmd
    if cmd == "list":
        _cmd_list(args, api)
    elif cmd == "create":
        _cmd_create(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "doctor":
        _cmd_doctor(args, api)
    elif cmd == "update":
        _cmd_update(args, api)
    elif cmd == "import-agenthub-root":
        _cmd_import_agenthub_root(args, api)
    elif cmd == "migration":
        _dispatch_migration(args, api)
    elif cmd == "delete":
        _cmd_delete(args, api)


def _dispatch_migration(args, api: API) -> None:
    cmd = args.migration_cmd
    if cmd == "status":
        _cmd_migration_status(args, api)
    elif cmd == "set-frontier":
        _cmd_migration_set_frontier(args, api)
    elif cmd == "backfill-ticket-bases":
        _cmd_migration_backfill_ticket_bases(args, api)
    elif cmd == "import-agenthub-root":
        _cmd_import_agenthub_root(args, api)


# ---------------------------------------------------------------------------

def _cmd_list(args, api: API) -> None:
    try:
        projects = api.get("/api/projects")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(projects)
        return
    rows = [
        {
            "id": short_id(p.get("id", "")),
            "name": p.get("name", ""),
            "mode": p.get("execution_mode", "docker"),
            "git": p.get("git_mode", "swarm"),
            "github": (p.get("github_url") or "")[:40],
        }
        for p in (projects or [])
    ]
    print_table(rows, [
        ("id", "ID"),
        ("name", "NAME"),
        ("mode", "EXEC"),
        ("git", "GIT MODE"),
        ("github", "GITHUB URL"),
    ])


def _cmd_create(args, api: API) -> None:
    cfg = {}
    if args.config:
        cfg = load_config_file(args.config)

    payload = _build_create_payload(args, cfg)
    _validate_create_payload(payload, output=args.output)

    try:
        project = api.post("/api/projects", payload)
    except APIError as e:
        die(e, output=args.output)

    project = _augment_project_payload(project, payload)

    # Create any default_tickets specified in config
    default_tickets = cfg.get("default_tickets") or []
    for ticket_def in default_tickets:
        ticket_payload = {
            "column_id": ticket_def.get("column_id", "backlog"),
            "title": ticket_def.get("title", ""),
            "description": ticket_def.get("description"),
            "priority": ticket_def.get("priority", "medium"),
            "status": ticket_def.get("status", "todo"),
            "associated_node_ids": ticket_def.get("associated_node_ids", []),
        }
        try:
            api.post(f"/api/projects/{project['id']}/tickets", ticket_payload)
        except APIError as e:
            print(f"Warning: failed to create ticket '{ticket_def.get('title')}': {e}", file=sys.stderr)

    if args.output == "json":
        print_json(project)
        return
    fields = [
        ("ID", project.get("id")),
        ("Name", project.get("name")),
        ("GitHub URL", project.get("github_url")),
        ("Base ref", project.get("github_ref") or project.get("base_ref")),
        ("Resolved SHA", project.get("github_resolved_sha")),
        ("Accepted frontier", project.get("accepted_frontier_id")),
        ("Git mode", project.get("git_mode", "swarm")),
        ("Exec", project.get("execution_mode", "docker")),
    ]
    if default_tickets:
        fields.append(("Tickets created", len(default_tickets)))
    print_receipt(
        "Created project",
        fields=[(label, value) for label, value in fields if value is not None],
    )


def _build_create_payload(args, cfg: dict) -> dict:
    # CLI flags override config file values.
    payload = {
        "name": getattr(args, "name", None) or cfg.get("name"),
        "description": getattr(args, "description", None) or cfg.get("description"),
        "github_url": getattr(args, "github_url", None) or cfg.get("github_url"),
        "base_ref": getattr(args, "base_ref", None) or cfg.get("base_ref") or cfg.get("github_ref"),
        "execution_mode": getattr(args, "execution_mode", None) or cfg.get("execution_mode", "docker"),
        "git_mode": getattr(args, "git_mode", None) or cfg.get("git_mode", "swarm"),
        "project_path": getattr(args, "project_path", None) or cfg.get("project_path"),
        "accepted_frontier_id": getattr(args, "accepted_frontier_id", None) or cfg.get("accepted_frontier_id"),
        "is_existing_repo": getattr(args, "existing_repo", False) or cfg.get("is_existing_repo", False),
        "import_to_agenthub": getattr(args, "import_to_agenthub", False) or cfg.get("import_to_agenthub", False),
    }
    return {
        k: v
        for k, v in payload.items()
        if (v is not None and v is not False) or k == "is_existing_repo"
    }


def _validate_create_payload(payload: dict, *, output: str) -> None:
    if not payload.get("name"):
        die("--name is required (or provide it in --config file)", output=output)
    if not payload.get("github_url") and not payload.get("project_path"):
        die("Provide --github-url for GitHub-first onboarding or --project-path for legacy local onboarding.", output=output)
    if payload.get("base_ref") and not payload.get("github_url"):
        die("--base-ref requires --github-url.", output=output)
    if payload.get("execution_mode") == "local" and not payload.get("project_path"):
        die("--execution-mode local requires --project-path.", output=output)


def _augment_project_payload(project: dict, payload: dict) -> dict:
    result = dict(project or {})
    if payload.get("github_url") and not result.get("github_url"):
        result["github_url"] = payload["github_url"]
    if payload.get("base_ref") and not (result.get("github_ref") or result.get("base_ref")):
        result["base_ref"] = payload["base_ref"]
    return result


def _cmd_show(args, api: API) -> None:
    try:
        project = api.get(f"/api/projects/{args.project_id}")
    except APIError as e:
        die(e, output=args.output)
    project = _augment_project_payload(project, {})
    if args.output == "json":
        print_json(project)
        return
    for k, v in project.items():
        if v is not None:
            print(f"  {k}: {v}")


def _resolve_project_id(project_ref: str, api: API, *, output: str = "human") -> str:
    try:
        projects = api.get("/api/projects")
    except APIError as e:
        raise e.with_context(detail="Could not list projects to resolve the requested project.")

    matches = [
        project for project in (projects or [])
        if project.get("id") == project_ref or project.get("name") == project_ref
    ]
    if not matches:
        die(f"Project '{project_ref}' was not found.", output=output)
    if len(matches) > 1:
        die(f"Project name '{project_ref}' is ambiguous; use a project ID.", output=output)
    return matches[0]["id"]


def _cmd_doctor(args, api: API) -> None:
    project_id = _resolve_project_id(args.project_ref, api, output=args.output)
    try:
        result = api.get(f"/api/projects/{project_id}/doctor")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(result)
        return

    project = result.get("project") or {}
    latest_attempt = result.get("latest_attempt") or {}
    readiness = result.get("execution_readiness") or {}
    missing = readiness.get("missing") or []
    issues = readiness.get("issues") or []
    observations = readiness.get("observations") or []
    source_url = result.get("source_url") or "unset"
    source_ref = result.get("source_ref") or "unset"

    print(f"Project doctor for {project.get('name') or project_id} ({project_id})")
    print(f"  Source:         {result.get('source_type') or 'unknown'}")
    print(f"  Source URL:     {source_url}")
    print(f"  Source ref:     {source_ref}")
    print(f"  Frontier:       {result.get('accepted_frontier_id') or 'unset'}")
    print(f"  Frontier hash:  {result.get('accepted_frontier_hash') or 'unset'}")
    print(f"  Root hash:      {result.get('root_hash') or 'unset'}")
    print(f"  Exec:           {result.get('execution_mode') or 'unknown'}")
    print(f"  Legacy path:    {result.get('project_path') or 'unset'}")
    print(
        f"  Jobs:           pending={result.get('job_counts', {}).get('pending', 0)} "
        f"running={result.get('job_counts', {}).get('running', 0)}"
    )
    if latest_attempt:
        print(
            f"  Latest attempt: {latest_attempt.get('status') or 'unknown'} "
            f"ticket={short_id(latest_attempt.get('ticket_id') or '')} "
            f"commit={(latest_attempt.get('agenthub_commit_hash') or 'unset')[:12]} "
            f"stale={latest_attempt.get('stale')}"
        )
    else:
        print("  Latest attempt: none")
    print(f"  Ready:          {'yes' if readiness.get('ready') else 'no'}")
    for item in issues:
        print(f"  Issue:          {item}")
    for item in missing:
        print(f"  Missing:        {item.get('label')} ({item.get('key')})")
    for item in observations:
        print(f"  Note:           {item}")


def _cmd_update(args, api: API) -> None:
    payload = {}
    if args.name:
        payload["name"] = args.name
    if args.description:
        payload["description"] = args.description
    if getattr(args, "github_url", None):
        payload["github_url"] = args.github_url
    if getattr(args, "execution_mode", None):
        payload["execution_mode"] = args.execution_mode
    if getattr(args, "git_mode", None):
        payload["git_mode"] = args.git_mode
    if getattr(args, "project_path", None):
        payload["project_path"] = args.project_path
    if getattr(args, "accepted_frontier_id", None) is not None:
        payload["accepted_frontier_id"] = args.accepted_frontier_id
    if not payload:
        die("No fields to update. Pass at least one option.")
    try:
        project = api.put(f"/api/projects/{args.project_id}", payload)
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(project)
        return
    print(f"Updated project {args.project_id}")


def _cmd_delete(args, api: API) -> None:
    confirm = args.confirm
    if not confirm:
        try:
            project = api.get(f"/api/projects/{args.project_id}")
            name = project.get("name", "")
        except APIError:
            name = ""
        hint = f" [{name}]" if name else ""
        confirm = input(f"Type the project name to confirm deletion{hint}: ").strip()

    try:
        api.delete(f"/api/projects/{args.project_id}", {"confirm_name": confirm})
    except APIError as e:
        die(str(e))
    print(f"Deleted project {args.project_id}")


def _cmd_import_agenthub_root(args, api: API) -> None:
    payload = {}
    if getattr(args, "path", None):
        payload["path"] = args.path
    try:
        result = api.post(f"/api/projects/{args.project_id}/import-agenthub-root", payload)
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(result)
        return
    project = result.get("project") or {}
    import_result = result.get("import_result") or {}
    print(f"Imported AgentHub root for project {args.project_id}")
    print(f"  Frontier: {project.get('accepted_frontier_id') or import_result.get('accepted_frontier_id')}")
    print(f"  Path:     {import_result.get('path') or project.get('project_path')}")


def _cmd_migration_status(args, api: API) -> None:
    try:
        result = api.get(f"/api/projects/{args.project_id}/migration/status")
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(result)
        return
    print(f"Migration status for project {args.project_id}")
    print(f"  Accepted frontier: {result.get('accepted_frontier_id') or 'unset'}")
    local_path = result.get("local_path") or {}
    print(f"  Local path:         {local_path.get('path') or 'unset'}")
    print(f"  Ticket bases missing: {result.get('ticket_counts', {}).get('missing_base_leaf_id', 0)}")
    print(f"  Stale tickets:        {result.get('ticket_counts', {}).get('stale', 0)}")
    print(f"  Attempts missing base/parent: {result.get('attempt_counts', {}).get('missing_base_hash', 0)}")


def _cmd_migration_set_frontier(args, api: API) -> None:
    try:
        result = api.post(
            f"/api/projects/{args.project_id}/migration/set-frontier",
            {"accepted_frontier_id": args.accepted_frontier_id},
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(result)
        return
    project = result.get("project") or {}
    print_receipt(
        f"Set accepted frontier for project {args.project_id}",
        fields=[("Frontier", project.get("accepted_frontier_id"))],
    )


def _cmd_migration_backfill_ticket_bases(args, api: API) -> None:
    try:
        result = api.post(
            f"/api/projects/{args.project_id}/migration/backfill-ticket-bases",
            {"dry_run": bool(getattr(args, "dry_run", False))},
        )
    except APIError as e:
        die(e, output=args.output)
    if args.output == "json":
        print_json(result)
        return
    print_receipt(
        f"Backfill ticket bases for project {args.project_id}",
        fields=[
            ("Dry run", result.get("dry_run")),
            ("Frontier", result.get("accepted_frontier_id")),
            ("Updated", result.get("updated_count")),
        ],
    )
