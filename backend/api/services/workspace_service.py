"""Composite Workspace service: serialization and compatibility analysis (Phase 9)."""
from models.db import CompositeWorkspace, Project, Ticket, TicketAttempt, db
from .attempt_service import SATISFIED_STATUSES


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def workspace_to_json(ws: CompositeWorkspace, *, include_test_output: bool = False) -> dict:
    out = {
        "id": str(ws.id),
        "project_id": str(ws.project_id),
        "base_root_hash": ws.base_root_hash,
        "selected_attempt_ids": ws.selected_attempt_ids or [],
        "selected_leaf_hashes": ws.selected_leaf_hashes or [],
        "status": ws.status,
        "composed_commit_hash": ws.composed_commit_hash,
        "short_composed_hash": (ws.composed_commit_hash or "")[:12] or None,
        "conflict_summary": ws.conflict_summary,
        "changed_files": ws.changed_files or [],
        "summary": ws.summary,
        "test_status": ws.test_status,
        "preview_url": ws.preview_url,
        "preview_status": ws.preview_status,
        "preview_command": ws.preview_command or [],
        "preview_error": ws.preview_error,
        "created_by": ws.created_by,
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
        "updated_at": ws.updated_at.isoformat() if ws.updated_at else None,
    }
    if include_test_output:
        out["test_output"] = ws.test_output
    return out


# ---------------------------------------------------------------------------
# Compatibility analysis (9.2)
# ---------------------------------------------------------------------------

def analyze_compatibility(project_id: str, attempt_ids: list[str]) -> dict:
    """Analyse a proposed set of attempts for compatibility before composing.

    Returns a report:
    {
      "ok": bool,
      "issues": [{"attempt_id": ..., "level": "error"|"warning", "message": ...}],
      "selected_attempts": [...attempt_to_json-lite...],
      "dep_order": [attempt_id, ...],  # safe merge order
    }
    """
    project = db.session.get(Project, project_id)
    frontier = getattr(project, "shipped_frontier", None) if project else None

    issues = []
    selected = []
    ticket_ids_in_selection: set[str] = set()

    for aid in attempt_ids:
        attempt = db.session.get(TicketAttempt, aid)
        if not attempt:
            issues.append({"attempt_id": aid, "level": "error", "message": "Attempt not found."})
            continue
        if attempt.status not in SATISFIED_STATUSES:
            issues.append({
                "attempt_id": aid,
                "level": "error",
                "message": f"Attempt is not accepted (status: {attempt.status}). Only accepted attempts can be composed.",
            })
        if attempt.validation_error:
            issues.append({
                "attempt_id": aid,
                "level": "error",
                "message": f"Attempt failed validation: {attempt.validation_error}",
            })
        if frontier and attempt.base_hash and attempt.base_hash != frontier:
            issues.append({
                "attempt_id": aid,
                "level": "warning",
                "message": (
                    f"Attempt is stale — built from {attempt.base_hash[:12]}, "
                    f"current frontier is {frontier[:12]}. "
                    "Composition will run a conflict check."
                ),
            })
        if not attempt.agenthub_commit_hash:
            issues.append({
                "attempt_id": aid,
                "level": "error",
                "message": "Attempt has no AgentHub commit hash — cannot compose.",
            })
        ticket_ids_in_selection.add(str(attempt.ticket_id))
        selected.append({
            "attempt_id": str(attempt.id),
            "ticket_id": str(attempt.ticket_id),
            "commit_hash": (attempt.agenthub_commit_hash or "")[:12],
            "base_hash": (attempt.base_hash or "")[:12],
            "wave_num": attempt.wave_num,
            "status": attempt.status,
            "summary": attempt.summary,
            "stale": (attempt.base_hash != frontier) if (frontier and attempt.base_hash) else None,
        })

    # Dependency ordering check
    for aid in attempt_ids:
        attempt = db.session.get(TicketAttempt, aid)
        if not attempt:
            continue
        ticket = db.session.get(Ticket, attempt.ticket_id)
        if not ticket:
            continue
        dep_ids = ticket.depends_on_ticket_ids or []
        for dep_ticket_id in dep_ids:
            if str(dep_ticket_id) not in ticket_ids_in_selection:
                # Dep is not in selection — check if it's shipped
                dep_attempt = (
                    TicketAttempt.query
                    .filter_by(ticket_id=dep_ticket_id, status="shipped")
                    .first()
                )
                if not dep_attempt:
                    dep_ticket = db.session.get(Ticket, dep_ticket_id)
                    dep_title = dep_ticket.title if dep_ticket else str(dep_ticket_id)[:8]
                    issues.append({
                        "attempt_id": aid,
                        "level": "warning",
                        "message": (
                            f"Ticket '{ticket.title[:40]}' depends on '{dep_title[:40]}' "
                            "which is not in the selection and not yet shipped. "
                            "Include its accepted attempt or ship it first."
                        ),
                    })

    # Architecture scope overlap detection
    scope_map: dict[str, list[str]] = {}  # node/edge_id → [attempt_id, ...]
    for s in selected:
        att = db.session.get(TicketAttempt, s["attempt_id"])
        if not att:
            continue
        ticket = db.session.get(Ticket, att.ticket_id)
        if not ticket:
            continue
        for nid in (ticket.associated_node_ids or []):
            if nid == "*":
                continue
            scope_map.setdefault(nid, []).append(s["attempt_id"])
        for eid in (ticket.associated_edge_ids or []):
            if eid == "*":
                continue
            scope_map.setdefault(eid, []).append(s["attempt_id"])
    for scope_id, overlap_attempts in scope_map.items():
        if len(overlap_attempts) > 1:
            issues.append({
                "attempt_id": overlap_attempts[0],
                "level": "warning",
                "message": (
                    f"Architecture scope '{scope_id}' is shared by {len(overlap_attempts)} attempts — "
                    "possible file conflicts. Composition will detect this."
                ),
            })

    # Determine safe merge order (by wave_num, then attempt_num).
    # Pre-build sort key map to avoid N+1 queries inside sorted().
    sort_keys: dict[str, tuple] = {
        s["attempt_id"]: (s["wave_num"], 0)
        for s in selected
    }
    # Fill in attempt_num from DB objects already fetched above
    for s in selected:
        att = db.session.get(TicketAttempt, s["attempt_id"])
        if att:
            sort_keys[s["attempt_id"]] = (att.wave_num, att.attempt_num)
    dep_order = sorted(attempt_ids, key=lambda aid: sort_keys.get(str(aid), (0, 0)))

    errors = [i for i in issues if i["level"] == "error"]
    return {
        "ok": len(errors) == 0,
        "issues": issues,
        "selected_attempts": selected,
        "dep_order": dep_order,
    }
