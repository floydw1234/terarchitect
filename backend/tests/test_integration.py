"""
Backend integration tests (plan 12.2).

Uses Flask test client against in-memory SQLite.

Covers:
  - swarm ticket completion creates TicketAttempt
  - no PR row created for swarm ticket completion
  - ship candidate detail lists accepted attempts
  - compose endpoint rejects invalid dependency subset
  - compose endpoint records conflicts (via worker fail)
  - ship endpoint updates root after PR merge
  - dependency ordering enforced in dispatch
  - intent fields persist and return in ticket response
  - legacy-compatible ship callbacks still report compose_failed/running states
"""
import os
import sys
import uuid
from unittest.mock import patch, MagicMock

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _worker_headers():
    """No auth configured in test — empty headers pass."""
    return {}


def _post_candidate(client, project_id, attempt_ids):
    resp = client.post(
        f"/api/projects/{project_id}/ship/candidates",
        json={"selected_attempt_ids": list(attempt_ids)},
    )
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _compose_ids(client, project_id, attempt_ids):
    candidate = _post_candidate(client, project_id, attempt_ids)
    compose = client.post(
        f"/api/projects/{project_id}/ship/candidates/{candidate['id']}/compose",
        json={},
    )
    return candidate, compose


# ---------------------------------------------------------------------------
# 12.2a  Swarm ticket completion creates TicketAttempt (not PR)
# ---------------------------------------------------------------------------

def test_ticket_complete_creates_attempt(client, project):
    from models.db import TicketAttempt, Ticket, db
    base_leaf_id = "leaf_01HZX3TICKETBASE0123456789ABC"
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="T",
            intent_status="active",
            base_leaf_id=base_leaf_id,
        )
        db.session.add(ticket)
        db.session.commit()
        tid = str(ticket.id)

    resp = client.post(
        f"/api/projects/{project['id']}/tickets/{tid}/complete",
        json={"commit_hash": "a" * 40, "summary": "done"},
    )
    assert resp.status_code == 200

    with client.application.app_context():
        attempts = TicketAttempt.query.filter_by(ticket_id=tid).all()
        assert len(attempts) == 1
        assert attempts[0].agenthub_commit_hash == "a" * 40
        assert attempts[0].base_hash == base_leaf_id
        assert attempts[0].wave_num == 0

    attempts_resp = client.get(f"/api/projects/{project['id']}/tickets/{tid}/attempts")
    assert attempts_resp.status_code == 200
    attempt_payload = attempts_resp.get_json()[0]
    assert attempt_payload["status"] == "validated"
    assert attempt_payload["validated"] is True
    assert attempt_payload["is_winner"] is False
    assert attempt_payload["integrated"] is False
    assert attempt_payload["base_hash"] == base_leaf_id
    assert attempt_payload["base_leaf_id"] == base_leaf_id
    assert attempt_payload["parent_leaf_id"] == base_leaf_id


def test_ticket_complete_requires_ticket_base_leaf_for_swarm_publish(client, project):
    from models.db import Ticket, db
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Missing base leaf",
            intent_status="active",
            base_leaf_id=None,
        )
        db.session.add(ticket)
        db.session.commit()
        tid = str(ticket.id)

    resp = client.post(
        f"/api/projects/{project['id']}/tickets/{tid}/complete",
        json={"commit_hash": "a" * 40, "summary": "done"},
    )
    assert resp.status_code == 409
    assert "base_leaf_id" in resp.get_json()["error"]


def test_ticket_complete_rejects_mismatched_publish_base(client, project):
    from models.db import Ticket, db
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Mismatched base leaf",
            intent_status="active",
            base_leaf_id="leaf_01HZX3TICKETBASE0123456789ABC",
        )
        db.session.add(ticket)
        db.session.commit()
        tid = str(ticket.id)

    resp = client.post(
        f"/api/projects/{project['id']}/tickets/{tid}/complete",
        json={
            "commit_hash": "a" * 40,
            "summary": "done",
            "base_hash": "leaf_01HZX3OTHERBASE0123456789AB",
        },
    )
    assert resp.status_code == 409
    assert "does not match ticket.base_leaf_id" in resp.get_json()["error"]


def test_ticket_complete_creates_no_pr_row(client, project):
    """Swarm completion writes attempts only; no ticket-level PR record is created."""
    from models.db import db, Ticket
    base_leaf_id = "leaf_01HZX3TICKETBASE0123456789ABC"
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="T",
            intent_status="active",
            base_leaf_id=base_leaf_id,
        )
        db.session.add(ticket)
        db.session.commit()
        tid = str(ticket.id)

    resp = client.post(
        f"/api/projects/{project['id']}/tickets/{tid}/complete",
        json={"commit_hash": "b" * 40, "summary": "done"},
    )
    assert resp.status_code == 200

    # PR model was removed in Phase 11 — verify it's gone from the model layer
    import models.db as models_module
    assert not hasattr(models_module, "PR"), "PR model should not exist — removed in Phase 11"


