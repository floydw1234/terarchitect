"""Worker job queue helpers: swarm job claiming and response serialisation."""

from flask import current_app

from models.db import db, Project, Ticket, AgentJob, TicketAttempt
from .attempt_service import (
    SATISFIED_STATUSES as _SATISFIED_STATUSES,
    attempt_satisfies_dependencies as _attempt_satisfies_dependencies,
)
from .project_service import get_project_frontier_id as _get_project_frontier_id


def occupied_nodes_edges(project_id) -> tuple:
    """Return (occupied_node_ids, occupied_edge_ids) sets for all currently running
    jobs in the given project.  Used to gate swarm job dispatch."""
    running_jobs = AgentJob.query.filter_by(project_id=project_id, status="running").all()
    occupied_nodes: set = set()
    occupied_edges: set = set()
    for rj in running_jobs:
        ticket = db.session.get(Ticket, rj.ticket_id)
        if not ticket:
            continue
        for n in (ticket.associated_node_ids or []):
            occupied_nodes.add(n)
        for e in (ticket.associated_edge_ids or []):
            occupied_edges.add(e)
    return occupied_nodes, occupied_edges

def occupied_nodes_edges_for_other_tickets(project_id, ticket_id) -> tuple[set, set, int]:
    """Return occupied nodes/edges from running jobs excluding the given ticket.

    Explicit competing attempts for the same ticket should not block each other,
    but conflicts from other tickets must still be respected.
    """
    running_jobs = (
        AgentJob.query
        .filter_by(project_id=project_id, status="running")
        .filter(AgentJob.ticket_id != ticket_id)
        .all()
    )
    occupied_nodes: set = set()
    occupied_edges: set = set()
    for rj in running_jobs:
        ticket = db.session.get(Ticket, rj.ticket_id)
        if not ticket:
            continue
        for n in (ticket.associated_node_ids or []):
            occupied_nodes.add(n)
        for e in (ticket.associated_edge_ids or []):
            occupied_edges.add(e)
    return occupied_nodes, occupied_edges, len(running_jobs)



def claim_swarm_job(project_id):
    """Claim the first pending swarm job whose ticket's nodes/edges don't conflict
    with any currently running job.

    Rules:
      - A ticket with no associated nodes/edges is always dispatchable (no constraint).
      - A ticket with ["*"] (wildcard) may only run when nothing else is running.
      - Otherwise: skip if any of the ticket's node_ids or edge_ids are occupied.

    Same-ticket competing attempts are allowed to run together when they were
    explicitly enqueued, so claim checks ignore occupancy from already-running
    jobs on that same ticket.
    """
    pending_jobs = (
        AgentJob.query.filter_by(project_id=project_id, status="pending")
        .order_by(AgentJob.created_at.asc())
        .all()
    )

    for job in pending_jobs:
        ticket = db.session.get(Ticket, job.ticket_id)
        if not ticket:
            continue

        ticket_nodes = set(ticket.associated_node_ids or [])
        ticket_edges = set(ticket.associated_edge_ids or [])
        occ_nodes, occ_edges, other_running_jobs = occupied_nodes_edges_for_other_tickets(
            project_id,
            job.ticket_id,
        )
        has_other_running = other_running_jobs > 0

        # Unconstrained ticket — always runnable
        if not ticket_nodes and not ticket_edges:
            pass  # fall through to claim

        # Wildcard ticket: must wait for all others to finish first
        elif "*" in ticket_nodes:
            if has_other_running:
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


