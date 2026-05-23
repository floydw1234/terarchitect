"""
End-to-end tests (plan 12.5 + 12.6).

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

12.6  End-to-end Composite Workspace path:
  1.  Create project + two independent intents
  2.  Simulate agent completing both tickets
  3.  Attempts become accepted
  4.  Create workspace with both attempts
  5.  Workspace composer reports composed (mocked)
  6.  User blesses composite → blessed_workspace_id set
  7.  Promote to ShipRun → ShipRun created
  8.  New ticket's base selected from blessed workspace
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

def _create_ticket(client, project_id, title, deps=None):
    resp = client.post(f"/api/projects/{project_id}/tickets", json={
        "column_id": "backlog",
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
        t = Ticket.query.get(ticket_id)
        t.column_id = "in_progress"
        t.intent_status = "active"
        db.session.commit()


def _complete_ticket(client, project_id, ticket_id, commit_hash):
    """Simulate agent calling /complete. Mocks AgentHub validation to accept."""
    ok_resp = MagicMock()
    ok_resp.ok = True
    ok_resp.status_code = 200
    with patch("api.services.attempt_service._requests.get", return_value=ok_resp):
        with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088"}):
            resp = client.post(
                f"/api/projects/{project_id}/tickets/{ticket_id}/complete",
                json={"commit_hash": commit_hash, "summary": f"Completed {ticket_id[:8]}"},
            )
    return resp


# ---------------------------------------------------------------------------
# 12.5  Ship happy path
# ---------------------------------------------------------------------------

def test_e2e_ship_happy_path(client, project):
    pid = project["id"]

    # Step 1+2: Create two dependency-linked tickets
    t_a = _create_ticket(client, pid, "Ticket A — no deps")
    t_b = _create_ticket(client, pid, "Ticket B — depends on A", deps=[t_a["id"]])

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
        from models.db import Ticket
        t_b_db = Ticket.query.get(t_b["id"])
        # B should have moved to in_progress since A has an accepted attempt
        assert t_b_db.column_id == "in_progress", \
            f"B should be in_progress after A accepted, got {t_b_db.column_id}"

    _complete_ticket(client, pid, t_b["id"], "b" * 40)

    # Step 6: Compose wave 0 (A) — both tickets done
    compose_resp = client.post(f"/api/projects/{pid}/ship/waves/0/compose", json={})
    assert compose_resp.status_code in (200, 201)
    run_id = compose_resp.get_json()["id"]

    # Step 7: Shipper reports composed
    composed_resp = client.post(f"/api/worker/ship-run/{run_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "base_main_hash": "d" * 40,
        "test_status": "passed",
        "test_output": "All tests pass.",
        "changed_files": ["src/app.py"],
    })
    assert composed_resp.status_code == 200
    assert composed_resp.get_json()["status"] == "ready_to_ship"

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
    from models.db import Project
    with client.application.app_context():
        p = Project.query.get(pid)
        assert p.shipped_frontier == "c" * 40, "Frontier must advance after ship"

    # Verify no per-ticket PR was created (plan 12.5 acceptance criteria)
    with client.application.app_context():
        from models.db import TicketAttempt
        attempts = TicketAttempt.query.filter_by(project_id=pid).all()
        for attempt in attempts:
            assert attempt.status == "shipped"


# ---------------------------------------------------------------------------
# 12.6  Composite Workspace path
# ---------------------------------------------------------------------------

def test_e2e_workspace_bless_and_promote(client, project):
    pid = project["id"]

    # Step 1: Two independent intents
    t1 = _create_ticket(client, pid, "Intent 1 — independent")
    t2 = _create_ticket(client, pid, "Intent 2 — independent")

    # Step 2+3: Complete both
    _move_to_in_progress(client, pid, t1["id"])
    _move_to_in_progress(client, pid, t2["id"])
    resp1 = _complete_ticket(client, pid, t1["id"], "1" * 40)
    resp2 = _complete_ticket(client, pid, t2["id"], "2" * 40)
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Get attempt IDs
    with client.application.app_context():
        from models.db import TicketAttempt
        att1 = TicketAttempt.query.filter_by(ticket_id=t1["id"]).first()
        att2 = TicketAttempt.query.filter_by(ticket_id=t2["id"]).first()
        att1_id = str(att1.id)
        att2_id = str(att2.id)

    # Step 4: Create workspace
    ws_resp = client.post(f"/api/projects/{pid}/workspaces", json={
        "attempt_ids": [att1_id, att2_id],
    })
    assert ws_resp.status_code == 201
    ws_id = ws_resp.get_json()["id"]

    # Trigger composition
    compose_resp = client.post(f"/api/projects/{pid}/workspaces/{ws_id}/compose", json={})
    assert compose_resp.status_code == 200
    assert compose_resp.get_json()["status"] == "composing"

    # Step 5: Workspace composer reports composed
    composed_resp = client.post(f"/api/worker/workspaces/{ws_id}/composed", json={
        "composed_commit_hash": "3" * 40,
        "test_status": "passed",
        "test_output": "All clear.",
        "changed_files": ["src/feature_a.py", "src/feature_b.py"],
    })
    assert composed_resp.status_code == 200
    assert composed_resp.get_json()["status"] == "preview_ready"

    # Step 6: Bless composite
    bless_resp = client.post(f"/api/projects/{pid}/workspaces/{ws_id}/bless", json={})
    assert bless_resp.status_code == 200
    assert bless_resp.get_json()["status"] == "blessed"

    # blessed_workspace_id set on project
    with client.application.app_context():
        from models.db import Project
        p = Project.query.get(pid)
        assert p.blessed_workspace_id == ws_id

    # Step 7: Promote to ShipRun
    promote_resp = client.post(f"/api/projects/{pid}/workspaces/{ws_id}/promote", json={})
    assert promote_resp.status_code == 200
    result = promote_resp.get_json()
    assert "ship_run" in result
    assert result["ship_run"]["status"] == "queued"

    # Step 8: New ticket should start from blessed composite's commit hash
    from api.services.job_service import compute_base_hash
    with client.application.app_context():
        from models.db import Ticket, CompositeWorkspace
        new_ticket = Ticket(
            project_id=pid,
            column_id="queued",
            title="Post-bless ticket",
            intent_status="ready",
        )
        from models.db import db
        db.session.add(new_ticket)
        db.session.commit()

        from models.db import Project
        p = Project.query.get(pid)
        base = compute_base_hash(new_ticket, p)
        # Base should be the blessed composite's composed hash
        assert base == "3" * 40, \
            f"Post-bless ticket should start from blessed composite hash, got {base}"


# ---------------------------------------------------------------------------
# 12.6b  Workspace does not require a persistent main branch
# ---------------------------------------------------------------------------

def test_workspace_compose_without_frontier(client, project):
    """Workspace can be created and composed even when shipped_frontier is not set."""
    pid = project["id"]
    t = _create_ticket(client, pid, "Ticket without frontier")
    _move_to_in_progress(client, pid, t["id"])
    _complete_ticket(client, pid, t["id"], "a" * 40)

    with client.application.app_context():
        from models.db import TicketAttempt
        att = TicketAttempt.query.filter_by(ticket_id=t["id"]).first()
        att_id = str(att.id)

    ws_resp = client.post(f"/api/projects/{pid}/workspaces", json={
        "attempt_ids": [att_id],
    })
    assert ws_resp.status_code == 201
    ws = ws_resp.get_json()
    # base_root_hash may be None if no frontier — workspace still created
    assert ws["id"] is not None
    assert ws["status"] == "draft"