def test_ticket_complete_allows_parallel_attempt_completion_after_first_attempt(client, project):
    from models.db import AgentJob, Ticket, TicketAttempt, db

    frontier_id = project["accepted_frontier_id"]
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Parallel completion ticket",
            intent_status="active",
            base_leaf_id=frontier_id,
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add_all([
            AgentJob(
                ticket_id=ticket.id,
                project_id=project["id"],
                kind="ticket",
                status="running",
            ),
            AgentJob(
                ticket_id=ticket.id,
                project_id=project["id"],
                kind="ticket",
                status="pending",
            ),
        ])
        db.session.commit()
        ticket_id = str(ticket.id)

    first = client.post(
        f"/api/projects/{project['id']}/tickets/{ticket_id}/complete",
        json={
            "commit_hash": "c" * 40,
            "base_hash": frontier_id,
            "agent_id": "parallel-agent-1",
            "summary": "first competing attempt",
        },
    )
    assert first.status_code == 200
    assert first.get_json()["attempt_created"] is True

    second = client.post(
        f"/api/projects/{project['id']}/tickets/{ticket_id}/complete",
        json={
            "commit_hash": "d" * 40,
            "base_hash": frontier_id,
            "agent_id": "parallel-agent-2",
            "summary": "second competing attempt",
        },
    )
    assert second.status_code == 200
    assert second.get_json()["attempt_created"] is True

    with client.application.app_context():
        attempts = (
            TicketAttempt.query
            .filter_by(ticket_id=ticket_id)
            .order_by(TicketAttempt.attempt_num.asc())
            .all()
        )
        assert [attempt.attempt_num for attempt in attempts] == [1, 2]
        assert [attempt.agent_id for attempt in attempts] == [
            "parallel-agent-1",
            "parallel-agent-2",
        ]


# ---------------------------------------------------------------------------
# 12.2b  Candidate detail lists accepted attempts
# ---------------------------------------------------------------------------

