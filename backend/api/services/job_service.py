"""Worker job queue helpers: swarm job claiming and response serialisation."""
from flask import current_app

from models.db import db, Project, Ticket, AgentJob, TicketAttempt, CompositeWorkspace
from .attempt_service import SATISFIED_STATUSES as _SATISFIED_STATUSES


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


def compute_base_hash(ticket: Ticket, project: Project) -> str | None:
    """Select the AgentHub commit hash this ticket should build on top of.

    Priority (from AGENTHUB-CONVERSION.md §Agent Base Selection):
      1. Single explicit dependency → use that dep's accepted attempt hash.
      2. Multiple dependencies → use the dep with the highest wave_num.
         (Full multi-dep composition into a temp commit is Phase 4 work.)
      3. No deps → use project.shipped_frontier.
    """
    dep_ids = ticket.depends_on_ticket_ids or []

    if not dep_ids:
        # Prefer blessed composite's composed commit over raw frontier
        # so agents building after a bless start from the preferred candidate state
        blessed_ws_id = getattr(project, "blessed_workspace_id", None) if project else None
        if blessed_ws_id:
            blessed = CompositeWorkspace.query.filter_by(
                id=blessed_ws_id, status="blessed"
            ).first()
            if blessed and blessed.composed_commit_hash:
                return blessed.composed_commit_hash
        return (project.shipped_frontier or None) if project else None

    # Plan 4.6: if dep is already shipped into main, its changes are in shipped_frontier.
    # Use shipped_frontier as base (not the pre-merge AgentHub hash).
    # For unshipped accepted deps, use the dep's AgentHub commit hash.
    best_hash = None
    best_wave = -1
    all_shipped = True

    for dep_id in dep_ids:
        attempt = (
            TicketAttempt.query
            .filter_by(ticket_id=dep_id)
            .filter(TicketAttempt.status.in_(_SATISFIED_STATUSES))
            .order_by(TicketAttempt.attempt_num.desc())
            .first()
        )
        if not attempt:
            continue
        if attempt.status == "shipped":
            # This dep is in main; its contribution is captured in shipped_frontier.
            continue
        all_shipped = False
        if attempt.agenthub_commit_hash and attempt.wave_num > best_wave:
            best_hash = attempt.agenthub_commit_hash
            best_wave = attempt.wave_num

    # If all deps are shipped (in main), or no unshipped dep hash found, use frontier
    frontier = (project.shipped_frontier or None) if project else None
    if all_shipped or not best_hash:
        return frontier
    return best_hash


def job_to_response(job):
    """Build JSON payload for a claimed job. Includes base_hash for AgentHub base selection."""
    project = Project.query.get(job.project_id)
    ticket = Ticket.query.get(job.ticket_id)
    repo_url = (project.github_url or "") if project else ""
    execution_mode = getattr(project, "execution_mode", None) or "docker" if project else "docker"
    git_mode = getattr(project, "git_mode", None) or "swarm" if project else "swarm"
    shipped_frontier = (getattr(project, "shipped_frontier", None) or None) if project else None

    base_hash = None
    if ticket and project and git_mode == "swarm":
        base_hash = compute_base_hash(ticket, project)
        current_app.logger.info(
            "base_selection project=%s ticket=%s base=%s frontier=%s deps=%s",
            job.project_id, job.ticket_id,
            (base_hash or "")[:12] or "none",
            (shipped_frontier or "")[:12] or "none",
            ticket.depends_on_ticket_ids or [],
        )

    out = {
        "job_id": str(job.id),
        "ticket_id": str(job.ticket_id),
        "project_id": str(job.project_id),
        "kind": job.kind,
        "repo_url": repo_url,
        "execution_mode": execution_mode,
        "git_mode": git_mode,
        "project_path": (project.project_path or "").strip() or None if project else None,
        "base_hash": base_hash,
        "shipped_frontier": shipped_frontier,
    }
    return out
