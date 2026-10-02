"""Revert shipped_frontier to a prior ShipRun base without deleting commits."""

from __future__ import annotations

from datetime import datetime, timezone

from models.db import db, Project, ShipRun
from .channel_service import event_content as _event_content, post_event as _post_event, ship_run_channel as _ship_run_channel
from .merge_service import lock_project_for_update as _lock_project_for_update
from .ticket_service import dispatch_unblocked_queued as _dispatch_unblocked_queued


class ShipRevertError(Exception):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def revert_shipped_frontier(project_id, *, to_ship_run_id: str | None = None) -> dict:
    project = _lock_project_for_update(project_id)
    if project is None:
        raise ShipRevertError("Project not found", status_code=404)

    current_frontier = (getattr(project, "shipped_frontier", None) or "").strip() or None
    shipped_runs = (
        ShipRun.query.filter_by(project_id=project_id, status="shipped")
        .order_by(ShipRun.created_at.desc())
        .all()
    )
    if not shipped_runs:
        raise ShipRevertError("No shipped ShipRuns to revert from.")

    if to_ship_run_id:
        anchor = next((run for run in shipped_runs if str(run.id) == str(to_ship_run_id)), None)
        if anchor is None:
            raise ShipRevertError(f"ShipRun {to_ship_run_id} is not a shipped run for this project.")
        target_frontier = (anchor.base_main_hash or "").strip() or None
        if not target_frontier:
            raise ShipRevertError("Target ShipRun has no base_main_hash to revert to.")
    else:
        latest = shipped_runs[0]
        target_frontier = (latest.base_main_hash or "").strip() or None
        if not target_frontier:
            raise ShipRevertError("Latest shipped run has no base_main_hash to revert to.")

    if current_frontier and current_frontier == target_frontier:
        raise ShipRevertError("shipped_frontier already matches the revert target.")

    now = datetime.now(timezone.utc)
    revert_run = ShipRun(
        project_id=str(project_id),
        promotion_candidate_id=None,
        status="shipped",
        base_main_hash=current_frontier,
        composed_commit_hash=target_frontier,
        shipped_commit_hash=target_frontier,
        shipped_at=now,
        summary=f"revert shipped_frontier to {target_frontier[:12]}",
    )
    db.session.add(revert_run)
    db.session.flush()

    project.shipped_frontier = target_frontier
    project.shipped_frontier_updated_at = now
    project.accepted_frontier_id = target_frontier
    db.session.commit()

    ship_ch = _ship_run_channel(project.name, str(revert_run.id))
    _post_event(
        ship_ch,
        _event_content(
            "frontier_reverted",
            f"Reverted shipped_frontier to {target_frontier[:12]}",
            {
                "ship_run_id": str(revert_run.id),
                "from_frontier": current_frontier,
                "to_frontier": target_frontier,
                "anchor_ship_run_id": to_ship_run_id,
            },
        ),
    )

    try:
        _dispatch_unblocked_queued(project_id)
    except Exception:
        pass

    return {
        "project_id": str(project_id),
        "ship_run_id": str(revert_run.id),
        "status": "shipped",
        "shipped_frontier_before": current_frontier,
        "shipped_frontier_after": target_frontier,
        "shipped_commit_hash": target_frontier,
        "revert": True,
    }