def test_ship_candidate_detail_lists_accepted_attempts(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    _ticket_id, attempt_id = accepted_ticket_and_attempt
    candidate = _post_candidate(client, pid, [attempt_id])
    resp = client.get(f"/api/projects/{pid}/ship/candidates/{candidate['id']}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["membership"]["attempts"]) == 1
    assert data["membership"]["attempts"][0]["status"] == "accepted"
    assert data["validation_errors"] == []


def test_ship_candidate_list_shows_candidate(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    _ticket_id, attempt_id = accepted_ticket_and_attempt
    _post_candidate(client, pid, [attempt_id])
    resp = client.get(f"/api/projects/{pid}/ship/candidates")
    assert resp.status_code == 200
    candidates = resp.get_json()
    assert len(candidates) >= 1
    assert candidates[0]["status"] in ("valid", "ready")


def test_ship_candidate_detail_explains_unknown_dependency_refs(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt
    missing_dep = str(uuid.uuid4())

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Broken dependency ticket",
            intent_status="active",
            depends_on_ticket_ids=[missing_dep],
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="d" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    candidate = _post_candidate(client, pid, [attempt_id])
    assert candidate["status"] == "blocked"
    blockers = (candidate.get("validation_summary") or {}).get("blockers") or []
    if not blockers:
        detail = client.get(f"/api/projects/{pid}/ship/candidates/{candidate['id']}").get_json()
        blockers = detail.get("validation_errors") or []
    assert any("unknown" in blocker.lower() for blocker in blockers)


def test_ship_candidate_detail_explains_dependency_cycles(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        a = Ticket(project_id=pid, column_id="done", title="Cycle A", intent_status="active")
        b = Ticket(project_id=pid, column_id="done", title="Cycle B", intent_status="active")
        db.session.add_all([a, b])
        db.session.flush()
        a.depends_on_ticket_ids = [str(b.id)]
        b.depends_on_ticket_ids = [str(a.id)]
        attempt_a = TicketAttempt(
            project_id=pid,
            ticket_id=a.id,
            agenthub_commit_hash="a" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="a",
        )
        attempt_b = TicketAttempt(
            project_id=pid,
            ticket_id=b.id,
            agenthub_commit_hash="b" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="b",
        )
        db.session.add_all([attempt_a, attempt_b])
        db.session.commit()
        attempt_a_id = str(attempt_a.id)

    candidate = _post_candidate(client, pid, [attempt_a_id])
    assert candidate["status"] == "blocked"
    blockers = (candidate.get("validation_summary") or {}).get("blockers") or []
    if not blockers:
        detail = client.get(f"/api/projects/{pid}/ship/candidates/{candidate['id']}").get_json()
        blockers = detail.get("validation_errors") or []
    assert any("cycle" in blocker.lower() for blocker in blockers)


def test_ship_candidate_dry_compose_includes_blockers(client, project):
    pid = project["id"]
    candidate = _post_candidate(client, pid, [])
    assert candidate["status"] == "blocked"
    dry = client.get(f"/api/projects/{pid}/ship/candidates/{candidate['id']}/dry-compose")
    assert dry.status_code == 200
    data = dry.get_json()
    assert data["safe_to_compose"] is False
    assert data["next_actions"]


# ---------------------------------------------------------------------------
# 12.2c  Compose endpoint rejects when no accepted attempts
# ---------------------------------------------------------------------------

def test_compose_rejects_no_accepted_attempts(client, project):
    pid = project["id"]
    candidate = _post_candidate(client, pid, [])
    resp = client.post(
        f"/api/projects/{pid}/ship/candidates/{candidate['id']}/compose",
        json={},
    )
    assert resp.status_code == 409
    assert "Candidate composition validation failed" in resp.get_json().get("error", "")


def test_compose_auto_includes_unshipped_dependency(client, project):
    """Selecting a child without including its unshipped parent still auto-includes the parent."""
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt
    parent_hash = "p" * 40
    child_hash = "c" * 40

    with client.application.app_context():
        parent = Ticket(
            project_id=pid,
            column_id="done",
            title="Parent",
            intent_status="active",
        )
        db.session.add(parent)
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="done",
            title="Child",
            intent_status="active",
            depends_on_ticket_ids=[str(parent.id)],
        )
        db.session.add(child)
        db.session.flush()
        parent_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash=parent_hash,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="parent",
        )
        child_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash=child_hash,
            base_hash=parent_hash,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="child",
        )
        db.session.add_all([parent_attempt, child_attempt])
        db.session.commit()
        child_attempt_id = str(child_attempt.id)
        parent_attempt_id = str(parent_attempt.id)

    candidate = _post_candidate(client, pid, [child_attempt_id])
    assert set(candidate["selected_attempt_ids"]) == {parent_attempt_id, child_attempt_id}
    _, compose = _compose_ids(client, pid, [child_attempt_id])
    assert compose.status_code in (200, 201)


def test_dry_compose_reports_blockers_and_commit_hashes(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Dry compose ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="e" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    candidate = _post_candidate(client, pid, [attempt_id])
    resp = client.get(f"/api/projects/{pid}/ship/candidates/{candidate['id']}/dry-compose")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["safe_to_compose"] is True
    assert data["commit_hashes"] == ["e" * 40]
    assert any("Compose this promotion candidate" in action for action in data["next_actions"])


def test_diff_requires_existing_composed_candidate(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    _ticket_id, attempt_id = accepted_ticket_and_attempt
    candidate = _post_candidate(client, pid, [attempt_id])
    resp = client.get(f"/api/projects/{pid}/ship/candidates/{candidate['id']}/diff")
    assert resp.status_code == 409
    data = resp.get_json()
    assert "No ship run exists" in data["error"]
    assert data["next_actions"]


def test_compose_returns_existing_active_run_instead_of_duplicating(client, project):
    """Retrying compose on an active run should return that run, not create another."""
    pid = project["id"]
    from models.db import db, Project

    # Start from an accepted candidate, then make the frontier stale after the run is active.
    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = "f" * 40
        db.session.commit()

    # Reuse the accepted ticket fixture pattern by creating a single accepted ticket.
    from models.db import Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Compose once",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    candidate, first = _compose_ids(client, pid, [attempt_id])
    assert first.status_code == 201
    run_id = first.get_json()["id"]

    composed = client.post(f"/api/worker/ship-run/{run_id}/composed", json={
        "composed_commit_hash": "d" * 40,
        "base_main_hash": "f" * 40,
        "test_status": "passed",
        "test_output": "ok",
        "changed_files": ["src/app.py"],
    })
    assert composed.status_code == 200
    assert composed.get_json()["status"] == "ready_to_ship"

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = "g" * 40
        db.session.commit()

    second = client.post(
        f"/api/projects/{pid}/ship/candidates/{candidate['id']}/compose",
        json={},
    )
    assert second.status_code == 200
    assert second.get_json()["id"] == run_id

    with client.application.app_context():
        assert Project.query.filter_by(id=pid).first().shipped_frontier == "g" * 40
        from models.db import ShipRun
        assert ShipRun.query.filter_by(project_id=pid).count() == 1


def test_worker_claim_moves_ship_run_to_composing(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Claimable candidate ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    compose = _compose_ids(client, pid, [attempt_id])[1]
    assert compose.status_code == 201
    run_id = compose.get_json()["id"]

    claim = client.post("/api/worker/ship-run/next", json={})
    assert claim.status_code == 200
    claim_data = claim.get_json()
    assert claim_data["run"]["id"] == run_id
    assert claim_data["run"]["status"] == "composing"
    assert claim_data["candidate"]["id"] == compose.get_json()["promotion_candidate_id"]
    assert claim_data["membership"]["commit_hashes"] == ["c" * 40]
    assert claim_data["commit_hashes"] == ["c" * 40]


def test_compose_allows_child_after_dependency_shipped(client, project):
    """Once a dependency is shipped into the frontier, the child candidate may compose."""
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt
    parent_hash = "p" * 40
    child_hash = "c" * 40

    client.post(f"/api/projects/{pid}/frontier", json={"hash": parent_hash, "source": "test"})

    with client.application.app_context():
        parent = Ticket(
            project_id=pid,
            column_id="done",
            title="Parent",
            intent_status="active",
        )
        db.session.add(parent)
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="done",
            title="Child",
            intent_status="active",
            depends_on_ticket_ids=[str(parent.id)],
        )
        db.session.add(child)
        db.session.flush()
        parent_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash=parent_hash,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="shipped",
            summary="parent",
        )
        child_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash=child_hash,
            base_hash=parent_hash,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="child",
        )
        db.session.add_all([parent_attempt, child_attempt])
        db.session.commit()
        child_attempt_id = str(child_attempt.id)

    compose = _compose_ids(client, pid, [child_attempt_id])[1]
    assert compose.status_code == 201
    assert compose.get_json()["status"] == "queued"


def test_ship_rejects_when_run_not_ready_to_ship(client, project):
    """Shipping only succeeds from ready_to_ship."""
    pid = project["id"]
    from models.db import db, ShipRun

    with client.application.app_context():
        run = ShipRun(project_id=pid, wave_num=0, status="queued")
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})
    assert resp.status_code == 409
    assert "ready_to_ship" in resp.get_json().get("error", "")


