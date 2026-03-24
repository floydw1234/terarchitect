"""project subcommand: list | create | show | update | delete"""

import sys
from cli._api import API, APIError
from cli._config import load_config_file
from cli._output import die, print_json, print_table, short_id


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
    c.add_argument(
        "--execution-mode",
        choices=["docker", "local"],
        default="docker",
        help="Agent execution mode (default: docker)",
    )
    c.add_argument("--git-mode", choices=["structured", "swarm"], default="structured",
                   help="Git mode (default: structured)")
    c.add_argument("--project-path", metavar="PATH", help="Host path for local execution mode")
    c.add_argument("--existing-repo", action="store_true",
                   help="Skip creating default setup ticket (existing repo)")

    # show
    s = sub.add_parser("show", help="Show project details")
    s.add_argument("project_id", help="Project ID")

    # update
    u = sub.add_parser("update", help="Update a project")
    u.add_argument("project_id", help="Project ID")
    u.add_argument("--name", help="New name")
    u.add_argument("--description", help="New description")
    u.add_argument("--github-url", metavar="URL")
    u.add_argument("--execution-mode", choices=["docker", "local"])
    u.add_argument("--git-mode", choices=["structured", "swarm"])
    u.add_argument("--project-path", metavar="PATH")

    # delete
    dd = sub.add_parser("delete", help="Delete a project")
    dd.add_argument("project_id", help="Project ID")
    dd.add_argument("--confirm", metavar="NAME",
                    help="Project name to confirm deletion (prompted if omitted)")

    p.set_defaults(func=_dispatch)


def _dispatch(args, api: API) -> None:
    cmd = args.project_cmd
    if cmd == "list":
        _cmd_list(args, api)
    elif cmd == "create":
        _cmd_create(args, api)
    elif cmd == "show":
        _cmd_show(args, api)
    elif cmd == "update":
        _cmd_update(args, api)
    elif cmd == "delete":
        _cmd_delete(args, api)


# ---------------------------------------------------------------------------

def _cmd_list(args, api: API) -> None:
    try:
        projects = api.get("/api/projects")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(projects)
        return
    rows = [
        {
            "id": short_id(p.get("id", "")),
            "name": p.get("name", ""),
            "mode": p.get("execution_mode", "docker"),
            "git": p.get("git_mode", "structured"),
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

    # CLI flags override config file values
    payload = {
        "name": getattr(args, "name", None) or cfg.get("name"),
        "description": getattr(args, "description", None) or cfg.get("description"),
        "github_url": getattr(args, "github_url", None) or cfg.get("github_url"),
        "execution_mode": getattr(args, "execution_mode", None) or cfg.get("execution_mode", "docker"),
        "git_mode": getattr(args, "git_mode", None) or cfg.get("git_mode", "structured"),
        "project_path": getattr(args, "project_path", None) or cfg.get("project_path"),
        "is_existing_repo": getattr(args, "existing_repo", False) or cfg.get("is_existing_repo", False),
    }
    payload = {k: v for k, v in payload.items() if v is not None and v is not False or k == "is_existing_repo"}

    if not payload.get("name"):
        die("--name is required (or provide it in --config file)")

    try:
        project = api.post("/api/projects", payload)
    except APIError as e:
        die(str(e))

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
    print(f"Created project: {project['id']}")
    print(f"  Name:     {project.get('name')}")
    print(f"  Git mode: {project.get('git_mode', 'structured')}")
    print(f"  Exec:     {project.get('execution_mode', 'docker')}")
    if default_tickets:
        print(f"  Tickets created: {len(default_tickets)}")


def _cmd_show(args, api: API) -> None:
    try:
        project = api.get(f"/api/projects/{args.project_id}")
    except APIError as e:
        die(str(e))
    if args.output == "json":
        print_json(project)
        return
    for k, v in project.items():
        if v is not None:
            print(f"  {k}: {v}")


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
    if not payload:
        die("No fields to update. Pass at least one option.")
    try:
        project = api.put(f"/api/projects/{args.project_id}", payload)
    except APIError as e:
        die(str(e))
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
