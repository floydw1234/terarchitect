"""Automatic shipping when a ticket's attempt batch finishes validation."""

from __future__ import annotations

from flask import current_app

from models.db import db, Project, Ticket, TicketAttempt
from .attempt_service import get_accepted_attempt as _get_accepted_attempt
from .merge_service import lock_project_for_update as _lock_project_for_update
from .project_service import get_project_auto_ship as _get_project_auto_ship
from .auto_ship_judge_service import pick_auto_winner_with_decision
from models.db import ShipRun
from .project_service import get_project_ship_target as _get_project_ship_target
from .promotion_spine_service import (
    PromotionSpineError,
    _record_ship_run_auto_ship_failure,
    run_promotion_spine_for_attempt,
    ship_ready_to_ship_run,
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

    # ``attempt_id`` is only the attempt whose validation triggered this hook (usually the
    # last one to finish). It must not short-circuit winner selection, otherwise the
    # judge never runs and the last finisher always wins.
    attempt, winner_decision = pick_auto_winner_with_decision(project, ticket)
    if attempt is None:
        current_app.logger.info(
            "auto_ship skipped project=%s ticket=%s: no eligible validated attempt",
            project_id,
            ticket_id,
        )
        return None

    if winner_decision:
        ticket.auto_ship_winner_decision = winner_decision
        db.session.commit()

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
        if winner_decision:
            result = dict(result)
            result["winner_pick"] = winner_decision
        return result
    except PromotionSpineError as exc:
        current_app.logger.warning(
            "auto_ship failed project=%s ticket=%s: %s",
            project_id,
            ticket_id,
            exc,
        )
        payload = {
            "error": str(exc),
            "status_code": exc.status_code,
            "detail": exc.detail,
            "attempt_id": str(attempt.id),
        }
        if winner_decision:
            payload["winner_pick"] = winner_decision
            ticket.auto_ship_winner_decision = {
                **winner_decision,
                "auto_ship_error": str(exc),
                "status_code": exc.status_code,
            }
            db.session.commit()
        return payload


def maybe_finalize_auto_ship_after_compose(run: ShipRun) -> None:
    """Finalize AgentHub auto-ship when a run reaches ready_to_ship (e.g. coordinator compose)."""
    if run is None or run.status != "ready_to_ship":
        return

    project = _lock_project_for_update(run.project_id)
    if project is None or not _get_project_auto_ship(project):
        return
    if _get_project_ship_target(project) != "agenthub":
        return

    db.session.refresh(run)
    if run.status != "ready_to_ship":
        return
    if run.status == "shipped":
        return

    try:
        shipped = ship_ready_to_ship_run(project, str(run.id))
        current_app.logger.info(
            "auto_ship finalized compose project=%s run=%s shipped=%s",
            project.id,
            run.id,
            (shipped or {}).get("status") == "shipped",
        )
        try:
            _dispatch_unblocked_queued(str(project.id))
        except Exception as exc:
            current_app.logger.warning("auto_ship finalize dispatch queued failed: %s", exc)
    except PromotionSpineError as exc:
        current_app.logger.error(
            "auto_ship finalize failed project=%s run=%s: %s",
            project.id,
            run.id,
            exc,
        )
        _record_ship_run_auto_ship_failure(
            str(run.id),
            f"Auto-ship finalize failed after compose: {exc}",
            compose_failed=False,
        )