def claim_pending_job(project_id=None):
    """Claim the next pending ticket job, respecting swarm constraints for project-scoped claims."""
    if project_id:
        project = db.session.get(Project, project_id)
        if getattr(project, "git_mode", None) == "swarm":
            return claim_swarm_job(project_id)
        return (
            AgentJob.query.filter_by(project_id=project_id, status="pending")
            .order_by(AgentJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
    return (
        AgentJob.query.filter_by(status="pending")
        .order_by(AgentJob.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )


def compute_base_hash(ticket: Ticket, project: Project) -> str | None:
    """Return the DAG-native base hash for a ticket job, if one is available."""
    context = mvp_dependency_base_context(ticket, project)
    return context.get("base_hash")


def mvp_dependency_base_context(ticket: Ticket, project: Project) -> dict:
    """Return the explicit MVP base-selection context for a ticket.

    MVP order:
      1. No unshipped deps -> shipped_frontier
      2. One winning accepted/integrated unshipped dep -> that dep's commit hash
      3. All deps shipped -> shipped_frontier
      4. Multiple winning accepted/integrated unshipped deps -> blocked

    This path must not depend on temporary workspace composition.
    """
    dep_ids = ticket.depends_on_ticket_ids or []
    frontier = (project.shipped_frontier or None) if project else None

    if not dep_ids:
        return {
            "base_hash": frontier,
            "base_source": "shipped_frontier",
            "dependency_ticket_ids": [],
            "dependency_parent_hashes": [],
            "accepted_unshipped_dependency_ticket_ids": [],
            "shipped_dependency_ticket_ids": [],
            "resolved_from_ticket_id": None,
            "blocked": False,
            "blocked_reason": None,
        }

    accepted_unshipped_attempts: list[TicketAttempt] = []
    shipped_attempts: list[TicketAttempt] = []
    for dep_id in dep_ids:
        attempt = (
            TicketAttempt.query
            .filter_by(ticket_id=dep_id)
            .order_by(TicketAttempt.attempt_num.desc())
            .all()
        )
        attempt = next((row for row in attempt if _attempt_satisfies_dependencies(row)), None)
        if not attempt:
            continue
        if attempt.status == "shipped":
            shipped_attempts.append(attempt)
        else:
            accepted_unshipped_attempts.append(attempt)

    if not accepted_unshipped_attempts:
        return {
            "base_hash": frontier,
            "base_source": "shipped_frontier",
            "dependency_ticket_ids": [str(dep_id) for dep_id in dep_ids],
            "dependency_parent_hashes": [
                attempt.agenthub_commit_hash
                for attempt in shipped_attempts
                if attempt.agenthub_commit_hash
            ],
            "accepted_unshipped_dependency_ticket_ids": [],
            "shipped_dependency_ticket_ids": [str(attempt.ticket_id) for attempt in shipped_attempts],
            "resolved_from_ticket_id": None,
            "blocked": False,
            "blocked_reason": None,
        }

    if len(accepted_unshipped_attempts) == 1:
        parent_attempt = accepted_unshipped_attempts[0]
        parent_hash = parent_attempt.agenthub_commit_hash or frontier
        return {
            "base_hash": parent_hash,
            "base_source": "accepted_dependency",
            "dependency_ticket_ids": [str(dep_id) for dep_id in dep_ids],
            "dependency_parent_hashes": [parent_hash] if parent_hash else [],
            "accepted_unshipped_dependency_ticket_ids": [str(parent_attempt.ticket_id)],
            "shipped_dependency_ticket_ids": [str(attempt.ticket_id) for attempt in shipped_attempts],
            "resolved_from_ticket_id": str(parent_attempt.ticket_id),
            "blocked": False,
            "blocked_reason": None,
        }

    parent_hashes = _attempt_hashes(accepted_unshipped_attempts)
    return {
        "base_hash": None,
        "base_source": "blocked_multiple_unshipped_dependencies",
        "dependency_ticket_ids": [str(dep_id) for dep_id in dep_ids],
        "dependency_parent_hashes": parent_hashes,
        "accepted_unshipped_dependency_ticket_ids": [
            str(attempt.ticket_id) for attempt in accepted_unshipped_attempts
        ],
            "shipped_dependency_ticket_ids": [str(attempt.ticket_id) for attempt in shipped_attempts],
            "resolved_from_ticket_id": None,
            "blocked": True,
            "blocked_reason": (
                "Multiple winning accepted/integrated unshipped dependencies are blocked in the MVP path. "
                "Promote or ship prerequisite work first so exactly one stable base commit remains."
            ),
        }


def _attempt_hashes(attempts: list[TicketAttempt]) -> list[str]:
    return [attempt.agenthub_commit_hash for attempt in attempts if attempt.agenthub_commit_hash]



def _optional_attempt_metadata(job) -> dict:
    """Pass through optional competing-attempt metadata when present on the job."""
    sources = [job]
    for attr in ("attempt", "parallel_attempt", "attempt_metadata", "parallel_attempt_metadata"):
        value = getattr(job, attr, None)
        if isinstance(value, dict):
            sources.append(value)

    aliases = {
        "attempt_batch_id": ("attempt_batch_id", "parallel_attempt_batch_id", "batch_id"),
        "attempt_index": ("attempt_index", "parallel_attempt_index", "index"),
        "attempt_count": ("attempt_count", "parallel_attempt_count", "count"),
        "attempt_strategy": ("attempt_strategy", "parallel_attempt_strategy", "strategy", "attempt_slot", "parallel_attempt_slot", "slot"),
        "attempt_strategy_description": (
            "attempt_strategy_description",
            "parallel_attempt_strategy_description",
            "strategy_description",
            "description",
        ),
    }
    out = {}
    for field, names in aliases.items():
        for source in sources:
            for name in names:
                value = getattr(source, name, None) if source is job else source.get(name)
                if value is None:
                    continue
                text_value = str(value).strip()
                if text_value:
                    out[field] = text_value
                    break
            if field in out:
                break
    return out

def job_to_response(job):
    """Build JSON payload for a claimed job. Includes the explicit AgentHub base leaf."""
    project = db.session.get(Project, job.project_id)
    ticket = db.session.get(Ticket, job.ticket_id)
    repo_url = (project.github_url or "") if project else ""
    execution_mode = getattr(project, "execution_mode", None) or "docker" if project else "docker"
    git_mode = getattr(project, "git_mode", None) or "swarm" if project else "swarm"
    shipped_frontier = (getattr(project, "shipped_frontier", None) or None) if project else None
    accepted_frontier_id = _get_project_frontier_id(project) if project else None

    source_metadata = {
        "source_type": getattr(project, "source_type", None) or "local_path" if project else None,
        "github_url": repo_url or None,
        "github_ref": getattr(project, "github_ref", None) if project else None,
        "github_resolved_sha": getattr(project, "github_resolved_sha", None) if project else None,
    }

    base_context = {}
    base_leaf_id = None
    base_hash = None
    if ticket and project and git_mode == "swarm":
        ticket_base_leaf_id = (getattr(ticket, "base_leaf_id", None) or "").strip() or None
        base_leaf_id = ticket_base_leaf_id or accepted_frontier_id
        if base_leaf_id is None:
            raise ValueError(
                "Cannot dispatch swarm ticket job: ticket.base_leaf_id is not set "
                "and project.accepted_frontier_id is not set."
            )
        base_hash = base_leaf_id
        base_context = {
            "base_hash": base_hash,
            "base_leaf_id": base_leaf_id,
            "accepted_frontier_id": accepted_frontier_id,
            "base_source": "ticket_base_leaf" if ticket_base_leaf_id else "project_accepted_frontier",
            "blocked": False,
            "blocked_reason": None,
        }
        current_app.logger.info(
            "base_selection project=%s ticket=%s base_leaf=%s source=%s accepted_frontier=%s frontier=%s deps=%s",
            job.project_id, job.ticket_id,
            (base_leaf_id or "")[:12] or "none",
            base_context.get("base_source"),
            (accepted_frontier_id or "")[:12] or "none",
            (shipped_frontier or "")[:12] or "none",
            ticket.depends_on_ticket_ids or [],
        )

    attempt_metadata = _optional_attempt_metadata(job)
    active_jobs = (
        AgentJob.query
        .filter(
            AgentJob.ticket_id == job.ticket_id,
            AgentJob.status.in_(["pending", "running"]),
        )
        .order_by(AgentJob.created_at.asc(), AgentJob.id.asc())
        .all()
    )
    try:
        parallel_attempt_count = int(attempt_metadata.get("attempt_count") or len(active_jobs))
    except (TypeError, ValueError):
        parallel_attempt_count = len(active_jobs)
    try:
        parallel_attempt_index = int(
            attempt_metadata.get("attempt_index")
            or next((index for index, sibling in enumerate(active_jobs, start=1) if sibling.id == job.id), 1)
        )
    except (TypeError, ValueError):
        parallel_attempt_index = next(
            (index for index, sibling in enumerate(active_jobs, start=1) if sibling.id == job.id),
            1,
        )

    out = {
        "job_id": str(job.id),
        "ticket_id": str(job.ticket_id),
        "project_id": str(job.project_id),
        "kind": job.kind,
        "repo_url": repo_url,
        "github_url": repo_url or None,
        "execution_mode": execution_mode,
        "git_mode": git_mode,
        "base_leaf_id": base_leaf_id,
        "base_hash": base_hash,
        "accepted_frontier_id": accepted_frontier_id,
        "shipped_frontier": shipped_frontier,
        "agenthub_root_hash": base_hash,
        "base_selection": base_context,
        "source_type": source_metadata["source_type"],
        "source_metadata": source_metadata,
        "parallel_attempt_count": parallel_attempt_count,
        "parallel_attempt_index": parallel_attempt_index,
    }
    project_path = (project.project_path or "").strip() if project else ""
    if project_path:
        out["project_path"] = project_path
    if attempt_metadata:
        out["attempt_metadata"] = dict(attempt_metadata)
        out["metadata"] = dict(attempt_metadata)
    out.update(attempt_metadata)
    return out