def test_ship_rejects_stale_composition_validation(client, project):
    """Ship must revalidate the composed candidate against the current frontier."""
    pid = project["id"]
    from models.db import db, Project, ShipRun, Ticket, TicketAttempt

    assert client.post(f"/api/projects/{pid}/frontier", json={
        "hash": "f" * 40,
        "source": "test",
    }).status_code == 200

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Stale ship",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        ))
        run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="ready_to_ship",
            composed_commit_hash="d" * 40,
            base_main_hash="f" * 40,
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    assert client.post(f"/api/projects/{pid}/frontier", json={
        "hash": "g" * 40,
        "source": "test",
    }).status_code == 200

    resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})
    assert resp.status_code == 409
    data = resp.get_json()
    assert "validation failed" in data.get("error", "")
    assert any(
        "not the current frontier" in detail or "Ship run base" in detail
        for detail in data.get("details", [])
    )


def test_candidate_compose_and_inspect_run(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Candidate ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="h" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    candidate_resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [attempt_id]},
    )
    assert candidate_resp.status_code == 201
    candidate = candidate_resp.get_json()

    compose_resp = client.post(f"/api/projects/{pid}/ship/candidates/{candidate['id']}/compose", json={})
    assert compose_resp.status_code == 201
    run = compose_resp.get_json()
    assert run["promotion_candidate_id"] == candidate["id"]
    assert run["candidate"]["id"] == candidate["id"]

    inspect_resp = client.get(f"/api/projects/{pid}/ship/runs/{run['id']}")
    assert inspect_resp.status_code == 200
    inspect = inspect_resp.get_json()
    assert inspect["candidate"]["id"] == candidate["id"]
    assert inspect["membership"]["attempts"][0]["id"] == attempt_id


