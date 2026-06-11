"""Ticket domain helpers: serialisation, enqueueing, dispatch, and display state."""
from typing import Optional

from flask import current_app

from models.db import db, Project, Ticket, AgentJob, TicketAttempt
from .project_service import (
    get_project_frontier_id as _get_project_frontier_id,
    normalize_frontier_id as _normalize_frontier_id,
    validate_project_frontier_candidate as _validate_project_frontier_candidate,
)
from .attempt_service import SATISFIED_STATUSES as _SATISFIED_STATUSES
from .channel_service import (
    ticket_channel as _ticket_channel,
    post_event as _post_event,
    event_content as _event_content,
)
from .job_service import mvp_dependency_base_context as _mvp_dependency_base_context


def _has_accepted_attempt(ticket_id) -> bool:
    """True if the ticket has an accepted (or better) attempt."""
    return TicketAttempt.query.filter_by(ticket_id=ticket_id).filter(
        TicketAttempt.status.in_(_SATISFIED_STATUSES)
    ).first() is not None


def resolve_ticket_base_leaf_id(project: Project | None, explicit_value, *, explicit_provided: bool) -> str | None:
    """Resolve ticket base leaf from explicit input or the project's accepted frontier."""
    if explicit_provided:
        return _normalize_frontier_id(explicit_value)
    return _get_project_frontier_id(project) if project else None


def validate_ticket_base_leaf(project: Project | None, base_leaf_id) -> tuple[bool, str | None]:
    normalized = _normalize_frontier_id(base_leaf_id)
    git_mode = (getattr(project, "git_mode", None) or "swarm").strip().lower() if project else "swarm"

    if git_mode != "swarm":
        if normalized is None:
            return True, None
        return False, "base_leaf_id is only valid for swarm projects"

    if normalized is None:
        return (
            False,
            "base_leaf_id is required for swarm projects; set project.accepted_frontier_id or provide base_leaf_id explicitly",
        )

    valid, error = _validate_project_frontier_candidate(project, normalized)
    if valid:
        return True, None
    return False, (error or "base_leaf_id is invalid").replace("accepted_frontier_id", "base_leaf_id")


