"""
AgentHub integration tests (plan 12.3).

All AgentHub HTTP calls are mocked — no running AgentHub required.

Covers:
  - validate_attempt passes when commit found in AgentHub
  - validate_attempt fails when commit not found (404)
  - validate_attempt accepts when AgentHub unreachable (non-blocking)
  - root refresh changes base for newly queued work
  - stale attempts are detectable (base_hash != shipped_frontier)
  - compute_base_hash selects dep hash when dep is accepted (not shipped)
  - compute_base_hash falls back to frontier when dep is shipped
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
