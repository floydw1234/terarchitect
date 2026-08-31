"""
Concurrency and race-condition tests (plan 8.5).

Tests run against a minimal in-memory SQLite app (no Docker, no AgentHub).

Scenarios covered:
  1. Two simultaneous compose requests → one run created, not two
  2. Compose fires before a candidate has accepted attempts → rejected 409
  3. Pre-ship: PR already merged externally → reconcile as shipped
  4. Accept attempt while compose running → compose not affected
  5. Reject attempt after it is composed/release_pr_open → 409 (terminal state)
  6. Coordinator restart reset-stale endpoint → running ship runs re-queued
"""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def app():
    """Minimal Flask app wired to an in-memory SQLite DB."""
    os.environ.setdefault("SQLALCHEMY_DATABASE_URI", "sqlite:///:memory:")
    os.environ.setdefault("MEMORY_SAVE_DIR", "/tmp/terarchitect_test")
    from main import create_app
    application = create_app()
    application.config["TESTING"] = True
    application.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with application.app_context():
        from models.db import db
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def project(client, app):
    """Create a swarm project and return its JSON."""
    resp = client.post(
        "/api/projects",
        json={
            "name": "test-proj",
            "git_mode": "swarm",
            "accepted_frontier_id": "leaf_01HZX3CONCURRENCYBASE01234567",
            "is_existing_repo": True,
        },
    )
    assert resp.status_code == 201
    return resp.get_json()


@pytest.fixture
def accepted_attempt_pair(client, project):
    """Create a ticket with an accepted attempt, return (ticket_id, attempt_id)."""
    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="done",
            title="T1",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=project["id"],
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash=project["accepted_frontier_id"],
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        return str(ticket.id), str(attempt.id)


def _compose_attempt(client, pid, attempt_id):
    resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [attempt_id]},
    )
    assert resp.status_code == 201, resp.get_json()
    candidate_id = resp.get_json()["id"]
    return client.post(
        f"/api/projects/{pid}/ship/candidates/{candidate_id}/compose",
        json={},
    )


# ---------------------------------------------------------------------------
# Scenario 1: Double compose → idempotent (one ship run)
# ---------------------------------------------------------------------------

def test_double_compose_idempotent(client, project, accepted_attempt_pair):
    """Two simultaneous compose requests should produce exactly one ShipRun."""
    pid = project["id"]
    _ticket_id, attempt_id = accepted_attempt_pair

    r1 = _compose_attempt(client, pid, attempt_id)
    r2 = _compose_attempt(client, pid, attempt_id)

    # Both should succeed (200 or 201)
    assert r1.status_code in (200, 201)
    assert r2.status_code in (200, 201)

    # Same run ID returned
    assert r1.get_json()["id"] == r2.get_json()["id"]

    from models.db import ShipRun
    with client.application.app_context():
        runs = ShipRun.query.filter_by(project_id=pid).filter(
            ShipRun.status.in_(["queued", "running", "composing"])
        ).all()
        assert len(runs) == 1, f"Expected 1 active run, got {len(runs)}"


def test_compose_returns_existing_ready_to_ship_run(client, project, accepted_attempt_pair):
    """Repeated compose after shipper reports ready should not create a second ShipRun."""
    pid = project["id"]
    _ticket_id, attempt_id = accepted_attempt_pair

    first = _compose_attempt(client, pid, attempt_id)
    assert first.status_code == 201
    run_id = first.get_json()["id"]

    composed = client.post(f"/api/worker/ship-run/{run_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "base_main_hash": "f" * 40,
        "test_status": "passed",
        "test_output": "ok",
        "changed_files": ["src/app.py"],
    })
    assert composed.status_code == 200

    r = _compose_attempt(client, pid, attempt_id)

    assert r.status_code == 200
    assert r.get_json()["id"] == run_id

    from models.db import ShipRun
    with client.application.app_context():
        runs = ShipRun.query.filter_by(project_id=pid).all()
        assert len(runs) == 1


# ---------------------------------------------------------------------------
# Scenario 2: Compose before an accepted attempt exists → 409
# ---------------------------------------------------------------------------

