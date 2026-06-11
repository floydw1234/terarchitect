"""
Backend integration tests (plan 12.2).

Uses Flask test client against in-memory SQLite.

Covers:
  - swarm ticket completion creates TicketAttempt
  - no PR row created for swarm ticket completion
  - ship wave detail lists accepted attempts
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


# ---------------------------------------------------------------------------
# 12.2b  Ship wave detail lists accepted attempts
# ---------------------------------------------------------------------------

def test_ship_wave_detail_lists_accepted_attempts(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    resp = client.get(f"/api/projects/{pid}/ship/waves/0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["wave_num"] == 0
    assert len(data["accepted_attempts"]) == 1
    assert data["accepted_attempts"][0]["status"] == "accepted"
    assert data["can_compose"] is True


def test_ship_wave_list_shows_wave(client, project, accepted_ticket_and_attempt):
    pid = project["id"]
    resp = client.get(f"/api/projects/{pid}/ship/waves")
    assert resp.status_code == 200
    waves = resp.get_json()
    assert len(waves) >= 1
    w0 = next(w for w in waves if w["wave_num"] == 0)
    assert w0["accepted_count"] == 1
    assert w0["all_done"] is True


def test_ship_wave_detail_explains_unknown_dependency_refs(client, project):
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
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="d" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        ))
        db.session.commit()

    resp = client.get(f"/api/projects/{pid}/ship/waves/0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_compose"] is False
    assert any("unknown dependency" in blocker.lower() for blocker in data["blockers"])
    assert data["unknown_dependency_refs"][0]["unknown_dependency_ids"] == [missing_dep]
    assert "Unknown refs" in data["tickets"][0]["dependency_reason"]


def test_ship_wave_detail_explains_dependency_cycles(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        a = Ticket(project_id=pid, column_id="done", title="Cycle A", intent_status="active")
        b = Ticket(project_id=pid, column_id="done", title="Cycle B", intent_status="active")
        db.session.add_all([a, b])
        db.session.flush()
        a.depends_on_ticket_ids = [str(b.id)]
        b.depends_on_ticket_ids = [str(a.id)]
        db.session.add_all([
            TicketAttempt(
                project_id=pid,
                ticket_id=a.id,
                agenthub_commit_hash="a" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="a",
            ),
            TicketAttempt(
                project_id=pid,
                ticket_id=b.id,
                agenthub_commit_hash="b" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="b",
            ),
        ])
        db.session.commit()

    resp = client.get(f"/api/projects/{pid}/ship/waves/0")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["can_compose"] is False
    assert data["dependency_cycles"]
    assert any("cycle" in blocker.lower() for blocker in data["blockers"])
    assert any(t["dependency_cycles"] for t in data["tickets"])


def test_ship_waves_explain_includes_blockers(client, project):
    pid = project["id"]
    from models.db import db, Ticket

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Needs attempt",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.commit()

    resp = client.get(f"/api/projects/{pid}/ship/waves?explain=1")
    assert resp.status_code == 200
    waves = resp.get_json()
    wave = next(w for w in waves if w["wave_num"] == 0)
    assert wave["can_compose"] is False
    assert wave["next_actions"]


# ---------------------------------------------------------------------------
# 12.2c  Compose endpoint rejects when no accepted attempts
# ---------------------------------------------------------------------------

def test_compose_rejects_no_accepted_attempts(client, project):
    pid = project["id"]
    from models.db import db, Ticket
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="in_progress",
            title="No attempt ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.commit()

    resp = client.post(f"/api/projects/{pid}/ship/waves/0/compose", json={})
    assert resp.status_code == 409
    assert "No accepted attempts" in resp.get_json().get("error", "")


def test_compose_rejects_wave_with_unshipped_dependency(client, project):
    """A later wave cannot compose until earlier dependency leaves are shipped."""
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
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash=parent_hash,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="parent",
        ))
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash=child_hash,
            base_hash=parent_hash,
            wave_num=1,
            attempt_num=1,
            status="accepted",
            summary="child",
        ))
        db.session.commit()

    resp = client.post(f"/api/projects/{pid}/ship/waves/1/compose", json={})

    assert resp.status_code == 409
    data = resp.get_json()
    assert "Wave composition validation failed" in data.get("error", "")
    assert any("not shipped" in detail for detail in data.get("details", []))


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
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="e" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        ))
        db.session.commit()

    resp = client.get(f"/api/projects/{pid}/ship/waves/0/dry-compose")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["safe_to_compose"] is True
    assert data["commit_hashes"] == ["e" * 40]
    assert any("Compose this promotion candidate" in action for action in data["next_actions"])


def test_diff_requires_existing_composed_wave(client, project):
    pid = project["id"]
    resp = client.get(f"/api/projects/{pid}/ship/waves/0/diff")
    assert resp.status_code == 409
    data = resp.get_json()
    assert "No ship run exists" in data["error"]
    assert data["next_actions"]


def test_compose_returns_existing_active_run_instead_of_duplicating(client, project):
    """Retrying compose on an active run should return that run, not create another."""
    pid = project["id"]
    from models.db import db, Project

    # Start from an accepted wave, then make the frontier stale after the run is active.
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

    first = client.post(f"/api/projects/{pid}/ship/waves/0/compose", json={})
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

    second = client.post(f"/api/projects/{pid}/ship/waves/0/compose", json={})
    assert second.status_code == 200
    assert second.get_json()["id"] == run_id

    with client.application.app_context():
        assert Project.query.filter_by(id=pid).first().shipped_frontier == "g" * 40
        from models.db import ShipRun
        assert ShipRun.query.filter_by(project_id=pid, wave_num=0).count() == 1


def test_worker_claim_moves_ship_run_to_composing(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Claimable wave ticket",
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
        db.session.commit()

    compose = client.post(f"/api/projects/{pid}/ship/waves/0/compose", json={})
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


def test_compose_allows_wave_after_dependency_shipped(client, project):
    """Once a dependency is shipped into the frontier, the next wave may compose."""
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
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=parent.id,
            agenthub_commit_hash=parent_hash,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="shipped",
            summary="parent",
        ))
        db.session.add(TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash=child_hash,
            base_hash=parent_hash,
            wave_num=1,
            attempt_num=1,
            status="accepted",
            summary="child",
        ))
        db.session.commit()

    resp = client.post(f"/api/projects/{pid}/ship/waves/1/compose", json={})

    assert resp.status_code == 201
    assert resp.get_json()["status"] == "queued"


def test_ship_rejects_when_run_not_ready_to_ship(client, project):
    """Shipping only succeeds from ready_to_ship."""
    pid = project["id"]
    from models.db import db, ShipRun

    with client.application.app_context():
        run = ShipRun(project_id=pid, wave_num=0, status="queued")
        db.session.add(run)
        db.session.commit()

    resp = client.post(f"/api/projects/{pid}/ship/waves/0/ship", json={})
    assert resp.status_code == 409
    assert "ready_to_ship" in resp.get_json().get("error", "")


def test_ship_rejects_stale_composition_validation(client, project):
    """Ship must revalidate the composed wave against the current frontier."""
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

    assert client.post(f"/api/projects/{pid}/frontier", json={
        "hash": "g" * 40,
        "source": "test",
    }).status_code == 200

    resp = client.post(f"/api/projects/{pid}/ship/waves/0/ship", json={})
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

    resp = client.post(f"/api/projects/{pid}/ship/waves/0/ship", json={})
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
        resp = client.post(f"/api/projects/{pid}/ship/waves/0/ship", json={})

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
