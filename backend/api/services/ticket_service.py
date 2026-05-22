"""Ticket domain helpers: serialisation, enqueueing, and dispatch."""
from flask import current_app

from models.db import db, Project, Ticket, AgentJob


def ticket_to_json(t):
    out = {
        "id": str(t.id),
        "project_id": str(t.project_id),
        "column_id": str(t.column_id),
        "title": t.title,
        "description": t.description,
        "associated_node_ids": t.associated_node_ids,
        "associated_edge_ids": t.associated_edge_ids,
        "priority": t.priority,
        "status": t.status,
        "failed_count": t.failed_count or 0,
        "depends_on_ticket_ids": t.depends_on_ticket_ids or [],
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    running_job = AgentJob.query.filter_by(ticket_id=t.id, status="running").first()
    out["is_running"] = running_job is not None
    out["running_job_kind"] = running_job.kind if running_job else None
    if t.pr:
        out["pr_url"] = t.pr.pr_url
        out["pr_number"] = t.pr.pr_number
    else:
        out["pr_url"] = None
        out["pr_number"] = None
    return out


def enqueue_ticket_job(ticket_id):
    """Enqueue a ticket job to agent_jobs. Skip if project missing URL/path for mode or already pending/running."""
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return
    project = Project.query.get(ticket.project_id)
    if not project:
        return
    execution_mode = getattr(project, "execution_mode", None) or "docker"
    if execution_mode == "local":
        if not (project.project_path or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project is local but has no project path", ticket_id)
            return
    else:
        if not (project.github_url or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project has no GitHub URL", ticket_id)
            return
    dep_ids = ticket.depends_on_ticket_ids or []
    if dep_ids:
        blocking = Ticket.query.filter(
            Ticket.id.in_(dep_ids),
            Ticket.column_id != "done",
        ).first()
        if blocking:
            current_app.logger.info(
                "Skipping enqueue: ticket %s blocked by dependency %s (%s)",
                ticket_id, blocking.id, blocking.title,
            )
            return
    existing = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).with_for_update(skip_locked=True).first()
    if existing:
        current_app.logger.info("Skipping enqueue: ticket %s already has job %s", ticket_id, existing.id)
        return
    db.session.add(AgentJob(
        ticket_id=ticket_id,
        project_id=ticket.project_id,
        kind="ticket",
        status="pending",
    ))
    db.session.commit()
    current_app.logger.info("Enqueued ticket job for ticket %s", ticket_id)


def dispatch_unblocked_queued(project_id):
    """Move any queued tickets whose dependencies are all done to in_progress and enqueue them.
    In swarm mode, also holds back tickets whose graph nodes conflict with currently running tickets."""
    project = Project.query.get(project_id)
    is_swarm = project and (getattr(project, "git_mode", None) or "structured") == "swarm"

    # Pre-compute occupied nodes/edges from in_progress tickets (swarm only)
    occupied_nodes: set = set()
    occupied_edges: set = set()
    if is_swarm:
        running = Ticket.query.filter_by(project_id=project_id, column_id="in_progress").all()
        for rt in running:
            occupied_nodes.update(rt.associated_node_ids or [])
            occupied_edges.update(rt.associated_edge_ids or [])

    queued = Ticket.query.filter_by(project_id=project_id, column_id="queued").all()
    for t in queued:
        dep_ids = t.depends_on_ticket_ids or []
        if dep_ids:
            blocking = Ticket.query.filter(
                Ticket.id.in_(dep_ids),
                Ticket.column_id != "done",
            ).first()
            if blocking:
                continue
        if is_swarm:
            ticket_nodes = set(t.associated_node_ids or [])
            ticket_edges = set(t.associated_edge_ids or [])
            if ticket_nodes & occupied_nodes or ticket_edges & occupied_edges:
                continue  # Node conflict — leave in queued until the blocker finishes
        t.column_id = "in_progress"
        db.session.commit()
        enqueue_ticket_job(t.id)
        if is_swarm:
            occupied_nodes.update(t.associated_node_ids or [])
            occupied_edges.update(t.associated_edge_ids or [])