def test_compose_without_accepted_attempts_rejected(client, project):
    """Compose should fail when no tickets have accepted attempts."""
    pid = project["id"]

    r = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": []},
    )
    assert r.status_code == 201
    candidate = r.get_json()
    assert candidate["status"] == "blocked"
    compose = client.post(
        f"/api/projects/{pid}/ship/candidates/{candidate['id']}/compose",
        json={},
    )
    assert compose.status_code == 409
    assert "Candidate composition validation failed" in compose.get_json().get("error", "")


# ---------------------------------------------------------------------------
# Scenario 3: Pre-ship check: PR already merged → reconcile as shipped
# ---------------------------------------------------------------------------

def test_ship_pr_already_merged(client, project, accepted_attempt_pair):
    """Ship should reconcile state if the release PR was already merged."""
    pid = project["id"]

    # Create a ready_to_ship run with a PR number
    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="ready_to_ship",
            composed_commit_hash="c" * 40,
            release_pr_number=42,
            release_pr_url="https://github.com/o/r/pull/42",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    # Patch project github_url and gh pr view to return MERGED
    update_resp = client.put(
        f"/api/projects/{pid}",
        json={"github_url": "https://github.com/owner/repo"},
    )
    assert update_resp.status_code == 200

    merged_response = MagicMock()
    merged_response.returncode = 0
    merged_response.stdout = '{"state":"MERGED","mergedAt":"2026-05-22T00:00:00Z","headRefOid":"' + ("c" * 40) + '"}'

    with patch("subprocess.run", return_value=merged_response):
        r = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert r.status_code == 200
    assert r.get_json()["status"] == "shipped"
    assert r.get_json()["shipped_commit_hash"] == "c" * 40


def test_ship_rejects_release_pr_branch_mismatch(client, project, accepted_attempt_pair):
    """Ship should block when the GitHub PR branch is not the run's release branch."""
    pid = project["id"]

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="ready_to_ship",
            release_branch="terarchitect/release/ship-abc12345",
            release_pr_number=42,
            release_pr_url="https://github.com/o/r/pull/42",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    update_resp = client.put(
        f"/api/projects/{pid}",
        json={"github_url": "https://github.com/owner/repo"},
    )
    assert update_resp.status_code == 200

    view_response = MagicMock()
    view_response.returncode = 0
    view_response.stdout = (
        '{"state":"OPEN","mergedAt":null,'
        '"headRefName":"somebody-elses-branch","headRefOid":"a"}'
    )

    with patch("subprocess.run", return_value=view_response):
        r = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert r.status_code == 409
    body = r.get_json()
    assert "branch does not match" in body.get("error", "")
    assert body["expected_branch"] == "terarchitect/release/ship-abc12345"


def test_ship_rejects_release_pr_head_mismatch(client, project, accepted_attempt_pair):
    """Ship should block when the GitHub PR head is not the composed commit."""
    pid = project["id"]

    from models.db import db, ShipRun
    expected_head = "a" * 40
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="ready_to_ship",
            release_branch="terarchitect/release/ship-abc12345",
            composed_commit_hash=expected_head,
            release_pr_number=42,
            release_pr_url="https://github.com/o/r/pull/42",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    update_resp = client.put(
        f"/api/projects/{pid}",
        json={"github_url": "https://github.com/owner/repo"},
    )
    assert update_resp.status_code == 200

    view_response = MagicMock()
    view_response.returncode = 0
    view_response.stdout = (
        '{"state":"OPEN","mergedAt":null,'
        '"headRefName":"terarchitect/release/ship-abc12345",'
        f'"headRefOid":"{"b" * 40}"}}'
    )

    with patch("subprocess.run", return_value=view_response):
        r = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert r.status_code == 409
    body = r.get_json()
    assert "head does not match" in body.get("error", "")
    assert body["expected_head"] == expected_head


