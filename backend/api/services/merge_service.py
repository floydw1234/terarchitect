"""Merge-run and wave-computation helpers (swarm mode)."""
from flask import current_app

from models.db import db, Ticket, MergeRun


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
            # Only wait on deps that exist in this project; ignore unknown refs
            local_deps = deps & known_ids
            if any(d not in waves for d in local_deps):
                continue
            w = (max(waves[d] for d in local_deps) + 1) if local_deps else 0
            waves[tid] = w
            changed = True
    # Fallback: circular or unresolved → wave 0
    for tid in id_to_deps:
        waves.setdefault(tid, 0)
    return waves


def maybe_trigger_wave_merge(project_id, completed_ticket_id) -> None:
    """Called after a swarm ticket reaches `done`.  If every ticket in that
    wave is done AND no merge run exists yet for the wave, enqueue one."""
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    if not tickets:
        return
    waves = compute_waves(tickets)
    my_wave = waves.get(str(completed_ticket_id), 0)

    # All tickets in this wave must be done
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == my_wave]
    if not all(t.column_id == "done" for t in wave_tickets):
        return

    # Don't double-trigger
    existing = MergeRun.query.filter_by(
        project_id=project_id, wave_num=my_wave,
    ).filter(MergeRun.status.in_(["queued", "running", "done"])).first()
    if existing:
        return

    run = MergeRun(project_id=str(project_id), wave_num=my_wave, status="queued")
    db.session.add(run)
    db.session.commit()
    current_app.logger.info(
        "Wave %d complete for project %s — merge run %s queued",
        my_wave, project_id, run.id,
    )


def merge_run_to_json(run: MergeRun) -> dict:
    return {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "wave_num": run.wave_num,
        "status": run.status,
        "commit_hash": run.commit_hash,
        "pr_url": run.pr_url,
        "error": run.error,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
    }
