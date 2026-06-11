"""Explicit admin helpers for DAG source-of-truth migration."""
import os

from models.db import db, Ticket, TicketAttempt, Project
from .project_service import (
    get_project_frontier_id as _get_project_frontier_id,
    normalize_frontier_id as _normalize_frontier_id,
    validate_project_frontier_candidate as _validate_project_frontier_candidate,
)
from .ticket_service import ticket_stale_status as _ticket_stale_status


def project_local_path_metadata(project: Project) -> dict:
    path = (getattr(project, "project_path", None) or "").strip() or None
    if not path:
        return {
            "path": None,
            "exists": False,
            "is_directory": False,
        }
    return {
        "path": path,
        "exists": os.path.exists(path),
        "is_directory": os.path.isdir(path),
    }


def project_migration_status(project: Project) -> dict:
    accepted_frontier_id = _get_project_frontier_id(project)
    tickets = (
        Ticket.query
        .filter_by(project_id=project.id)
        .order_by(Ticket.created_at.asc(), Ticket.id.asc())
        .all()
    )
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project.id)
        .order_by(TicketAttempt.created_at.asc(), TicketAttempt.id.asc())
        .all()
    )

    tickets_missing_base_leaf_ids = []
    stale_tickets = []
    for ticket in tickets:
        if not _normalize_frontier_id(getattr(ticket, "base_leaf_id", None)):
            tickets_missing_base_leaf_ids.append({
                "id": str(ticket.id),
                "title": ticket.title,
            })
        stale, stale_reason = _ticket_stale_status(ticket, project)
        if stale is True:
            stale_tickets.append({
                "id": str(ticket.id),
                "title": ticket.title,
                "base_leaf_id": ticket.base_leaf_id,
                "stale_reason": stale_reason,
            })

    attempts_missing_lineage = []
    for attempt in attempts:
        base_leaf_id = _normalize_frontier_id(getattr(attempt, "base_hash", None))
        if base_leaf_id is None:
            attempts_missing_lineage.append({
                "id": str(attempt.id),
                "ticket_id": str(attempt.ticket_id),
                "attempt_num": attempt.attempt_num,
                "base_leaf_id": None,
                "parent_leaf_id": None,
            })

    return {
        "project_id": str(project.id),
        "accepted_frontier_id": accepted_frontier_id,
        "has_accepted_frontier": accepted_frontier_id is not None,
        "local_path": project_local_path_metadata(project),
        "ticket_counts": {
            "total": len(tickets),
            "missing_base_leaf_id": len(tickets_missing_base_leaf_ids),
            "stale": len(stale_tickets),
        },
        "attempt_counts": {
            "total": len(attempts),
            "missing_base_hash": len(attempts_missing_lineage),
            "missing_parent_leaf_id": len(attempts_missing_lineage),
        },
        "tickets_missing_base_leaf_ids": tickets_missing_base_leaf_ids,
        "stale_tickets": stale_tickets,
        "attempts_missing_lineage": attempts_missing_lineage,
    }


def set_project_accepted_frontier(project: Project, frontier_id) -> str:
    normalized = _normalize_frontier_id(frontier_id)
    valid, error = _validate_project_frontier_candidate(project, normalized)
    if not valid:
        raise ValueError(error or "accepted_frontier_id is invalid")
    project.accepted_frontier_id = normalized
    db.session.commit()
    return normalized


def backfill_ticket_base_leaf_ids(project: Project, *, dry_run: bool) -> dict:
    accepted_frontier_id = _get_project_frontier_id(project)
    if accepted_frontier_id is None:
        raise ValueError("project.accepted_frontier_id is required for ticket base backfill")

    tickets = (
        Ticket.query
        .filter_by(project_id=project.id)
        .order_by(Ticket.created_at.asc(), Ticket.id.asc())
        .all()
    )
    updates = []
    for ticket in tickets:
        if _normalize_frontier_id(getattr(ticket, "base_leaf_id", None)) is not None:
            continue
        updates.append({
            "id": str(ticket.id),
            "base_leaf_id": accepted_frontier_id,
        })
        if not dry_run:
            ticket.base_leaf_id = accepted_frontier_id

    if not dry_run and updates:
        db.session.commit()

    return {
        "project_id": str(project.id),
        "accepted_frontier_id": accepted_frontier_id,
        "dry_run": bool(dry_run),
        "updated_count": len(updates),
        "tickets_to_update": updates,
    }
