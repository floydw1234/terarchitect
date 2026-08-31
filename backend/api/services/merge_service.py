"""Ship run and promotion-candidate helpers (swarm mode)."""
from collections import defaultdict

from models.db import db, Project, PromotionCandidate, Ticket, ShipRun, TicketAttempt
from .attempt_service import (
    SATISFIED_STATUSES as _SATISFIED_STATUSES,
    attempt_is_integrated as _attempt_is_integrated,
    attempt_satisfies_dependencies as _attempt_satisfies_dependencies,
)


# MVP docs speak in terms of queued/composing/ready_to_ship/shipping/shipped.
# The live code still accepts `running` and `compose_failed` callbacks for
# compatibility with the older ship worker flow.
ACTIVE_SHIP_RUN_STATUSES = ("queued", "composing", "running", "ready_to_ship", "shipping")
TERMINAL_SHIP_RUN_STATUSES = ("shipped",)


PROMOTION_CANDIDATE_TERMINAL_STATUSES = ("shipped", "superseded")


def lock_project_for_update(project_id):
    """Lock the project row while mutating ship-run/frontier state.

    PostgreSQL enforces the row lock. SQLite ignores FOR UPDATE, which is fine
    for the focused test app while keeping the production transaction shape.
    """
    return Project.query.filter_by(id=project_id).with_for_update().first()

def topological_ticket_rank(tickets: list) -> dict:
    """BFS topological rank over depends_on_ticket_ids.

    Returns {ticket_id_str: rank}. Rank 0 means no in-set dependencies.
    Cycles and unknown dependency refs get rank 0 so callers can still order
    independent work without blocking the rest of the graph.
    """
    id_to_deps: dict = {
        str(t.id): set(str(d) for d in (t.depends_on_ticket_ids or []))
        for t in tickets
    }
    known_ids = set(id_to_deps.keys())
    ranks: dict = {}
    changed = True
    while changed:
        changed = False
        for tid, deps in id_to_deps.items():
            if tid in ranks:
                continue
            local_deps = deps & known_ids
            if any(d not in ranks for d in local_deps):
                continue
            rank = (max(ranks[d] for d in local_deps) + 1) if local_deps else 0
            ranks[tid] = rank
            changed = True
    for tid in id_to_deps:
        ranks.setdefault(tid, 0)
    return ranks