def compute_ticket_display_state(
    ticket: Ticket,
    *,
    latest_attempt: Optional[TicketAttempt] = None,
    accepted_attempt: Optional[TicketAttempt] = None,
    project: Optional[Project] = None,
    satisfied_dep_ids: Optional[set] = None,
) -> str:
    """Derive a single display state from intent + execution data.

    Precedence (highest first):
      archived / draft                       → intent is terminal
      accepted attempt path (shipped → ...)  → execution is authoritative
      latest attempt (attempt_ready, failed) → agent just finished or failed
      running job                            → currently executing
      blocked by deps (no accepted attempt)  → waiting on other work
      queued / backlog                       → waiting for dispatch
    """
    intent_status = getattr(ticket, "intent_status", None) or "ready"

    if intent_status == "archived":
        return "archived"
    if intent_status == "draft":
        return "draft"
    if intent_status == "blocked":
        return "blocked"

    # Accepted attempt branch — execution state is authoritative
    if accepted_attempt:
        s = accepted_attempt.status
        if s == "shipped":
            return "shipped"
        if s == "release_pr_open":
            return "release_pr_open"
        if s == "composed":
            return "composed"
        # accepted: may be stale
        frontier = getattr(project, "shipped_frontier", None) if project else None
        if frontier and accepted_attempt.base_hash and accepted_attempt.base_hash != frontier:
            return "stale"
        return "accepted"

    # Latest attempt — agent has run but attempt isn't accepted yet
    if latest_attempt:
        if latest_attempt.status in ("proposed", "validating"):
            return "attempt_ready"
        if latest_attempt.status == "failed":
            return "failed"

    # Execution column state
    if ticket.column_id == "in_progress":
        return "running"

    # Dependency check — a dep is unblocked only when it has an accepted attempt.
    # If satisfied_dep_ids is pre-computed (batch), use it; otherwise fall back to per-dep query.
    dep_ids = ticket.depends_on_ticket_ids or []
    if dep_ids:
        if satisfied_dep_ids is not None:
            if not all(str(d) in satisfied_dep_ids for d in dep_ids):
                return "blocked"
        else:
            for dep_id in dep_ids:
                if not _has_accepted_attempt(dep_id):
                    return "blocked"

    if ticket.column_id == "queued":
        return "queued"

    # backlog or ready intent with no activity
    return "queued"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def ticket_to_json(t: Ticket) -> dict:
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
        "base_leaf_id": getattr(t, "base_leaf_id", None),
        # Intent fields
        "intent_status": getattr(t, "intent_status", None) or "ready",
        "rationale": getattr(t, "rationale", None),
        "acceptance_criteria": getattr(t, "acceptance_criteria", None),
        "constraints": getattr(t, "constraints", None),
        "value_score": getattr(t, "value_score", None),
        "risk_level": getattr(t, "risk_level", None),
        "created_source": getattr(t, "created_source", None),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }

    running_job = AgentJob.query.filter_by(ticket_id=t.id, status="running").first()
    out["is_running"] = running_job is not None

    project = db.session.get(Project, t.project_id)

    # Latest and accepted attempts
    latest = (
        TicketAttempt.query
        .filter_by(ticket_id=t.id)
        .order_by(TicketAttempt.attempt_num.desc())
        .first()
    )
    accepted = None
    if latest and latest.status in _SATISFIED_STATUSES:
        accepted = latest
    elif latest:
        accepted = (
            TicketAttempt.query
            .filter_by(ticket_id=t.id)
            .filter(TicketAttempt.status.in_(_SATISFIED_STATUSES))
            .order_by(TicketAttempt.attempt_num.desc())
            .first()
        )

    # Pre-fetch which dep tickets have accepted attempts (avoids N+1 inside compute_ticket_display_state)
    dep_ids = t.depends_on_ticket_ids or []
    satisfied_dep_ids: Optional[set] = None
    if dep_ids:
        satisfied_rows = TicketAttempt.query.filter(
            TicketAttempt.ticket_id.in_(dep_ids),
            TicketAttempt.status.in_(_SATISFIED_STATUSES),
        ).with_entities(TicketAttempt.ticket_id).distinct().all()
        satisfied_dep_ids = {str(row[0]) for row in satisfied_rows}

    out["display_state"] = compute_ticket_display_state(
        t,
        latest_attempt=latest,
        accepted_attempt=accepted,
        project=project,
        satisfied_dep_ids=satisfied_dep_ids,
    )

    frontier = getattr(project, "shipped_frontier", None) or None
    if latest:
        commit = latest.agenthub_commit_hash or ""
        stale = (latest.base_hash != frontier) if (frontier and latest.base_hash) else None
        out["latest_attempt"] = {
            "id": str(latest.id),
            "short_commit_hash": commit[:12] if commit else None,
            "status": latest.status,
            "wave_num": latest.wave_num,
            "attempt_num": latest.attempt_num,
            "summary": latest.summary,
            "test_status": latest.test_status,
            "stale": stale,
        }
    else:
        out["latest_attempt"] = None

    # Expose accepted attempt separately so callers (CLI workspace leaves, Ship Room)
    # can identify selectable leaves even when the latest attempt is failed/rejected.
    if accepted and accepted is not latest:
        acc_commit = accepted.agenthub_commit_hash or ""
        acc_stale = (accepted.base_hash != frontier) if (frontier and accepted.base_hash) else None
        out["accepted_attempt"] = {
            "id": str(accepted.id),
            "short_commit_hash": acc_commit[:12] if acc_commit else None,
            "status": accepted.status,
            "wave_num": accepted.wave_num,
            "attempt_num": accepted.attempt_num,
            "stale": acc_stale,
        }
    else:
        # latest IS accepted (or there's no accepted at all)
        out["accepted_attempt"] = out["latest_attempt"] if (accepted and accepted is latest) else None

    return out