def test_ship_direct_requires_composed_commit(client, project, accepted_attempt_pair):
    """Direct no-GitHub shipping must have an explicit composed commit."""
    pid = project["id"]

    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="ready_to_ship",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    r = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert r.status_code == 409
    assert "no composed commit hash" in r.get_json().get("error", "").lower()


# ---------------------------------------------------------------------------
# Scenario 4: Accept attempt while compose is running → compose unaffected
# ---------------------------------------------------------------------------

def test_accept_attempt_while_compose_running(client, project, accepted_attempt_pair):
    """Accepting a winner on another ticket after compose started does not change the running compose."""
    pid = project["id"]

    # Put a run in "running" state (simulate shipper mid-run)
    from models.db import db, ShipRun
    with client.application.app_context():
        run = ShipRun(project_id=pid, wave_num=0, status="running")
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    # Accept a validated winner on a different ticket — should succeed without
    # touching the in-flight compose. A second attempt on the same ticket cannot
    # supersede an already-integrated winner.
    from models.db import TicketAttempt, Ticket
    now = datetime.now(timezone.utc)
    with client.application.app_context():
        other = Ticket(
            project_id=pid,
            column_id="done",
            title="T2",
            intent_status="active",
            base_leaf_id=project["accepted_frontier_id"],
        )
        db.session.add(other)
        db.session.flush()
        new_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=other.id,
            agenthub_commit_hash="c" * 40,
            base_hash=project["accepted_frontier_id"],
            wave_num=0,
            attempt_num=1,
            status="validated",
            validated_at=now,
            is_winner=True,
            winner_chosen_at=now,
        )
        db.session.add(new_attempt)
        db.session.commit()
        other_ticket_id = str(other.id)
        new_attempt_id = str(new_attempt.id)

    r = client.post(
        f"/api/projects/{pid}/tickets/{other_ticket_id}/attempts/{new_attempt_id}/accept",
        json={},
    )
    assert r.status_code == 200, r.get_json()

    # Running ship run should still be running (compose not cancelled)
    with client.application.app_context():
        still_running = ShipRun.query.filter_by(id=run_id).first()
        assert still_running.status == "running"


# ---------------------------------------------------------------------------
# Scenario 5: Reject attempt after it is composed → 409
# ---------------------------------------------------------------------------

def test_reject_composed_attempt_fails(client, project, accepted_attempt_pair):
    """Rejecting an attempt that is already 'composed' or 'release_pr_open' is not allowed."""
    pid = project["id"]
    ticket_id, attempt_id = accepted_attempt_pair

    # Advance attempt to composed
    from models.db import db, TicketAttempt
    with client.application.app_context():
        attempt = db.session.get(TicketAttempt, attempt_id)
        attempt.status = "composed"
        db.session.commit()

    r = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/reject",
        json={"reason": "too slow"},
    )
    # composed → rejected is not a valid transition
    assert r.status_code == 409
    assert "Cannot transition" in r.get_json().get("error", "")


# ---------------------------------------------------------------------------
# Scenario 6: Coordinator restart → stale running ship runs re-queued
# ---------------------------------------------------------------------------

def test_stale_ship_run_reset(client, project):
    """Running ship runs older than max_age_seconds should be reset to queued."""
    pid = project["id"]
    from models.db import db, ShipRun
    from datetime import datetime, timezone, timedelta

    with client.application.app_context():
        stale_run = ShipRun(
            project_id=pid,
            wave_num=0,
            status="running",
        )
        db.session.add(stale_run)
        db.session.commit()
        # Backdate updated_at to simulate a run that's been stuck for a long time
        stale_run.updated_at = datetime.now(timezone.utc) - timedelta(seconds=3600)
        db.session.commit()
        run_id = str(stale_run.id)

    # Call reset-stale via worker auth header (no auth key set → passes in test)
    r = client.post("/api/worker/ship-run/reset-stale", json={"max_age_seconds": 1800})
    assert r.status_code == 200
    data = r.get_json()
    assert data["reset"] >= 1

    with client.application.app_context():
        reset_run = db.session.get(ShipRun, run_id)
        assert reset_run.status == "queued", f"Expected queued, got {reset_run.status}"