def analyze_promotion_candidate_graph(
    *,
    frontier: str | None,
    tickets: list,
    selected_attempts: list,
    accepted_attempts_by_ticket_id: dict[str, TicketAttempt],
) -> dict:
    """Resolve integrated-winner dependency closure and validate a candidate set."""
    ticket_by_id = {str(ticket.id): ticket for ticket in tickets}
    included_attempts_by_ticket_id: dict[str, TicketAttempt] = {}
    dependency_ticket_ids_by_ticket: dict[str, list[str]] = {}
    auto_included_attempt_ids: list[str] = []
    blockers: list[str] = []
    unknown_dependency_refs: list[dict] = []
    queue: list[str] = []

    for attempt in selected_attempts:
        ticket_id = str(attempt.ticket_id)
        if ticket_id not in included_attempts_by_ticket_id:
            included_attempts_by_ticket_id[ticket_id] = attempt
            queue.append(ticket_id)

    while queue:
        ticket_id = queue.pop(0)
        ticket = ticket_by_id.get(ticket_id)
        if not ticket:
            blockers.append(f"Included attempt references unknown ticket {ticket_id}.")
            continue
        dep_ids = [str(dep_id) for dep_id in (ticket.depends_on_ticket_ids or [])]
        dependency_ticket_ids_by_ticket[ticket_id] = dep_ids
        unknown_ids = sorted(dep_id for dep_id in dep_ids if dep_id not in ticket_by_id)
        if unknown_ids:
            unknown_dependency_refs.append({
                "ticket_id": ticket_id,
                "unknown_dependency_ids": unknown_ids,
            })
            blockers.append(
                f"Ticket '{ticket.title[:40]}' depends on unknown ticket ids: {', '.join(unknown_ids)}."
            )
        for dep_id in dep_ids:
            if dep_id not in ticket_by_id:
                continue
            dep_attempt = accepted_attempts_by_ticket_id.get(dep_id)
            dep_ticket = ticket_by_id.get(dep_id)
            if dep_attempt is None:
                dep_title = dep_ticket.title[:40] if dep_ticket else dep_id
                blockers.append(
                    f"Ticket '{ticket.title[:40]}' depends on '{dep_title}' with no integrated winner attempt."
                )
                continue
            if dep_attempt.status == "shipped":
                continue
            if dep_id not in included_attempts_by_ticket_id:
                included_attempts_by_ticket_id[dep_id] = dep_attempt
                auto_included_attempt_ids.append(str(dep_attempt.id))
                queue.append(dep_id)

    included_ticket_ids = set(included_attempts_by_ticket_id.keys())
    cycles: list[list[str]] = []
    cycle_keys: set[tuple[str, ...]] = set()
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
        cycles.append(list(key) + [key[0]])

    def _visit(ticket_id: str) -> None:
        state[ticket_id] = 1
        stack_index[ticket_id] = len(stack)
        stack.append(ticket_id)
        for dep_id in dependency_ticket_ids_by_ticket.get(ticket_id, []):
            if dep_id not in included_ticket_ids:
                continue
            dep_state = state.get(dep_id, 0)
            if dep_state == 0:
                _visit(dep_id)
            elif dep_state == 1:
                start = stack_index.get(dep_id, 0)
                _record_cycle(stack[start:] + [dep_id])
        stack.pop()
        stack_index.pop(ticket_id, None)
        state[ticket_id] = 2

    for ticket_id in sorted(included_ticket_ids):
        if state.get(ticket_id, 0) == 0:
            _visit(ticket_id)

    for cycle in cycles:
        blockers.append("Dependency cycle detected: " + " -> ".join(cycle))

    included_attempts = [
        included_attempts_by_ticket_id[ticket_id]
        for ticket_id in sorted(included_ticket_ids)
    ]
    included_hashes = {
        attempt.agenthub_commit_hash
        for attempt in included_attempts
        if attempt.agenthub_commit_hash
    }
    referenced_dependency_ticket_ids = {
        dep_id
        for dep_ids in dependency_ticket_ids_by_ticket.values()
        for dep_id in dep_ids
        if dep_id in included_ticket_ids
    }
    leaf_attempts = [
        included_attempts_by_ticket_id[ticket_id]
        for ticket_id in sorted(included_ticket_ids)
        if ticket_id not in referenced_dependency_ticket_ids
    ]

    for ticket_id in sorted(included_ticket_ids):
        ticket = ticket_by_id.get(ticket_id)
        attempt = included_attempts_by_ticket_id[ticket_id]
        dep_ids = dependency_ticket_ids_by_ticket.get(ticket_id, [])
        included_dep_ids = [
            dep_id
            for dep_id in dep_ids
            if dep_id in included_ticket_ids
        ]

        if len(included_dep_ids) > 1:
            dep_titles = [
                str(getattr(ticket_by_id[dep_id], "title", dep_id))[:40]
                for dep_id in included_dep_ids
                if dep_id in ticket_by_id
            ]
            blockers.append(
                f"Ticket '{ticket.title[:40]}' has ambiguous multi-parent ancestry via "
                + ", ".join(dep_titles or included_dep_ids)
                + "."
            )

        if not attempt.agenthub_commit_hash:
            blockers.append(f"Ticket '{ticket.title[:40]}' has an integrated winner attempt with no commit hash.")

        allowed_bases = set(filter(None, [frontier])) | included_hashes
        if attempt.base_hash:
            if allowed_bases and attempt.base_hash not in allowed_bases:
                # Preserve the legacy no-frontier path for independent root attempts.
                # Candidate-backed promotion should still validate dependency ancestry,
                # but projects without a recorded shipped frontier may legitimately
                # carry a historical base hash on the first accepted leaf.
                if frontier is None and not included_dep_ids:
                    pass
                else:
                    blockers.append(
                        f"Ticket '{ticket.title[:40]}' attempt base {attempt.base_hash[:12]} "
                        "is not the current frontier or another included integrated winner attempt."
                    )
        elif frontier:
            blockers.append(f"Ticket '{ticket.title[:40]}' has no base hash for frontier validation.")

    deduped_blockers: list[str] = []
    for blocker in blockers:
        if blocker and blocker not in deduped_blockers:
            deduped_blockers.append(blocker)

    selected_attempt_ids = [str(attempt.id) for attempt in included_attempts]
    selected_leaf_hashes = [
        attempt.agenthub_commit_hash
        for attempt in leaf_attempts
        if attempt.agenthub_commit_hash
    ]
    validation_summary = {
        "seed_attempt_ids": [str(attempt.id) for attempt in selected_attempts],
        "included_ticket_ids": [str(ticket_id) for ticket_id in sorted(included_ticket_ids)],
        "auto_included_dependency_attempt_ids": auto_included_attempt_ids,
        "selected_leaf_ticket_ids": [str(attempt.ticket_id) for attempt in leaf_attempts],
        "selected_leaf_hashes": selected_leaf_hashes,
        "unknown_dependency_refs": unknown_dependency_refs,
        "dependency_cycles": cycles,
        "blockers": deduped_blockers,
        "included_attempt_count": len(selected_attempt_ids),
    }
    return {
        "selected_attempt_ids": selected_attempt_ids,
        "selected_leaf_hashes": selected_leaf_hashes,
        "base_root_hash": frontier,
        "status": "blocked" if deduped_blockers else "valid",
        "validation_summary": validation_summary,
        "conflict_summary": "\n".join(deduped_blockers) if deduped_blockers else None,
    }


