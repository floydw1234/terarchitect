"""
End-to-end tests for the MVP ShipRun path.

Uses Flask test client. External calls (AgentHub, GitHub) are mocked.

12.5  End-to-end ship happy path:
  1.  Create project
  2.  Create dependency-linked tickets
  3.  Simulate agent completing tickets (ticket_complete)
  4.  Attempts publish to AgentHub (mocked validation)
  5.  Attempts become accepted
  6.  Ship Room compose wave
  7.  Shipper reports composed (mocked)
  8.  Release PR merged (mocked gh)
  9.  shipped_frontier advances
  10. Dependent queued work now satisfiable

"""
import os
import sys
import json
from unittest.mock import patch, MagicMock

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_ticket(client, project_id, title, deps=None, column_id="backlog"):
    resp = client.post(f"/api/projects/{project_id}/tickets", json={
        "column_id": column_id,
        "title": title,
        "depends_on_ticket_ids": deps or [],
        "intent_status": "ready",
    })
    assert resp.status_code == 201
    return resp.get_json()


def _move_to_in_progress(client, project_id, ticket_id):
    """Move a ticket to in_progress, bypassing graph/readiness checks in test."""
    from models.db import db, Ticket
    with client.application.app_context():
        t = db.session.get(Ticket, ticket_id)
        t.column_id = "in_progress"
        t.intent_status = "active"
        db.session.commit()


def _complete_ticket(client, project_id, ticket_id, commit_hash, base_hash=None):
    """Simulate agent calling /complete. Mocks AgentHub validation to accept."""
    ok_resp = MagicMock()
    ok_resp.ok = True
    ok_resp.status_code = 200
    with patch("api.services.attempt_service._requests.get", return_value=ok_resp):
        with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088"}):
            resp = client.post(
                f"/api/projects/{project_id}/tickets/{ticket_id}/complete",
                json={
                    "commit_hash": commit_hash,
                    "base_hash": base_hash,
                    "summary": f"Completed {ticket_id[:8]}",
                },
            )
    return resp


# ---------------------------------------------------------------------------
# 12.5  Ship happy path
# ---------------------------------------------------------------------------

