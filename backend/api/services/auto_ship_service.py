"""Automatic shipping when a ticket's attempt batch finishes validation."""

from __future__ import annotations

from flask import current_app

from models.db import db, Project, Ticket, TicketAttempt
from .attempt_service import get_accepted_attempt as _get_accepted_attempt
from .merge_service import lock_project_for_update as _lock_project_for_update
from .project_service import get_project_auto_ship as _get_project_auto_ship
from .promotion_spine_service import (
    PromotionSpineError,
    pick_auto_winner_attempt,
    run_promotion_spine_for_attempt,
    ticket_execution_batch_settled,
)
from .ticket_service import dispatch_unblocked_queued as _dispatch_unblocked_queued


def maybe_auto_ship_after_validation(project_id, ticket_id, attempt_id: str | None = None) -> dict | None:
    """Run auto-ship when enabled and the ticket's jobs have all settled."""
    project = _lock_project_for_update(project_id)
    if project is None:
        return None
    if not _get_project_auto_ship(project):
        return None

    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None or str(ticket.project_id) != str(project_id):
        return None

    if not ticket_execution_batch_settled(str(ticket.id)):
        return None

    if _get_accepted_attempt(ticket.id) is not None:
        return None

    attempt = None
    if attempt_id:
        attempt = TicketAttempt.query.filter_by(
            project_id=project_id,
            ticket_id=ticket.id,
            id=attempt_id,
        ).first()
    if attempt is None:
        attempt = pick_auto_winner_attempt(project, ticket)
    if attempt is None:
        current_app.logger.info(
            "auto_ship skipped project=%s ticket=%s: no eligible validated attempt",
            project_id,
            ticket_id,
        )
        return None

    try:
        result = run_promotion_spine_for_attempt(
            project,
            ticket,
            attempt,
            sync_compose=True,
            ship_after_compose=True,
        )
        current_app.logger.info(
            "auto_ship completed project=%s ticket=%s run=%s shipped=%s",
            project_id,
            ticket_id,
            result.get("ship_run_id"),
            result.get("shipped"),
        )
        try:
            _dispatch_unblocked_queued(project_id)
        except Exception as exc:
            current_app.logger.warning("auto_ship dispatch queued failed: %s", exc)
        return result
    except PromotionSpineError as exc:
        current_app.logger.warning(
            "auto_ship failed project=%s ticket=%s: %s",
            project_id,
            ticket_id,
            exc,
        )
        return {
            "error": str(exc),
            "status_code": exc.status_code,
            "detail": exc.detail,
            "attempt_id": str(attempt.id),
        }