def test_candidate_ship_only_transitions_candidate_membership(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket_a = Ticket(project_id=pid, column_id="done", title="A", intent_status="active")
        ticket_b = Ticket(project_id=pid, column_id="done", title="B", intent_status="active")
        db.session.add_all([ticket_a, ticket_b])
        db.session.flush()
        attempt_a = TicketAttempt(
            project_id=pid,
            ticket_id=ticket_a.id,
            agenthub_commit_hash="a" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="a",
        )
        attempt_b = TicketAttempt(
            project_id=pid,
            ticket_id=ticket_b.id,
            agenthub_commit_hash="b" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="b",
        )
        db.session.add_all([attempt_a, attempt_b])
        db.session.commit()
        attempt_a_id = str(attempt_a.id)
        attempt_b_id = str(attempt_b.id)

    candidate_resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [attempt_a_id]},
    )
    candidate = candidate_resp.get_json()
    compose_resp = client.post(f"/api/projects/{pid}/ship/candidates/{candidate['id']}/compose", json={})
    run_id = compose_resp.get_json()["id"]

    composed = client.post(f"/api/worker/ship-run/{run_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "base_main_hash": "f" * 40,
        "test_status": "passed",
        "test_output": "ok",
        "changed_files": ["src/app.py"],
    })
    assert composed.status_code == 200

    ship_resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})
    assert ship_resp.status_code == 200
    assert ship_resp.get_json()["candidate"]["id"] == candidate["id"]

    with client.application.app_context():
        shipped_a = db.session.get(TicketAttempt, attempt_a_id)
        shipped_b = db.session.get(TicketAttempt, attempt_b_id)
        assert shipped_a.status == "shipped"
        assert shipped_b.status == "accepted"


# ---------------------------------------------------------------------------
# 12.2d  Worker fail endpoint records compose_failed
# ---------------------------------------------------------------------------

