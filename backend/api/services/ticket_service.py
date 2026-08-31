"""Ticket domain helpers: serialisation, enqueueing, dispatch, and display state."""
import uuid
from typing import Optional

from flask import current_app

from models.db import db, Project, Ticket, AgentJob, TicketAttempt
from .project_service import (
    compare_base_to_accepted_frontier as _compare_base_to_accepted_frontier,
    get_project_frontier_id as _get_project_frontier_id,
    normalize_frontier_id as _normalize_frontier_id,
    validate_project_frontier_candidate as _validate_project_frontier_candidate,
)
from .attempt_service import (
    SATISFIED_STATUSES as _SATISFIED_STATUSES,
    attempt_is_integrated as _attempt_is_integrated,
    attempt_is_validated as _attempt_is_validated,
    attempt_is_winner as _attempt_is_winner,
    attempt_satisfies_dependencies as _attempt_satisfies_dependencies,
    attempt_stale_status as _attempt_stale_status,
)
from .channel_service import (
    ticket_channel as _ticket_channel,
    post_event as _post_event,
    event_content as _event_content,
)
from .job_service import mvp_dependency_base_context as _mvp_dependency_base_context


MAX_PARALLEL_ATTEMPTS = 5
DEFAULT_TICKET_ATTEMPT_COUNT = 3
ATTEMPT_STRATEGIES = (
    {
        "key": "conservative-minimalist",
        "description": "Prefer the smallest safe change that satisfies the ticket and avoids broad refactors.",
    },
    {
        "key": "test-first-verifier",
        "description": "Lead with tests or explicit verification so the change is proven before broad edits.",
    },
    {
        "key": "architecture-cleanup",
        "description": "Improve structure where it directly clarifies the ticket and reduces local complexity.",
    },
    {
        "key": "performance-simplicity",
        "description": "Favor simple implementations with attention to obvious performance costs and unnecessary work.",
    },
    {
        "key": "product-polish",
        "description": "Bias toward user-facing clarity, edge-case handling, and finish quality while staying on scope.",
    },
)


def _clamp_attempt_count(value: int) -> int:
    return max(1, min(MAX_PARALLEL_ATTEMPTS, value))


def parse_attempt_count(
    raw_value,
    *,
    default: int,
    field_name: str = "attempt_count",
) -> tuple[int | None, str | None]:
    if raw_value is None:
        return _clamp_attempt_count(default), None
    if isinstance(raw_value, bool):
        return None, f"{field_name} must be an integer"
    if isinstance(raw_value, float):
        if not raw_value.is_integer():
            return None, f"{field_name} must be an integer"
        raw_value = int(raw_value)
    try:
        attempt_count = int(raw_value)
    except (TypeError, ValueError):
        return None, f"{field_name} must be an integer"
    if attempt_count < 1:
        return None, f"{field_name} must be at least 1"
    if attempt_count > MAX_PARALLEL_ATTEMPTS:
        return None, f"{field_name} must be at most {MAX_PARALLEL_ATTEMPTS}"
    return attempt_count, None


def ticket_default_attempt_count(ticket: Ticket | None) -> int:
    raw_value = getattr(ticket, "default_attempt_count", None) if ticket else None
    attempt_count, error = parse_attempt_count(
        raw_value,
        default=DEFAULT_TICKET_ATTEMPT_COUNT,
        field_name="default_attempt_count",
    )
    if error or attempt_count is None:
        return DEFAULT_TICKET_ATTEMPT_COUNT
    return attempt_count


def _attempt_strategy_for_index(attempt_index: int) -> dict:
    strategy = ATTEMPT_STRATEGIES[(attempt_index - 1) % len(ATTEMPT_STRATEGIES)]
    return dict(strategy)