def test_e2e_ship_happy_path(client, project):
    pid = project["id"]

    # Step 1+2: Create two dependency-linked tickets
    t_a = _create_ticket(client, pid, "Ticket A — no deps")
    t_b = _create_ticket(client, pid, "Ticket B — depends on A", deps=[t_a["id"]], column_id="queued")

    # Step 3: Move A to in_progress and complete it
    _move_to_in_progress(client, pid, t_a["id"])
    resp = _complete_ticket(client, pid, t_a["id"], "a" * 40)
    assert resp.status_code == 200

    # Step 4+5: Attempt A should be accepted (AgentHub mocked to return 200)
    wave_resp = client.get(f"/api/projects/{pid}/ship/waves")
    assert wave_resp.status_code == 200
    waves = wave_resp.get_json()
    wave_0 = next((w for w in waves if w["wave_num"] == 0), None)
    assert wave_0 is not None
    assert wave_0["accepted_count"] == 1

    # Step 5b: B is now unblocked — dispatch it
    from api.services.ticket_service import dispatch_unblocked_queued
    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        from models.db import db, Ticket
        t_b_db = db.session.get(Ticket, t_b["id"])
        # B should have moved to in_progress since A has an accepted attempt
        assert t_b_db.column_id == "in_progress", \
            f"B should be in_progress after A accepted, got {t_b_db.column_id}"

    _complete_ticket(client, pid, t_b["id"], "b" * 40, base_hash="a" * 40)

    # Step 6: Compose wave 0 (A)
    compose_resp = client.post(f"/api/projects/{pid}/ship/waves/0/compose", json={})
    assert compose_resp.status_code in (200, 201)
    compose_data = compose_resp.get_json()
    run_id = compose_data["id"]
    assert compose_data["status"] == "queued"

    claim_resp = client.post("/api/worker/ship-run/next", json={})
    assert claim_resp.status_code == 200
    claim_data = claim_resp.get_json()
    assert claim_data["run"]["id"] == run_id
    assert claim_data["run"]["status"] == "composing"
    assert claim_data["commit_hashes"] == ["a" * 40]

    # Step 7: Shipper reports composed
    composed_resp = client.post(f"/api/worker/ship-run/{run_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "base_main_hash": "d" * 40,
        "test_status": "passed",
        "test_output": "All tests pass.",
        "changed_files": ["src/app.py"],
    })
    assert composed_resp.status_code == 200
    composed_data = composed_resp.get_json()
    assert composed_data["status"] == "ready_to_ship"
    assert composed_data["composed_commit_hash"] == "c" * 40
    assert composed_data["base_main_hash"] == "d" * 40
    assert composed_data["changed_files"] == ["src/app.py"]
    assert composed_data["test_status"] == "passed"
    assert composed_data["test_output"] == "All tests pass."

    # Step 8+9: Ship via the no-main path — GitHub is optional.
    # No github_url on this project → ship advances frontier directly
    # from composed_commit_hash without any gh pr merge call.
    ship_resp = client.post(f"/api/projects/{pid}/ship/waves/0/ship", json={})

    assert ship_resp.status_code == 200
    data = ship_resp.get_json()
    assert data["status"] == "shipped"
    # shipped_commit_hash == composed_commit_hash (no gh PR needed)
    assert data["shipped_commit_hash"] == "c" * 40

    # Step 10: shipped_frontier advanced
    with client.application.app_context():
        from models.db import db, Project
        p = db.session.get(Project, pid)
        assert p.shipped_frontier == "c" * 40, "Frontier must advance after ship"

    # Verify no per-ticket PR was created (plan 12.5 acceptance criteria)
    with client.application.app_context():
        from models.db import TicketAttempt
        attempts = TicketAttempt.query.filter_by(project_id=pid).all()
        statuses_by_commit = {a.agenthub_commit_hash: a.status for a in attempts}
        assert statuses_by_commit["a" * 40] == "shipped"
        assert statuses_by_commit["b" * 40] == "accepted"


def test_e2e_create_promotion_candidate_from_accepted_attempts(client, project):
    pid = project["id"]
    frontier = "f" * 40

    frontier_resp = client.post(f"/api/projects/{pid}/frontier", json={
        "hash": frontier,
        "source": "test",
    })
    assert frontier_resp.status_code == 200

    t_a = _create_ticket(client, pid, "Ticket A")
    t_b = _create_ticket(client, pid, "Ticket B depends on A", deps=[t_a["id"]], column_id="queued")

    _move_to_in_progress(client, pid, t_a["id"])
    resp_a = _complete_ticket(client, pid, t_a["id"], "a" * 40, base_hash=frontier)
    assert resp_a.status_code == 200

    from api.services.ticket_service import dispatch_unblocked_queued
    from models.db import db, TicketAttempt
    with client.application.app_context():
        dispatch_unblocked_queued(pid)

    resp_b = _complete_ticket(client, pid, t_b["id"], "b" * 40, base_hash="a" * 40)
    assert resp_b.status_code == 200

    attempts_resp = client.get(f"/api/projects/{pid}/attempts?status=accepted")
    assert attempts_resp.status_code == 200
    attempts = attempts_resp.get_json()
    by_commit = {attempt["agenthub_commit_hash"]: attempt for attempt in attempts}

    candidate_resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [by_commit["b" * 40]["id"]]},
    )
    assert candidate_resp.status_code == 201
    candidate = candidate_resp.get_json()
    assert candidate["status"] == "valid"
    assert set(candidate["selected_attempt_ids"]) == {by_commit["a" * 40]["id"], by_commit["b" * 40]["id"]}
    assert candidate["selected_leaf_hashes"] == ["b" * 40]

    with client.application.app_context():
        stored_attempt_ids = set(
            db.session.get(TicketAttempt, attempt_id).agenthub_commit_hash
            for attempt_id in candidate["selected_attempt_ids"]
        )
        assert stored_attempt_ids == {"a" * 40, "b" * 40}
