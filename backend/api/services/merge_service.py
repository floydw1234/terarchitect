"""Ship run and wave-computation helpers (swarm mode)."""
from flask import current_app

from models.db import db, Project, Ticket, ShipRun, TicketAttempt
from .attempt_service import SATISFIED_STATUSES as _SATISFIED_STATUSES


# MVP docs speak in terms of queued/composing/ready_to_ship/shipping/shipped.
# The live code still accepts `running` and `compose_failed` callbacks for
# compatibility with the older ship worker flow.
ACTIVE_SHIP_RUN_STATUSES = ("queued", "composing", "running", "ready_to_ship", "shipping")
TERMINAL_SHIP_RUN_STATUSES = ("shipped",)


def lock_project_for_update(project_id):
    """Lock the project row while mutating ship-run/frontier state.

    PostgreSQL enforces the row lock. SQLite ignores FOR UPDATE, which is fine
    for the focused test app while keeping the production transaction shape.
    """
    return Project.query.filter_by(id=project_id).with_for_update().first()


def _has_accepted_attempt(ticket_id) -> bool:
    return TicketAttempt.query.filter_by(ticket_id=ticket_id).filter(
        TicketAttempt.status.in_(_SATISFIED_STATUSES)
    ).first() is not None


def compute_waves(tickets: list) -> dict:
    """BFS topological layering over depends_on_ticket_ids.
    Returns {ticket_id_str: wave_num}.  Wave 0 = no dependencies.
    Handles cycles and unknown dep refs gracefully (assigns wave 0).
    """
    id_to_deps: dict = {
        str(t.id): set(str(d) for d in (t.depends_on_ticket_ids or []))
        for t in tickets
    }
    known_ids = set(id_to_deps.keys())
    waves: dict = {}
    changed = True
    while changed:
        changed = False
        for tid, deps in id_to_deps.items():
            if tid in waves:
                continue
            local_deps = deps & known_ids
            if any(d not in waves for d in local_deps):
                continue
            w = (max(waves[d] for d in local_deps) + 1) if local_deps else 0
            waves[tid] = w
            changed = True
    for tid in id_to_deps:
        waves.setdefault(tid, 0)
    return waves


def maybe_trigger_wave_merge(project_id, completed_ticket_id) -> None:
    """Called after a swarm ticket reaches `done`. If every ticket in that
    wave has an accepted attempt AND no ship run exists yet, enqueue one."""
    project = lock_project_for_update(project_id)
    if not project:
        return

    tickets = Ticket.query.filter_by(project_id=project_id).all()
    if not tickets:
        return
    waves = compute_waves(tickets)
    my_wave = waves.get(str(completed_ticket_id), 0)

    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == my_wave]
    if not all(_has_accepted_attempt(t.id) for t in wave_tickets):
        return

    existing = ShipRun.query.filter_by(
        project_id=project_id, wave_num=my_wave,
    ).filter(ShipRun.status.in_(ACTIVE_SHIP_RUN_STATUSES + TERMINAL_SHIP_RUN_STATUSES)).first()
    if existing:
        return

    run = ShipRun(project_id=str(project_id), wave_num=my_wave, status="queued")
    db.session.add(run)
    db.session.commit()
    current_app.logger.info(
        "Wave %d complete for project %s — ship run %s queued",
        my_wave, project_id, run.id,
    )


def ship_run_to_json(run: ShipRun) -> dict:
    return {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "wave_num": run.wave_num,
        "status": run.status,
        "error": run.error,
        "release_branch": run.release_branch,
        "base_main_hash": run.base_main_hash,
        "composed_commit_hash": run.composed_commit_hash,
        "changed_files": run.changed_files or [],
        "summary": run.summary,
        "test_status": run.test_status,
        "test_output": run.test_output,
        "release_pr_url": run.release_pr_url,
        "release_pr_number": run.release_pr_number,
        "shipped_at": run.shipped_at.isoformat() if run.shipped_at else None,
        "shipped_commit_hash": run.shipped_commit_hash,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }
