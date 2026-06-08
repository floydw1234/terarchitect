"""Worker job queue helpers: swarm job claiming and response serialisation."""
from collections import Counter

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
        ticket = db.session.get(Ticket, rj.ticket_id)
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
        ticket = db.session.get(Ticket, job.ticket_id)
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
      2. Multiple unshipped dependencies → use their composed temporary base.
      3. No deps → use project.shipped_frontier.
    """
    context = dependency_base_context(ticket, project)
    return context.get("base_hash")


def mvp_dependency_base_context(ticket: Ticket, project: Project) -> dict:
    """Return the explicit MVP base-selection context for a ticket.

    MVP order:
      1. No unshipped deps -> shipped_frontier
      2. One accepted unshipped dep -> that dep's commit hash
      3. All deps shipped -> shipped_frontier
      4. Multiple accepted unshipped deps -> blocked

    This path must not depend on temporary workspace composition.
    """
    dep_ids = ticket.depends_on_ticket_ids or []
    frontier = (project.shipped_frontier or None) if project else None

    if not dep_ids:
        return {
            "base_hash": frontier,
            "base_source": "shipped_frontier",
            "dependency_parent_hashes": [],
            "blocked": False,
            "blocked_reason": None,
            "temporary_base_required": False,
        }

    accepted_unshipped_attempts: list[TicketAttempt] = []
    shipped_attempts: list[TicketAttempt] = []
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
            shipped_attempts.append(attempt)
        else:
            accepted_unshipped_attempts.append(attempt)

    if not accepted_unshipped_attempts:
        return {
            "base_hash": frontier,
            "base_source": "shipped_frontier",
            "dependency_parent_hashes": [
                attempt.agenthub_commit_hash
                for attempt in shipped_attempts
                if attempt.agenthub_commit_hash
            ],
            "blocked": False,
            "blocked_reason": None,
            "temporary_base_required": False,
        }

    if len(accepted_unshipped_attempts) == 1:
        parent_hash = accepted_unshipped_attempts[0].agenthub_commit_hash or frontier
        return {
            "base_hash": parent_hash,
            "base_source": "accepted_dependency",
            "dependency_parent_hashes": [parent_hash] if parent_hash else [],
            "blocked": False,
            "blocked_reason": None,
            "temporary_base_required": False,
        }

    parent_hashes = _attempt_hashes(accepted_unshipped_attempts)
    return {
        "base_hash": None,
        "base_source": "blocked_multiple_unshipped_dependencies",
        "dependency_parent_hashes": parent_hashes,
        "blocked": True,
        "blocked_reason": (
            "Multiple accepted unshipped dependencies are blocked in the MVP path. "
            "Ship prerequisite work first or reduce the ticket to a single unshipped dependency."
        ),
        "temporary_base_required": False,
    }


def dependency_base_context(ticket: Ticket, project: Project) -> dict:
    """Compatibility base-selection context.

    This retains the older workspace-based behavior for legacy callers, but the
    MVP dispatch path uses mvp_dependency_base_context().
    """
    dep_ids = ticket.depends_on_ticket_ids or []
    frontier = (project.shipped_frontier or None) if project else None

    if not dep_ids:
        # Prefer blessed composite's composed commit over raw frontier
        # so agents building after a bless start from the preferred candidate state
        blessed_ws_id = getattr(project, "blessed_workspace_id", None) if project else None
        if blessed_ws_id:
            blessed = CompositeWorkspace.query.filter_by(
                id=blessed_ws_id, status="blessed"
            ).first()
            if blessed and blessed.composed_commit_hash:
                return {
                    "base_hash": blessed.composed_commit_hash,
                    "base_source": "blessed_workspace",
                    "dependency_parent_hashes": [],
                    "temporary_base_workspace_id": str(blessed.id),
                    "temporary_base_status": blessed.status,
                    "temporary_base_required": False,
                }
        return {
            "base_hash": frontier,
            "base_source": "frontier",
            "dependency_parent_hashes": [],
            "temporary_base_workspace_id": None,
            "temporary_base_status": None,
            "temporary_base_required": False,
        }

    unshipped_attempts = unshipped_dependency_attempts(ticket)
    if not unshipped_attempts:
        return {
            "base_hash": frontier,
            "base_source": "frontier",
            "dependency_parent_hashes": [],
            "temporary_base_workspace_id": None,
            "temporary_base_status": None,
            "temporary_base_required": False,
        }
    if len(unshipped_attempts) == 1:
        parent_hash = unshipped_attempts[0].agenthub_commit_hash or frontier
        return {
            "base_hash": parent_hash,
            "base_source": "single_dependency",
            "dependency_parent_hashes": [parent_hash] if parent_hash else [],
            "temporary_base_workspace_id": None,
            "temporary_base_status": None,
            "temporary_base_required": False,
        }

    workspace = ensure_temporary_dependency_base(ticket, project, unshipped_attempts)
    parent_hashes = _attempt_hashes(unshipped_attempts)
    ready = workspace and workspace.composed_commit_hash and workspace.status in {"preview_ready", "blessed", "snapshot_candidate"}
    return {
        "base_hash": workspace.composed_commit_hash if ready else None,
        "base_source": "temporary_dependency_base",
        "dependency_parent_hashes": parent_hashes,
        "temporary_base_workspace_id": str(workspace.id) if workspace else None,
        "temporary_base_status": workspace.status if workspace else None,
        "temporary_base_required": True,
    }


def unshipped_dependency_attempts(ticket: Ticket) -> list[TicketAttempt]:
    """Return satisfied dependency attempts that are not yet shipped into the frontier."""
    attempts: list[TicketAttempt] = []
    for dep_id in ticket.depends_on_ticket_ids or []:
        attempt = (
            TicketAttempt.query
            .filter_by(ticket_id=dep_id)
            .filter(TicketAttempt.status.in_(_SATISFIED_STATUSES))
            .order_by(TicketAttempt.attempt_num.desc())
            .first()
        )
        if attempt and attempt.status != "shipped":
            attempts.append(attempt)
    return attempts


def ensure_temporary_dependency_base(ticket: Ticket, project: Project, attempts: list[TicketAttempt]) -> CompositeWorkspace | None:
    """Find or create a Composite Workspace that composes multiple dependency leaves."""
    parent_hashes = _attempt_hashes(attempts)
    if len(parent_hashes) < 2:
        return None
    existing = _find_temporary_dependency_workspace(project.id, parent_hashes)
    if existing:
        return existing
    workspace = CompositeWorkspace(
        project_id=project.id,
        selected_attempt_ids=[str(attempt.id) for attempt in attempts],
        selected_leaf_hashes=parent_hashes,
        base_root_hash=project.shipped_frontier,
        status="queued",
        summary=f"Temporary dependency base for {ticket.title or ticket.id}",
        created_by="dependency_base_composer",
    )
    db.session.add(workspace)
    db.session.commit()
    current_app.logger.info(
        "base_selection created temporary dependency workspace=%s ticket=%s parents=%s",
        workspace.id,
        getattr(ticket, "id", None),
        parent_hashes,
    )
    return workspace


def _find_temporary_dependency_workspace(project_id, parent_hashes: list[str]) -> CompositeWorkspace | None:
    candidates = (
        CompositeWorkspace.query
        .filter_by(project_id=project_id, created_by="dependency_base_composer")
        .filter(CompositeWorkspace.status.in_(["queued", "composing", "preview_ready", "blessed", "snapshot_candidate"]))
        .order_by(CompositeWorkspace.created_at.desc())
        .all()
    )
    wanted = Counter(parent_hashes)
    for workspace in candidates:
        if Counter(workspace.selected_leaf_hashes or []) == wanted:
            return workspace
    return None


def _attempt_hashes(attempts: list[TicketAttempt]) -> list[str]:
    return [attempt.agenthub_commit_hash for attempt in attempts if attempt.agenthub_commit_hash]


def job_to_response(job):
    """Build JSON payload for a claimed job. Includes base_hash for AgentHub base selection."""
    project = db.session.get(Project, job.project_id)
    ticket = db.session.get(Ticket, job.ticket_id)
    repo_url = (project.github_url or "") if project else ""
    execution_mode = getattr(project, "execution_mode", None) or "docker" if project else "docker"
    git_mode = getattr(project, "git_mode", None) or "swarm" if project else "swarm"
    shipped_frontier = (getattr(project, "shipped_frontier", None) or None) if project else None

    base_context = {}
    base_hash = None
    if ticket and project and git_mode == "swarm":
        base_context = mvp_dependency_base_context(ticket, project)
        base_hash = base_context.get("base_hash")
        current_app.logger.info(
            "base_selection project=%s ticket=%s base=%s source=%s frontier=%s deps=%s",
            job.project_id, job.ticket_id,
            (base_hash or "")[:12] or "none",
            base_context.get("base_source"),
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
        "base_selection": base_context,
    }
    return out