def build_promotion_candidate_snapshot(project, selected_attempt_ids: list[str]) -> dict:
    """Resolve selected attempt ids into a stable promotion candidate snapshot."""
    deduped_attempt_ids: list[str] = []
    for attempt_id in selected_attempt_ids or []:
        attempt_id_str = str(attempt_id)
        if attempt_id_str not in deduped_attempt_ids:
            deduped_attempt_ids.append(attempt_id_str)

    accepted_attempts = [
        attempt
        for attempt in (
            TicketAttempt.query
            .filter_by(project_id=project.id)
            .order_by(TicketAttempt.ticket_id.asc(), TicketAttempt.attempt_num.desc())
            .all()
        )
        if _attempt_satisfies_dependencies(attempt)
    ]
    accepted_attempts_by_ticket_id: dict[str, TicketAttempt] = {}
    for attempt in accepted_attempts:
        ticket_id = str(attempt.ticket_id)
        accepted_attempts_by_ticket_id.setdefault(ticket_id, attempt)

    selected_attempts: list[TicketAttempt] = []
    blockers: list[str] = []
    attempts_by_id = {str(attempt.id): attempt for attempt in accepted_attempts}
    for attempt_id in deduped_attempt_ids:
        attempt = attempts_by_id.get(attempt_id)
        if attempt is None:
            blockers.append(
                f"Attempt {attempt_id} is not a winning integrated attempt in this project."
            )
            continue
        selected_attempts.append(attempt)

    analysis = analyze_promotion_candidate_graph(
        frontier=getattr(project, "shipped_frontier", None) or None,
        tickets=Ticket.query.filter_by(project_id=project.id).all(),
        selected_attempts=selected_attempts,
        accepted_attempts_by_ticket_id=accepted_attempts_by_ticket_id,
    )
    if not deduped_attempt_ids:
        blockers.append("selected_attempt_ids must include at least one winning integrated attempt.")
    merged_blockers = analysis["validation_summary"].get("blockers", []) + blockers
    deduped_blockers: list[str] = []
    for blocker in merged_blockers:
        if blocker and blocker not in deduped_blockers:
            deduped_blockers.append(blocker)
    analysis["validation_summary"]["requested_attempt_ids"] = deduped_attempt_ids
    analysis["validation_summary"]["blockers"] = deduped_blockers
    analysis["status"] = "blocked" if deduped_blockers else analysis["status"]
    analysis["conflict_summary"] = "\n".join(deduped_blockers) if deduped_blockers else None
    return analysis


