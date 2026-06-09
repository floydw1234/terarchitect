"""Ship run and wave-computation helpers (swarm mode)."""
from collections import defaultdict

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


def analyze_wave_dependencies(tickets: list) -> dict:
    """Return agent-friendly dependency analysis while preserving legacy wave math.

    The returned structure keeps ``compute_waves`` semantics for compatibility, but
    also surfaces unknown references and dependency cycles so callers can explain
    why a ticket landed in a given wave or why it is unsafe to compose/ship.
    """
    id_to_ticket = {str(t.id): t for t in tickets}
    id_to_deps = {
        str(t.id): [str(dep_id) for dep_id in (t.depends_on_ticket_ids or [])]
        for t in tickets
    }
    waves = compute_waves(tickets)
    known_ids = set(id_to_ticket.keys())

    unknown_refs_by_ticket: dict[str, list[str]] = {}
    for tid, deps in id_to_deps.items():
        unknown = sorted({dep_id for dep_id in deps if dep_id not in known_ids})
        if unknown:
            unknown_refs_by_ticket[tid] = unknown

    cycles: list[list[str]] = []
    cycle_keys: set[tuple[str, ...]] = set()
    cycle_members_by_ticket: dict[str, list[list[str]]] = defaultdict(list)
    state: dict[str, int] = {}
    stack: list[str] = []
    stack_index: dict[str, int] = {}

    def _record_cycle(path: list[str]) -> None:
        base_nodes = path[:-1] if len(path) > 1 and path[0] == path[-1] else path
        if not base_nodes:
            return
        rotations = [tuple(base_nodes[i:] + base_nodes[:i]) for i in range(len(base_nodes))]
        key = min(rotations)
        if key in cycle_keys:
            return
        cycle_keys.add(key)
        cycle_path = list(key) + [key[0]]
        cycles.append(cycle_path)
        for node in key:
            cycle_members_by_ticket[node].append(cycle_path)

    def _visit(node_id: str) -> None:
        state[node_id] = 1
        stack_index[node_id] = len(stack)
        stack.append(node_id)
        for dep_id in id_to_deps.get(node_id, []):
            if dep_id not in known_ids:
                continue
            dep_state = state.get(dep_id, 0)
            if dep_state == 0:
                _visit(dep_id)
            elif dep_state == 1:
                start = stack_index.get(dep_id, 0)
                _record_cycle(stack[start:] + [dep_id])
        stack.pop()
        stack_index.pop(node_id, None)
        state[node_id] = 2

    for ticket_id in id_to_ticket:
        if state.get(ticket_id, 0) == 0:
            _visit(ticket_id)

    ticket_explanations: dict[str, dict] = {}
    for ticket_id, ticket in id_to_ticket.items():
        dep_ids = id_to_deps.get(ticket_id, [])
        blockers: list[str] = []
        cycle_paths = cycle_members_by_ticket.get(ticket_id, [])
        unknown_refs = unknown_refs_by_ticket.get(ticket_id, [])

        if unknown_refs:
            blockers.append(
                "Unknown dependency references: " + ", ".join(unknown_refs)
            )
        if cycle_paths:
            blockers.extend(
                f"Dependency cycle detected: {' -> '.join(path)}"
                for path in cycle_paths
            )

        if not dep_ids:
            dependency_reason = "No dependencies."
        else:
            known_dep_bits = []
            for dep_id in dep_ids:
                dep = id_to_ticket.get(dep_id)
                if not dep:
                    continue
                known_dep_bits.append(f"{dep_id} (wave {waves.get(dep_id, 0)})")
            reasons = []
            if known_dep_bits:
                reasons.append("Depends on " + ", ".join(known_dep_bits) + ".")
            if unknown_refs:
                reasons.append("Unknown refs: " + ", ".join(unknown_refs) + ".")
            if cycle_paths:
                reasons.append(
                    "Cycle(s): " + "; ".join(" -> ".join(path) for path in cycle_paths) + "."
                )
            dependency_reason = " ".join(reasons) if reasons else "Dependencies recorded."

        ticket_explanations[ticket_id] = {
            "ticket_id": ticket_id,
            "wave_num": waves.get(ticket_id, 0),
            "depends_on_ticket_ids": dep_ids,
            "unknown_dependency_ids": unknown_refs,
            "dependency_cycles": cycle_paths,
            "has_unknown_dependencies": bool(unknown_refs),
            "in_dependency_cycle": bool(cycle_paths),
            "dependency_reason": dependency_reason,
            "blockers": blockers,
        }

    return {
        "waves": waves,
        "ticket_explanations": ticket_explanations,
        "unknown_dependency_refs": [
            {"ticket_id": tid, "unknown_dependency_ids": refs}
            for tid, refs in sorted(unknown_refs_by_ticket.items())
        ],
        "dependency_cycles": cycles,
    }


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