def _has_accepted_attempt(ticket_id) -> bool:
    """True if the ticket has a winner that has been integrated for dependency use."""
    attempts = (
        TicketAttempt.query
        .filter_by(ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .all()
    )
    return any(_attempt_satisfies_dependencies(attempt) for attempt in attempts)


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


def ensure_ticket_base_leaf_id(
    ticket: Ticket | None,
    project: Project | None,
    *,
    persist: bool = False,
) -> tuple[str | None, str | None]:
    """Resolve and optionally persist the base leaf used for swarm ticket execution."""
    if not ticket:
        return None, "Ticket not found"

    git_mode = (getattr(project, "git_mode", None) or "swarm").strip().lower() if project else "swarm"
    current_value = _normalize_frontier_id(getattr(ticket, "base_leaf_id", None))
    if git_mode != "swarm":
        return current_value, None

    resolved = current_value or _get_project_frontier_id(project)
    if resolved is None:
        return (
            None,
            "No AgentHub frontier/base available for ticket dispatch: "
            "ticket.base_leaf_id is not set and project.accepted_frontier_id is not set.",
        )

    if current_value != resolved:
        ticket.base_leaf_id = resolved
        if persist:
            db.session.flush()

    valid, error = validate_ticket_base_leaf(project, resolved)
    if not valid:
        return None, error
    return resolved, None


def ticket_stale_status(ticket: Ticket, project: Project | None) -> tuple[Optional[bool], Optional[str]]:
    accepted_frontier_id = _get_project_frontier_id(project) if project else None
    return _compare_base_to_accepted_frontier(
        getattr(ticket, "base_leaf_id", None),
        accepted_frontier_id,
        subject_name="ticket",
        base_field_name="ticket.base_leaf_id",
    )


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
        stale, _ = _attempt_stale_status(accepted_attempt, project)
        if stale is True:
            return "stale"
        return "accepted"

    # Latest attempt — agent has run but attempt isn't integrated yet
    if latest_attempt:
        if latest_attempt.status in ("proposed", "validating", "validated"):
            return "attempt_ready"
        if latest_attempt.status == "failed":
            return "failed"

    # Execution column state
    if ticket.column_id == "in_progress":
        return "running"

    # Dependency check — a dep is unblocked only when it has a winning integrated attempt.
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
    project = db.session.get(Project, t.project_id)
    accepted_frontier_id = _get_project_frontier_id(project)
    stale, stale_reason = ticket_stale_status(t, project)
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
        "default_attempt_count": ticket_default_attempt_count(t),
        "depends_on_ticket_ids": t.depends_on_ticket_ids or [],
        "base_leaf_id": getattr(t, "base_leaf_id", None),
        "accepted_frontier_id": accepted_frontier_id,
        "stale": stale,
        "stale_reason": stale_reason,
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

    # Latest and accepted attempts
    latest = (
        TicketAttempt.query
        .filter_by(ticket_id=t.id)
        .order_by(TicketAttempt.attempt_num.desc())
        .first()
    )
    accepted = None
    if latest and _attempt_is_integrated(latest):
        accepted = latest
    elif latest:
        attempts = (
            TicketAttempt.query
            .filter_by(ticket_id=t.id)
            .order_by(TicketAttempt.attempt_num.desc())
            .all()
        )
        accepted = next((attempt for attempt in attempts if _attempt_satisfies_dependencies(attempt)), None)

    # Pre-fetch which dep tickets have winning integrated attempts (avoids N+1 inside compute_ticket_display_state)
    dep_ids = t.depends_on_ticket_ids or []
    satisfied_dep_ids: Optional[set] = None
    if dep_ids:
        dep_attempts = (
            TicketAttempt.query
            .filter(TicketAttempt.ticket_id.in_(dep_ids))
            .order_by(TicketAttempt.ticket_id.asc(), TicketAttempt.attempt_num.desc())
            .all()
        )
        satisfied_dep_ids = set()
        for attempt in dep_attempts:
            ticket_id = str(attempt.ticket_id)
            if ticket_id in satisfied_dep_ids:
                continue
            if _attempt_satisfies_dependencies(attempt):
                satisfied_dep_ids.add(ticket_id)

    out["display_state"] = compute_ticket_display_state(
        t,
        latest_attempt=latest,
        accepted_attempt=accepted,
        project=project,
        satisfied_dep_ids=satisfied_dep_ids,
    )

    if latest:
        commit = latest.agenthub_commit_hash or ""
        latest_stale, latest_stale_reason = _attempt_stale_status(latest, project)
        out["latest_attempt"] = {
            "id": str(latest.id),
            "short_commit_hash": commit[:12] if commit else None,
            "status": latest.status,
            "attempt_num": latest.attempt_num,
            "summary": latest.summary,
            "test_status": latest.test_status,
            "validated": _attempt_is_validated(latest),
            "is_winner": _attempt_is_winner(latest),
            "integrated": _attempt_is_integrated(latest),
            "accepted_frontier_id": accepted_frontier_id,
            "stale": latest_stale,
            "stale_reason": latest_stale_reason,
        }
    else:
        out["latest_attempt"] = None

    # Expose accepted attempt separately so callers (CLI workspace leaves, Ship Room)
    # can identify selectable leaves even when the latest attempt is failed/rejected.
    if accepted and accepted is not latest:
        acc_commit = accepted.agenthub_commit_hash or ""
        acc_stale, acc_stale_reason = _attempt_stale_status(accepted, project)
        out["accepted_attempt"] = {
            "id": str(accepted.id),
            "short_commit_hash": acc_commit[:12] if acc_commit else None,
            "status": accepted.status,
            "attempt_num": accepted.attempt_num,
            "validated": _attempt_is_validated(accepted),
            "is_winner": _attempt_is_winner(accepted),
            "integrated": _attempt_is_integrated(accepted),
            "accepted_frontier_id": accepted_frontier_id,
            "stale": acc_stale,
            "stale_reason": acc_stale_reason,
        }
    else:
        # latest IS accepted (or there's no accepted at all)
        out["accepted_attempt"] = out["latest_attempt"] if (accepted and accepted is latest) else None

    return out


# ---------------------------------------------------------------------------
# Enqueueing and dispatch
# ---------------------------------------------------------------------------

def _prepare_ticket_enqueue(ticket_id) -> tuple[Ticket | None, Project | None]:
    """Return enqueueable ticket/project pair, or (None, None) when dispatch should be skipped."""
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return None, None
    project = db.session.get(Project, ticket.project_id)
    if not project:
        return None, None
    execution_mode = getattr(project, "execution_mode", None) or "docker"
    if execution_mode == "local":
        if not (project.project_path or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project has no project path", ticket_id)
            return None, None
    else:
        if not (project.github_url or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project has no GitHub URL", ticket_id)
            return None, None

    # Dependency check: a dep is satisfied only when it has a winning integrated attempt.
    dep_ids = ticket.depends_on_ticket_ids or []
    for dep_id in dep_ids:
        if not _has_accepted_attempt(dep_id):
            dep = db.session.get(Ticket, dep_id)
            current_app.logger.info(
                "Skipping enqueue: ticket %s blocked by dep %s (%s) — no integrated winner attempt yet",
                ticket_id, dep_id, dep.title if dep else "?",
            )
            return None, None

    is_swarm = (getattr(project, "git_mode", None) or "swarm") == "swarm"
    if is_swarm:
        _, error = ensure_ticket_base_leaf_id(ticket, project, persist=True)
        if error:
            current_app.logger.info("Skipping enqueue: ticket %s invalid base leaf: %s", ticket_id, error)
            return None, None
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
            return None, None

    return ticket, project


def _create_ticket_jobs(ticket: Ticket, *, count: int) -> list[AgentJob]:
    count = _clamp_attempt_count(count)
    attempt_batch_id = str(uuid.uuid4())
    jobs: list[AgentJob] = []
    for attempt_index in range(1, count + 1):
        strategy = _attempt_strategy_for_index(attempt_index)
        jobs.append(
            AgentJob(
                ticket_id=ticket.id,
                project_id=ticket.project_id,
                kind="ticket",
                status="pending",
                attempt_metadata={
                    "attempt_batch_id": attempt_batch_id,
                    "attempt_index": attempt_index,
                    "attempt_count": count,
                    "attempt_strategy": strategy["key"],
                    "attempt_strategy_description": strategy["description"],
                },
            )
        )
    db.session.add_all(jobs)
    db.session.commit()
    return jobs


def enqueue_ticket_jobs(ticket_id, attempt_count: int | None = None) -> list[AgentJob]:
    """Enqueue one or more jobs for a ticket, assigning persisted attempt metadata."""
    ticket, _ = _prepare_ticket_enqueue(ticket_id)
    if not ticket:
        return []

    existing = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).with_for_update(skip_locked=True).first()
    if existing:
        current_app.logger.info("Skipping enqueue: ticket %s already has job %s", ticket_id, existing.id)
        return []

    count = attempt_count if attempt_count is not None else ticket_default_attempt_count(ticket)
    jobs = _create_ticket_jobs(ticket, count=count)
    current_app.logger.info(
        "Enqueued %s ticket job(s) for ticket %s",
        len(jobs),
        ticket_id,
    )
    event_payload = {
        "ticket_id": str(ticket_id),
        "project_id": str(ticket.project_id),
        "attempt_count": len(jobs),
    }
    batch_id = ((jobs[0].attempt_metadata or {}).get("attempt_batch_id") if jobs else None)
    if batch_id:
        event_payload["attempt_batch_id"] = batch_id
    _post_event(
        _ticket_channel(str(ticket_id)),
        _event_content(
            "ticket_assigned",
            f"Ticket assigned: {ticket.title[:200]}",
            event_payload,
        ),
    )
    return jobs


def enqueue_ticket_job(ticket_id):
    """Enqueue one ticket job. Skip if project missing URL/path or already pending/running."""
    jobs = enqueue_ticket_jobs(ticket_id, attempt_count=1)
    return jobs[0] if jobs else None


def enqueue_parallel_ticket_jobs(ticket_id, attempt_count: int) -> list[AgentJob]:
    """Enqueue an explicit competing-attempt batch for one ticket."""
    return enqueue_ticket_jobs(ticket_id, attempt_count=attempt_count)


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
            _, error = ensure_ticket_base_leaf_id(t, project, persist=True)
            if error:
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
        enqueue_ticket_jobs(t.id)
        if is_swarm:
            occupied_nodes.update(t.associated_node_ids or [])
            occupied_edges.update(t.associated_edge_ids or [])