def test_worker_fail_records_compose_failed(client, project):
    """Compatibility check for the older ship worker callback."""
    from models.db import db, ShipRun, Project
    with client.application.app_context():
        run = ShipRun(project_id=project["id"], wave_num=0, status="running")
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    resp = client.post(
        f"/api/worker/ship-run/{run_id}/fail",
        json={
            "error": "Conflict merging commit abc into release branch.",
            "compose_failed": True,
        },
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "compose_failed"

    with client.application.app_context():
        run = db.session.get(ShipRun, run_id)
        assert run.status == "compose_failed"
        assert "Conflict" in run.error


# ---------------------------------------------------------------------------
# 12.2e  Ship endpoint updates root after PR merge (mocked gh)
# ---------------------------------------------------------------------------

def test_ship_no_github_advances_frontier_directly(client, project, accepted_ticket_and_attempt):
    """Ship without GitHub URL: frontier advances from composed_commit_hash, no gh calls."""
    pid = project["id"]
    from models.db import db, ShipRun, Project
    with client.application.app_context():
        run = ShipRun(
            project_id=pid, wave_num=0, status="ready_to_ship",
            composed_commit_hash="z" * 40,
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "shipped"
    assert data["shipped_commit_hash"] == "z" * 40

    from models.db import Project
    with client.application.app_context():
        p = db.session.get(Project, pid)
        assert p.shipped_frontier == "z" * 40


def test_ship_updates_frontier_after_merge(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    ticket_id, attempt_id = accepted_ticket_and_attempt

    # Set github_url and create a ready_to_ship run
    from models.db import db, ShipRun, Project
    update_resp = client.put(
        f"/api/projects/{pid}",
        json={"github_url": "https://github.com/owner/repo"},
    )
    assert update_resp.status_code == 200
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="ready_to_ship",
            release_pr_number=99,
            release_pr_url="https://github.com/owner/repo/pull/99",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    new_sha = "f" * 40
    merge_ok = MagicMock(returncode=0, stdout="", stderr="")
    tip_ok = MagicMock(
        returncode=0,
        stdout=f'{{"object": {{"sha": "{new_sha}"}}}}',
        stderr="",
    )
    verify_ok = MagicMock(
        returncode=0,
        stdout='{"state":"OPEN","mergedAt":null}',
        stderr="",
    )

    with patch("subprocess.run", side_effect=[verify_ok, merge_ok, tip_ok]):
        resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "shipped"
    assert data["shipped_commit_hash"] == new_sha

    with client.application.app_context():
        p = db.session.get(Project, pid)
        assert p.shipped_frontier == new_sha


# ---------------------------------------------------------------------------
# 12.2f  Dependency ordering in dispatch
# ---------------------------------------------------------------------------

def test_dependent_ticket_not_dispatched_until_dep_accepted(client, project):
    """A ticket with an unsatisfied dep stays queued."""
    from models.db import db, Ticket
    with client.application.app_context():
        dep = Ticket(
            project_id=project["id"],
            column_id="done",
            title="Dep",
            intent_status="active",
        )
        db.session.add(dep)
        db.session.flush()
        child = Ticket(
            project_id=project["id"],
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[str(dep.id)],
        )
        db.session.add(child)
        db.session.commit()
        child_id = str(child.id)

    # Dispatch: dep has no accepted attempt → child should stay queued
    from api.services.ticket_service import dispatch_unblocked_queued
    with client.application.app_context():
        dispatch_unblocked_queued(project["id"])
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "queued", "Child should remain queued when dep has no accepted attempt"


def test_independent_ticket_dispatches_from_frontier_base(client, project):
    """Independent tickets use shipped_frontier as the explicit job base."""
    from models.db import db, Project, Ticket

    pid = project["id"]
    frontier = "f" * 40
    client.put(f"/api/projects/{pid}", json={"github_url": "https://github.com/owner/repo"})
    client.post(f"/api/projects/{pid}/frontier", json={"hash": frontier, "source": "test"})

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="queued",
            title="Independent",
            intent_status="ready",
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(ticket)
        db.session.commit()

    from api.services.ticket_service import dispatch_unblocked_queued
    dispatch_unblocked_queued(pid)

    resp = client.post("/api/worker/jobs/start", json={"project_id": pid})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["base_hash"] == project["accepted_frontier_id"]
    assert payload["base_leaf_id"] == project["accepted_frontier_id"]
    assert payload["base_selection"]["base_source"] == "ticket_base_leaf"
    assert payload["base_selection"]["blocked"] is False


def test_single_dependency_ticket_dispatches_from_parent_attempt_base(client, project):
    """A single accepted unshipped dependency becomes the explicit base."""
    from models.db import db, Ticket, TicketAttempt

    pid = project["id"]
    frontier = "f" * 40
    parent_hash = "a" * 40
    client.put(f"/api/projects/{pid}", json={"github_url": "https://github.com/owner/repo"})

    with client.application.app_context():
        from models.db import Project
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = frontier
        parent = Ticket(
            project_id=pid,
            column_id="done",
            title="Parent",
            intent_status="active",
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = Ticket(
            project_id=pid,
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[parent_id],
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash=parent_hash,
            base_hash=frontier,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="parent",
        ))
        db.session.commit()

    from api.services.ticket_service import dispatch_unblocked_queued
    dispatch_unblocked_queued(pid)

    resp = client.post("/api/worker/jobs/start", json={"project_id": pid})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["base_hash"] == project["accepted_frontier_id"]
    assert payload["base_leaf_id"] == project["accepted_frontier_id"]
    assert payload["agenthub_root_hash"] == project["accepted_frontier_id"]
    assert payload["base_selection"]["base_source"] == "ticket_base_leaf"
    assert payload["base_selection"]["blocked"] is False


def test_shipped_dependency_ticket_dispatches_from_current_frontier(client, project):
    """If dependencies are already shipped, the current frontier is reused."""
    from models.db import db, Project, Ticket, TicketAttempt

    pid = project["id"]
    frontier = "f" * 40
    shipped_hash = "s" * 40
    client.put(f"/api/projects/{pid}", json={"github_url": "https://github.com/owner/repo"})

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = frontier
        parent = Ticket(
            project_id=pid,
            column_id="done",
            title="Parent",
            intent_status="active",
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = Ticket(
            project_id=pid,
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[parent_id],
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash=shipped_hash,
            base_hash=frontier,
            wave_num=0,
            attempt_num=1,
            status="shipped",
            summary="parent shipped",
        ))
        db.session.commit()

    from api.services.ticket_service import dispatch_unblocked_queued
    dispatch_unblocked_queued(pid)

    resp = client.post("/api/worker/jobs/start", json={"project_id": pid})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["base_hash"] == project["accepted_frontier_id"]
    assert payload["base_leaf_id"] == project["accepted_frontier_id"]
    assert payload["agenthub_root_hash"] == project["accepted_frontier_id"]
    assert payload["base_selection"]["base_source"] == "ticket_base_leaf"
    assert payload["base_selection"]["blocked"] is False


def test_github_first_job_payload_uses_project_frontier_without_project_path(client):
    from models.db import AgentJob, Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "github-first-payload",
            "git_mode": "swarm",
            "source_type": "github",
            "github_url": "https://github.com/example/repo",
            "github_ref": "main",
            "accepted_frontier_id": "leaf_01HZX3GITHUBFIRST012345678",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project = create_project.get_json()
    pid = project["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="queued",
            title="GitHub source ticket",
            intent_status="ready",
            base_leaf_id=None,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    from api.services.ticket_service import dispatch_unblocked_queued
    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        stored_ticket = db.session.get(Ticket, ticket_id)
        assert stored_ticket.base_leaf_id == "leaf_01HZX3GITHUBFIRST012345678"
        assert AgentJob.query.filter_by(ticket_id=ticket_id).count() == 3

    resp = client.post("/api/worker/jobs/start", json={"project_id": pid})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["project_id"] == pid
    assert payload["ticket_id"] == ticket_id
    assert payload["job_id"]
    assert payload["base_leaf_id"] == "leaf_01HZX3GITHUBFIRST012345678"
    assert payload["accepted_frontier_id"] == "leaf_01HZX3GITHUBFIRST012345678"
    assert payload["github_url"] == "https://github.com/example/repo"
    assert payload["source_metadata"]["github_url"] == "https://github.com/example/repo"
    assert payload["source_metadata"]["github_ref"] == "main"
    assert payload["source_metadata"]["source_type"] == "github"
    assert "project_path" not in payload
    assert payload["base_selection"]["base_source"] == "ticket_base_leaf"
    assert payload["parallel_attempt_count"] == 3
    assert payload["attempt_count"] == "3"
    assert payload["attempt_index"] == "1"
    assert payload["attempt_strategy"] == "conservative-minimalist"


def test_worker_job_claim_fails_invalid_pending_job_without_blocking_queue(client):
    from models.db import AgentJob, Project, Ticket, db

    with client.application.app_context():
        project = Project(
            name="broken-github-project",
            git_mode="swarm",
            source_type="github",
            github_url="https://github.com/example/repo",
            execution_mode="docker",
            accepted_frontier_id=None,
        )
        db.session.add(project)
        db.session.flush()
        ticket = Ticket(
            project_id=project.id,
            column_id="in_progress",
            title="Missing frontier base",
            intent_status="active",
            base_leaf_id=None,
        )
        db.session.add(ticket)
        db.session.flush()
        job = AgentJob(
            ticket_id=ticket.id,
            project_id=project.id,
            kind="ticket",
            status="pending",
        )
        db.session.add(job)
        valid_ticket = Ticket(
            project_id=project.id,
            column_id="in_progress",
            title="Valid frontier base",
            intent_status="active",
            base_leaf_id="leaf_01HZX3VALIDBASE0123456789AB",
        )
        db.session.add(valid_ticket)
        db.session.flush()
        valid_job = AgentJob(
            ticket_id=valid_ticket.id,
            project_id=project.id,
            kind="ticket",
            status="pending",
        )
        db.session.add(valid_job)
        db.session.commit()
        project_id = str(project.id)
        job_id = str(job.id)
        invalid_ticket_id = str(ticket.id)
        valid_job_id = str(valid_job.id)
        valid_ticket_id = str(valid_ticket.id)

    resp = client.post("/api/worker/jobs/start", json={"project_id": project_id})
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["job_id"] == valid_job_id
    assert payload["ticket_id"] == valid_ticket_id
    assert payload["base_leaf_id"] == "leaf_01HZX3VALIDBASE0123456789AB"

    with client.application.app_context():
        invalid_job = db.session.get(AgentJob, job_id)
        claimed_job = db.session.get(AgentJob, valid_job_id)
        invalid_ticket = db.session.get(Ticket, invalid_ticket_id)
        assert invalid_job is not None
        assert invalid_job.status == "failed"
        assert invalid_ticket is not None
        assert invalid_ticket.column_id == "queued"
        assert invalid_ticket.failed_count == 1
        assert claimed_job is not None
        assert claimed_job.status == "running"


def test_worker_job_claim_allows_parallel_same_ticket_jobs_with_graph_overlap(client, project):
    from models.db import AgentJob, Ticket, db

    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Parallel same-ticket claim",
            intent_status="active",
            base_leaf_id=project["accepted_frontier_id"],
            associated_node_ids=["node-1"],
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add_all([
            AgentJob(
                ticket_id=ticket.id,
                project_id=project["id"],
                kind="ticket",
                status="pending",
            ),
            AgentJob(
                ticket_id=ticket.id,
                project_id=project["id"],
                kind="ticket",
                status="pending",
            ),
        ])
        db.session.commit()
        ticket_id = str(ticket.id)

    first = client.post("/api/worker/jobs/start", json={"project_id": project["id"]})
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["ticket_id"] == ticket_id
    assert first_payload["parallel_attempt_count"] == 2

    second = client.post("/api/worker/jobs/start", json={"project_id": project["id"]})
    assert second.status_code == 200
    second_payload = second.get_json()
    assert second_payload["ticket_id"] == ticket_id
    assert second_payload["parallel_attempt_count"] == 2
    assert second_payload["job_id"] != first_payload["job_id"]


def test_dispatch_unblocked_queued_enqueues_ticket_default_attempt_batch(client, project):
    from api.services.ticket_service import dispatch_unblocked_queued
    from models.db import AgentJob, Ticket, db

    client.put(
        f"/api/projects/{project['id']}",
        json={"github_url": "https://github.com/example/repo"},
    )

    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="queued",
            title="Default attempt batch",
            intent_status="ready",
            base_leaf_id=project["accepted_frontier_id"],
            default_attempt_count=3,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    with client.application.app_context():
        dispatch_unblocked_queued(project["id"])
        stored_ticket = db.session.get(Ticket, ticket_id)
        jobs = AgentJob.query.filter_by(ticket_id=ticket_id).order_by(AgentJob.created_at.asc()).all()

        assert stored_ticket is not None
        assert stored_ticket.column_id == "in_progress"
        assert stored_ticket.intent_status == "active"
        assert len(jobs) == 3
        assert len({job.attempt_metadata["attempt_batch_id"] for job in jobs}) == 1
        assert [job.attempt_metadata["attempt_index"] for job in jobs] == [1, 2, 3]
        assert [job.attempt_metadata["attempt_count"] for job in jobs] == [3, 3, 3]
        assert [job.attempt_metadata["attempt_strategy"] for job in jobs] == [
            "conservative-minimalist",
            "test-first-verifier",
            "architecture-cleanup",
        ]


def test_worker_job_fail_keeps_ticket_in_progress_when_parallel_attempts_remain(client, project):
    from models.db import AgentJob, Ticket, db

    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Parallel fail handling",
            intent_status="active",
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(ticket)
        db.session.flush()
        running_job = AgentJob(
            ticket_id=ticket.id,
            project_id=project["id"],
            kind="ticket",
            status="running",
        )
        pending_job = AgentJob(
            ticket_id=ticket.id,
            project_id=project["id"],
            kind="ticket",
            status="pending",
        )
        db.session.add_all([running_job, pending_job])
        db.session.commit()
        running_job_id = str(running_job.id)
        ticket_id = str(ticket.id)

    resp = client.post(f"/api/worker/jobs/{running_job_id}/fail", json={})
    assert resp.status_code == 200

    with client.application.app_context():
        stored_ticket = db.session.get(Ticket, ticket_id)
        assert stored_ticket is not None
        assert stored_ticket.column_id == "in_progress"
        assert stored_ticket.failed_count == 1


def test_multi_dependency_ticket_stays_queued_in_mvp(client, project):
    """Multiple accepted unshipped dependencies remain blocked in the MVP path."""
    from models.db import db, Project, Ticket, TicketAttempt, AgentJob
    from api.services.job_service import mvp_dependency_base_context

    pid = project["id"]
    client.put(f"/api/projects/{pid}", json={"github_url": "https://github.com/owner/repo"})

    with client.application.app_context():
        parent_a = Ticket(project_id=pid, column_id="done", title="Parent A", intent_status="active")
        parent_b = Ticket(project_id=pid, column_id="done", title="Parent B", intent_status="active")
        db.session.add_all([parent_a, parent_b])
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="queued",
            title="Child needs both parents",
            intent_status="ready",
            depends_on_ticket_ids=[str(parent_a.id), str(parent_b.id)],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add_all([
            TicketAttempt(
                project_id=pid,
                ticket_id=parent_a.id,
                agenthub_commit_hash="a" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="parent a",
            ),
            TicketAttempt(
                project_id=pid,
                ticket_id=parent_b.id,
                agenthub_commit_hash="b" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="parent b",
            ),
        ])
        db.session.commit()
        child_id = str(child.id)

    from api.services.ticket_service import dispatch_unblocked_queued
    dispatch_unblocked_queued(pid)

    with client.application.app_context():
        child = db.session.get(Ticket, child_id)
        project_row = db.session.get(Project, pid)
        base_context = mvp_dependency_base_context(child, project_row)
        assert child.column_id == "queued"
        assert AgentJob.query.filter_by(ticket_id=child_id).count() == 0
        assert base_context["blocked"] is True
        assert "Promote or ship prerequisite work first" in base_context["blocked_reason"]


# ---------------------------------------------------------------------------
# 12.2g  Intent fields persist and are returned
# ---------------------------------------------------------------------------

def test_intent_fields_roundtrip(client, project):
    pid = project["id"]
    resp = client.post(f"/api/projects/{pid}/tickets", json={
        "column_id": "backlog",
        "title": "Intent ticket",
        "rationale": "This matters because...",
        "acceptance_criteria": "Must pass all tests.",
        "constraints": "No breaking changes.",
        "intent_status": "ready",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["rationale"] == "This matters because..."
    assert data["acceptance_criteria"] == "Must pass all tests."
    assert data["constraints"] == "No breaking changes."
    assert data["intent_status"] == "ready"
    assert data["display_state"] is not None
