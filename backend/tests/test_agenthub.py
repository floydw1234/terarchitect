"""
AgentHub integration tests (plan 12.3).

All AgentHub HTTP calls are mocked — no running AgentHub required.

Covers:
  - validate_attempt passes when commit found in AgentHub
  - validate_attempt fails when commit not found (404)
  - validate_attempt accepts when AgentHub unreachable (non-blocking)
  - root refresh changes base for newly queued work
  - stale attempts are detectable (base_hash != shipped_frontier)
  - explicit MVP base selection uses frontier / accepted dependency / shipped frontier
  - coordinator env forwarding keeps BASE_HASH and AGENTHUB_ROOT_HASH intact
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# 12.3a  validate_attempt — commit found
# ---------------------------------------------------------------------------

def test_validate_attempt_accepts_when_commit_found(app):
    with app.app_context():
        from api.services.attempt_service import validate_attempt
        attempt = MagicMock()
        attempt.id = "a1"
        attempt.ticket_id = "t1"
        attempt.agenthub_commit_hash = "a" * 40
        attempt.base_hash = "b" * 40
        attempt.summary = "done"
        attempt.status = "proposed"

        ok_resp = MagicMock()
        ok_resp.ok = True
        ok_resp.status_code = 200

        with patch("api.services.attempt_service._requests.get", return_value=ok_resp):
            with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088"}):
                result = validate_attempt(attempt)

        assert result.status == "accepted"


# ---------------------------------------------------------------------------
# 12.3b  validate_attempt — commit not found (404)
# ---------------------------------------------------------------------------

def test_validate_attempt_fails_when_commit_not_found(app):
    with app.app_context():
        from api.services.attempt_service import validate_attempt
        attempt = MagicMock()
        attempt.id = "a2"
        attempt.ticket_id = "t2"
        attempt.agenthub_commit_hash = "c" * 40
        attempt.base_hash = None
        attempt.summary = "done"
        attempt.status = "proposed"

        not_found = MagicMock()
        not_found.ok = False
        not_found.status_code = 404

        with patch("api.services.attempt_service._requests.get", return_value=not_found):
            with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088"}):
                result = validate_attempt(attempt)

        assert result.status == "failed"
        assert "not found in AgentHub" in (result.validation_error or "")


# ---------------------------------------------------------------------------
# 12.3c  validate_attempt — AgentHub unreachable → accept (non-blocking)
# ---------------------------------------------------------------------------

def test_validate_attempt_accepts_when_agenthub_unreachable(app):
    with app.app_context():
        from api.services.attempt_service import validate_attempt
        attempt = MagicMock()
        attempt.id = "a3"
        attempt.ticket_id = "t3"
        attempt.agenthub_commit_hash = "d" * 40
        attempt.base_hash = None
        attempt.summary = "done"
        attempt.status = "proposed"

        with patch("api.services.attempt_service._requests.get",
                   side_effect=ConnectionError("unreachable")):
            with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088"}):
                result = validate_attempt(attempt)

        # Should accept — AgentHub unavailability must not block work
        assert result.status == "accepted"


# ---------------------------------------------------------------------------
# 12.3d  Root refresh changes base for newly queued work
# ---------------------------------------------------------------------------

def test_root_refresh_updates_frontier(client, project):
    pid = project["id"]
    new_hash = "e" * 40

    resp = client.post(f"/api/projects/{pid}/frontier", json={
        "hash": new_hash,
        "source": "manual",
    })
    assert resp.status_code == 200
    assert resp.get_json()["shipped_frontier"] == new_hash


# ---------------------------------------------------------------------------
# 12.3e  Stale attempts detectable via attempt_to_json
# ---------------------------------------------------------------------------

def test_stale_attempt_detected(app):
    with app.app_context():
        from api.services.attempt_service import attempt_to_json
        attempt = MagicMock()
        attempt.id = "a5"
        attempt.project_id = "p1"
        attempt.ticket_id = "t5"
        attempt.agenthub_commit_hash = "f" * 40
        attempt.short_commit_hash = "f" * 12
        attempt.base_hash = "old_base"
        attempt.wave_num = 0
        attempt.attempt_num = 1
        attempt.agent_id = None
        attempt.status = "accepted"
        attempt.summary = "done"
        attempt.validation_error = None
        attempt.test_status = None
        attempt.test_output = None
        attempt.created_at = None
        attempt.updated_at = None

        result = attempt_to_json(attempt, shipped_frontier="new_frontier")
        assert result["stale"] is True


def test_non_stale_attempt(app):
    with app.app_context():
        from api.services.attempt_service import attempt_to_json
        attempt = MagicMock()
        attempt.id = "a6"
        attempt.project_id = "p1"
        attempt.ticket_id = "t6"
        attempt.agenthub_commit_hash = "g" * 40
        attempt.base_hash = "same_frontier"
        attempt.wave_num = 0
        attempt.attempt_num = 1
        attempt.agent_id = None
        attempt.status = "accepted"
        attempt.summary = "done"
        attempt.validation_error = None
        attempt.test_status = None
        attempt.test_output = None
        attempt.created_at = None
        attempt.updated_at = None

        result = attempt_to_json(attempt, shipped_frontier="same_frontier")
        assert result["stale"] is False


# ---------------------------------------------------------------------------
# 12.3f  compute_base_hash: dep accepted → use dep hash; dep shipped → use frontier
# ---------------------------------------------------------------------------

def test_compute_base_hash_uses_dep_hash_when_accepted(app):
    with app.app_context():
        from api.services.job_service import compute_base_hash
        from models.db import db, Ticket, TicketAttempt, Project

        # Create ticket with dependency
        dep_id = str(__import__('uuid').uuid4())
        dep_hash = "h" * 40

        with patch("api.services.job_service.TicketAttempt") as MockTA:
            mock_attempt = MagicMock()
            mock_attempt.agenthub_commit_hash = dep_hash
            mock_attempt.wave_num = 0
            mock_attempt.status = "accepted"

            # Mock the query chain
            MockTA.query.filter_by.return_value.filter.return_value.order_by.return_value.first.return_value = mock_attempt

            ticket = MagicMock()
            ticket.depends_on_ticket_ids = [dep_id]

            project = MagicMock()
            project.shipped_frontier = "frontier_hash"

            result = compute_base_hash(ticket, project)
            # For accepted (not shipped) dep: use dep hash
            assert result == dep_hash


def test_compute_base_hash_uses_frontier_when_no_deps(app):
    with app.app_context():
        from api.services.job_service import compute_base_hash

        ticket = MagicMock()
        ticket.depends_on_ticket_ids = []

        project = MagicMock()
        project.shipped_frontier = "my_frontier"
        project.blessed_workspace_id = None

        result = compute_base_hash(ticket, project)
        assert result == "my_frontier"


def test_mvp_base_selection_uses_frontier_for_independent_ticket(client, project):
    from api.services.job_service import mvp_dependency_base_context
    from models.db import db, Ticket, Project

    frontier = "f" * 40
    with client.application.app_context():
        proj = db.session.get(Project, project["id"])
        proj.shipped_frontier = frontier
        ticket = Ticket(
            project_id=proj.id,
            column_id="queued",
            title="Independent ticket",
            intent_status="ready",
        )
        db.session.add(ticket)
        db.session.commit()
        ctx = mvp_dependency_base_context(ticket, proj)

    assert ctx["base_hash"] == frontier
    assert ctx["base_source"] == "shipped_frontier"
    assert ctx["blocked"] is False


def test_mvp_base_selection_uses_accepted_dependency_hash(client, project):
    from api.services.job_service import mvp_dependency_base_context
    from models.db import db, Project, Ticket, TicketAttempt

    frontier = "f" * 40
    dep_hash = "d" * 40
    with client.application.app_context():
        proj = db.session.get(Project, project["id"])
        proj.shipped_frontier = frontier
        parent = Ticket(
            project_id=proj.id,
            column_id="done",
            title="Parent",
            intent_status="active",
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = Ticket(
            project_id=proj.id,
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[parent_id],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add(TicketAttempt(
            project_id=proj.id,
            ticket_id=parent.id,
            agenthub_commit_hash=dep_hash,
            base_hash=frontier,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="parent done",
        ))
        db.session.commit()
        ctx = mvp_dependency_base_context(child, proj)

    assert ctx["base_hash"] == dep_hash
    assert ctx["base_source"] == "accepted_dependency"
    assert ctx["resolved_from_ticket_id"] == parent_id
    assert ctx["accepted_unshipped_dependency_ticket_ids"] == [parent_id]
    assert ctx["blocked"] is False


def test_mvp_base_selection_uses_frontier_when_dependencies_already_shipped(client, project):
    from api.services.job_service import mvp_dependency_base_context
    from models.db import db, Project, Ticket, TicketAttempt

    frontier = "f" * 40
    shipped_hash = "s" * 40
    with client.application.app_context():
        proj = db.session.get(Project, project["id"])
        proj.shipped_frontier = frontier
        parent = Ticket(
            project_id=proj.id,
            column_id="done",
            title="Shipped parent",
            intent_status="active",
        )
        db.session.add(parent)
        db.session.flush()
        parent_id = str(parent.id)
        child = Ticket(
            project_id=proj.id,
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[parent_id],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add(TicketAttempt(
            project_id=proj.id,
            ticket_id=parent.id,
            agenthub_commit_hash=shipped_hash,
            base_hash=frontier,
            wave_num=0,
            attempt_num=1,
            status="shipped",
            summary="parent shipped",
        ))
        db.session.commit()
        ctx = mvp_dependency_base_context(child, proj)

    assert ctx["base_hash"] == frontier
    assert ctx["base_source"] == "shipped_frontier"
    assert ctx["shipped_dependency_ticket_ids"] == [parent_id]
    assert ctx["blocked"] is False


def test_mvp_base_selection_blocks_multiple_unshipped_dependencies(client, project):
    from api.services.job_service import mvp_dependency_base_context
    from models.db import db, Project, Ticket, TicketAttempt

    frontier = "f" * 40
    with client.application.app_context():
        proj = db.session.get(Project, project["id"])
        proj.shipped_frontier = frontier
        parent_a = Ticket(project_id=proj.id, column_id="done", title="Parent A", intent_status="active")
        parent_b = Ticket(project_id=proj.id, column_id="done", title="Parent B", intent_status="active")
        db.session.add_all([parent_a, parent_b])
        db.session.flush()
        parent_a_id = str(parent_a.id)
        parent_b_id = str(parent_b.id)
        child = Ticket(
            project_id=proj.id,
            column_id="queued",
            title="Child",
            intent_status="ready",
            depends_on_ticket_ids=[parent_a_id, parent_b_id],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add_all([
            TicketAttempt(
                project_id=proj.id,
                ticket_id=parent_a.id,
                agenthub_commit_hash="a" * 40,
                base_hash=frontier,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="parent a",
            ),
            TicketAttempt(
                project_id=proj.id,
                ticket_id=parent_b.id,
                agenthub_commit_hash="b" * 40,
                base_hash=frontier,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="parent b",
            ),
        ])
        db.session.commit()
        ctx = mvp_dependency_base_context(child, proj)

    assert ctx["base_hash"] is None
    assert ctx["blocked"] is True
    assert ctx["accepted_unshipped_dependency_ticket_ids"] == [parent_a_id, parent_b_id]
    assert "Promote or ship prerequisite work first" in ctx["blocked_reason"]


def test_coordinator_job_to_env_forwards_base_hashes():
    from coordinator.coordinator import job_to_env

    env = job_to_env(
        {
            "project_id": "p1",
            "ticket_id": "t1",
            "repo_url": "https://github.com/org/repo",
            "job_id": "j1",
            "kind": "ticket",
            "base_hash": "b" * 40,
            "agenthub_root_hash": "f" * 40,
        },
        for_docker=False,
    )

    assert env["BASE_HASH"] == "b" * 40
    assert env["AGENTHUB_ROOT_HASH"] == "f" * 40


# ---------------------------------------------------------------------------
# 12.3g  Attempt controls: list, accept-supersede, reject
# ---------------------------------------------------------------------------

def test_ticket_attempts_list_returns_multiple_attempts_newest_first(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Multi-attempt ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        first = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="1" * 40,
            base_hash="0" * 40,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="first try",
        )
        second = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="2" * 40,
            base_hash="0" * 40,
            wave_num=0,
            attempt_num=2,
            status="proposed",
            summary="second try",
        )
        db.session.add_all([first, second])
        db.session.commit()
        ticket_id = str(ticket.id)

    resp = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/attempts")

    assert resp.status_code == 200
    data = resp.get_json()
    assert [attempt["attempt_num"] for attempt in data] == [2, 1]
    assert [attempt["status"] for attempt in data] == ["proposed", "proposed"]


def test_accept_attempt_supersedes_prior_accepted_attempt(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Supersede ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        first = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="first try",
        )
        second = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="b" * 40,
            base_hash="f" * 40,
            wave_num=0,
            attempt_num=2,
            status="proposed",
            summary="second try",
        )
        db.session.add_all([first, second])
        db.session.commit()
        ticket_id = str(ticket.id)
        first_attempt_id = str(first.id)
        second_attempt_id = str(second.id)

    first_accept = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{first_attempt_id}/accept"
    )
    assert first_accept.status_code == 200
    assert first_accept.get_json()["status"] == "accepted"

    second_accept = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{second_attempt_id}/accept"
    )
    assert second_accept.status_code == 200
    assert second_accept.get_json()["status"] == "accepted"

    resp = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/attempts")
    assert resp.status_code == 200
    attempts = resp.get_json()
    assert [attempt["attempt_num"] for attempt in attempts] == [2, 1]
    assert [attempt["status"] for attempt in attempts] == ["accepted", "superseded"]


def test_reject_attempt_returns_rejected_state(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Reject ticket",
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
            status="proposed",
            summary="needs work",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    resp = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/reject",
        json={"reason": "not ready"},
    )

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "rejected"

    attempts = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/attempts")
    assert attempts.status_code == 200
    assert attempts.get_json()[0]["status"] == "rejected"


# ---------------------------------------------------------------------------
# 8.4  Debug/audit endpoint
# ---------------------------------------------------------------------------

def test_project_debug_reports_frontier_attempts_runs_and_jobs(client, project):
    pid = project["id"]
    frontier = "f" * 40

    resp = client.post(f"/api/projects/{pid}/frontier", json={
        "hash": frontier,
        "source": "test",
    })
    assert resp.status_code == 200

    from models.db import db, Ticket, TicketAttempt, ShipRun, AgentJob
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Debug ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()

        accepted = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="old" * 13 + "o",
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        proposed = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="b" * 40,
            base_hash=frontier,
            wave_num=0,
            attempt_num=2,
            status="proposed",
            summary="new try",
        )
        run = ShipRun(project_id=pid, wave_num=0, status="queued")
        job = AgentJob(project_id=pid, ticket_id=ticket.id, kind="ticket", status="pending")
        db.session.add_all([accepted, proposed, run, job])
        db.session.commit()

    resp = client.get(f"/api/projects/{pid}/debug")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["project_id"] == pid
    assert data["shipped_frontier"] == frontier
    assert data["ticket_count"] == 1
    assert data["stale_attempt_count"] == 1
    assert len(data["stale_attempts"]) == 1
    assert data["stale_attempts"][0]["agenthub_commit_hash"] == "a" * 40
    assert data["accepted_attempts_by_wave"]["0"][0]["status"] == "accepted"
    assert data["pending_leaves"][0]["status"] == "accepted"
    assert {leaf["status"] for leaf in data["pending_leaves"]} == {"accepted", "proposed"}
    assert data["wave_summary"][0]["accepted_count"] == 1
    assert data["wave_summary"][0]["stale_count"] == 1
    assert data["open_ship_runs"][0]["status"] == "queued"
    assert data["active_jobs"][0]["status"] == "pending"