# ---------------------------------------------------------------------------
# Enqueueing and dispatch
# ---------------------------------------------------------------------------

def enqueue_ticket_job(ticket_id):
    """Enqueue a ticket job to agent_jobs. Skip if project missing URL/path or already pending/running."""
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return
    project = db.session.get(Project, ticket.project_id)
    if not project:
        return
    execution_mode = getattr(project, "execution_mode", None) or "docker"
    if execution_mode == "local":
        if not (project.project_path or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project has no project path", ticket_id)
            return
    else:
        if not (project.github_url or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project has no GitHub URL", ticket_id)
            return

    # Dependency check: a dep is satisfied when it has an accepted attempt
    dep_ids = ticket.depends_on_ticket_ids or []
    for dep_id in dep_ids:
        if not _has_accepted_attempt(dep_id):
            dep = db.session.get(Ticket, dep_id)
            current_app.logger.info(
                "Skipping enqueue: ticket %s blocked by dep %s (%s) — no accepted attempt yet",
                ticket_id, dep_id, dep.title if dep else "?",
            )
            return

    is_swarm = (getattr(project, "git_mode", None) or "swarm") == "swarm"
    if is_swarm:
        valid, error = validate_ticket_base_leaf(project, getattr(ticket, "base_leaf_id", None))
        if not valid:
            current_app.logger.info("Skipping enqueue: ticket %s invalid base leaf: %s", ticket_id, error)
            return
    if is_swarm:
        base_context = _mvp_dependency_base_context(ticket, project)
        if base_context.get("blocked") and not base_context.get("base_hash"):
            current_app.logger.info(
                "Skipping enqueue: ticket %s blocked by MVP base selection: %s",
                ticket_id,
                base_context.get("blocked_reason"),
            )
            if ticket.column_id == "in_progress":
                ticket.column_id = "queued"
                ticket.intent_status = "ready"
                db.session.commit()
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
    _post_event(
        _ticket_channel(str(ticket_id)),
        _event_content(
            "ticket_assigned",
            f"Ticket assigned: {ticket.title[:200]}",
            {"ticket_id": str(ticket_id), "project_id": str(ticket.project_id)},
        ),
    )


def dispatch_unblocked_queued(project_id):
    """Move queued tickets whose dependencies are satisfied to in_progress and enqueue.
    In swarm mode, also holds back tickets whose graph nodes conflict with running tickets."""
    project = db.session.get(Project, project_id)
    is_swarm = project and (getattr(project, "git_mode", None) or "swarm") == "swarm"

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
        if dep_ids and not all(_has_accepted_attempt(d) for d in dep_ids):
            continue
        if is_swarm:
            valid, error = validate_ticket_base_leaf(project, getattr(t, "base_leaf_id", None))
            if not valid:
                current_app.logger.info(
                    "dispatch waiting ticket=%s invalid_base_leaf=%s",
                    t.id,
                    error,
                )
                continue
            base_context = _mvp_dependency_base_context(t, project)
            if base_context.get("blocked") and not base_context.get("base_hash"):
                current_app.logger.info(
                    "dispatch waiting ticket=%s reason=%s deps=%s",
                    t.id,
                    base_context.get("blocked_reason"),
                    dep_ids,
                )
                continue
            ticket_nodes = set(t.associated_node_ids or [])
            ticket_edges = set(t.associated_edge_ids or [])
            if ticket_nodes & occupied_nodes or ticket_edges & occupied_edges:
                continue
        t.column_id = "in_progress"
        t.intent_status = "active"
        db.session.commit()
        enqueue_ticket_job(t.id)
        if is_swarm:
            occupied_nodes.update(t.associated_node_ids or [])
            occupied_edges.update(t.associated_edge_ids or [])
