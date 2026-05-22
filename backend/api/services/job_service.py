"""Worker job queue helpers: swarm job claiming and response serialisation."""
from models.db import db, Project, Ticket, AgentJob


def occupied_nodes_edges(project_id) -> tuple:
    """Return (occupied_node_ids, occupied_edge_ids) sets for all currently running
    jobs in the given project.  Used to gate swarm job dispatch."""
    running_jobs = AgentJob.query.filter_by(project_id=project_id, status="running").all()
    occupied_nodes: set = set()
    occupied_edges: set = set()
    for rj in running_jobs:
        ticket = Ticket.query.get(rj.ticket_id)
        if not ticket:
            continue
        for n in (ticket.associated_node_ids or []):
            occupied_nodes.add(n)
        for e in (ticket.associated_edge_ids or []):
            occupied_edges.add(e)
    return occupied_nodes, occupied_edges


def claim_swarm_job(project_id):
    """Claim the first pending swarm job whose ticket's nodes/edges don't conflict
    with any currently running job.

    Rules:
      - A ticket with no associated nodes/edges is always dispatchable (no constraint).
      - A ticket with ["*"] (wildcard) may only run when nothing else is running.
      - Otherwise: skip if any of the ticket's node_ids or edge_ids are occupied.
    """
    occ_nodes, occ_edges = occupied_nodes_edges(project_id)
    has_running = bool(occ_nodes or occ_edges or
                       AgentJob.query.filter_by(project_id=project_id, status="running").first())

    pending_jobs = (
        AgentJob.query.filter_by(project_id=project_id, status="pending")
        .order_by(AgentJob.created_at.asc())
        .all()
    )

    for job in pending_jobs:
        ticket = Ticket.query.get(job.ticket_id)
        if not ticket:
            continue

        ticket_nodes = set(ticket.associated_node_ids or [])
        ticket_edges = set(ticket.associated_edge_ids or [])

        # Unconstrained ticket — always runnable
        if not ticket_nodes and not ticket_edges:
            pass  # fall through to claim

        # Wildcard ticket: must wait for all others to finish first
        elif "*" in ticket_nodes:
            if has_running:
                continue

        # Normal ticket: skip if any overlap with currently occupied nodes/edges
        elif ticket_nodes & occ_nodes or ticket_edges & occ_edges:
            continue

        # Try to claim this specific job (with_for_update guards against concurrent coordinators)
        claimed = (
            AgentJob.query.filter_by(id=job.id, status="pending")
            .with_for_update(skip_locked=True)
            .first()
        )
        if claimed:
            return claimed
        # Another coordinator just claimed it — keep looking


def job_to_response(job):
    """Build JSON payload for a claimed job."""
    project = Project.query.get(job.project_id)
    repo_url = (project.github_url or "") if project else ""
    execution_mode = getattr(project, "execution_mode", None) or "docker" if project else "docker"
    git_mode = getattr(project, "git_mode", None) or "structured" if project else "structured"
    out = {
        "job_id": str(job.id),
        "ticket_id": str(job.ticket_id),
        "project_id": str(job.project_id),
        "kind": job.kind,
        "repo_url": repo_url,
        "execution_mode": execution_mode,
        "git_mode": git_mode,
        "project_path": (project.project_path or "").strip() or None if project else None,
    }
    if job.kind == "review":
        out["pr_number"] = job.pr_number
        out["comment_body"] = job.comment_body or ""
        out["github_comment_id"] = job.github_comment_id
    return out