def promotion_candidate_to_json(candidate: PromotionCandidate, *, include_attempts: bool = False) -> dict:
    payload = {
        "id": str(candidate.id),
        "project_id": str(candidate.project_id),
        "selected_attempt_ids": list(candidate.selected_attempt_ids or []),
        "selected_leaf_hashes": list(candidate.selected_leaf_hashes or []),
        "base_root_hash": candidate.base_root_hash,
        "status": candidate.status,
        "validation_summary": candidate.validation_summary or {},
        "conflict_summary": candidate.conflict_summary,
        "composed_commit_hash": candidate.composed_commit_hash,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "updated_at": candidate.updated_at.isoformat() if candidate.updated_at else None,
    }
    if include_attempts:
        payload["attempts"] = [
            {
                "id": str(attempt.id),
                "ticket_id": str(attempt.ticket_id),
                "status": attempt.status,
                "agenthub_commit_hash": attempt.agenthub_commit_hash,
                "base_hash": attempt.base_hash,
                "attempt_num": attempt.attempt_num,
            }
            for attempt in (
                TicketAttempt.query
                .filter(TicketAttempt.id.in_(candidate.selected_attempt_ids or []))
                .order_by(TicketAttempt.created_at.asc(), TicketAttempt.attempt_num.asc())
                .all()
            )
        ]
    return payload


def _candidate_attempts_by_id(candidate: PromotionCandidate) -> dict[str, TicketAttempt]:
    attempts = (
        TicketAttempt.query
        .filter(TicketAttempt.id.in_(candidate.selected_attempt_ids or []))
        .order_by(TicketAttempt.created_at.asc(), TicketAttempt.attempt_num.asc())
        .all()
    )
    return {str(attempt.id): attempt for attempt in attempts}


def candidate_attempts(candidate: PromotionCandidate) -> list[TicketAttempt]:
    attempts_by_id = _candidate_attempts_by_id(candidate)
    ordered_attempts: list[TicketAttempt] = []
    for attempt_id in candidate.selected_attempt_ids or []:
        attempt = attempts_by_id.get(str(attempt_id))
        if attempt is not None:
            ordered_attempts.append(attempt)
    return ordered_attempts


def candidate_commit_hashes(candidate: PromotionCandidate) -> list[str]:
    hashes = [h for h in (candidate.selected_leaf_hashes or []) if h]
    if hashes:
        return hashes
    return [
        attempt.agenthub_commit_hash
        for attempt in candidate_attempts(candidate)
        if attempt.agenthub_commit_hash
    ]


def validate_promotion_candidate(candidate: PromotionCandidate, project: Project) -> list[str]:
    attempts = candidate_attempts(candidate)
    candidate_attempts_by_ticket_id = {
        str(attempt.ticket_id): attempt
        for attempt in attempts
    }
    shipped_attempts = (
        TicketAttempt.query
        .filter_by(project_id=project.id, status="shipped")
        .order_by(TicketAttempt.ticket_id.asc(), TicketAttempt.attempt_num.desc())
        .all()
    )
    accepted_attempts_by_ticket_id = {
        ticket_id: attempt
        for ticket_id, attempt in candidate_attempts_by_ticket_id.items()
        if _attempt_satisfies_dependencies(attempt)
    }
    for attempt in shipped_attempts:
        accepted_attempts_by_ticket_id.setdefault(str(attempt.ticket_id), attempt)

    analysis = analyze_promotion_candidate_graph(
        frontier=getattr(project, "shipped_frontier", None) or None,
        tickets=Ticket.query.filter_by(project_id=project.id).all(),
        selected_attempts=attempts,
        accepted_attempts_by_ticket_id=accepted_attempts_by_ticket_id,
    )
    errors = list(analysis["validation_summary"].get("blockers", []))
    if not attempts:
        errors.append("Candidate has no selected attempts.")
    missing_attempt_ids = [
        str(attempt_id)
        for attempt_id in (candidate.selected_attempt_ids or [])
        if str(attempt_id) not in {str(attempt.id) for attempt in attempts}
    ]
    if missing_attempt_ids:
        errors.append(
            "Candidate references missing attempts: " + ", ".join(missing_attempt_ids)
        )
    deduped: list[str] = []
    for error in errors:
        if error and error not in deduped:
            deduped.append(error)
    return deduped


def ship_run_to_json(run: ShipRun) -> dict:
    return {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "promotion_candidate_id": str(run.promotion_candidate_id) if run.promotion_candidate_id else None,
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
