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
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="T",
            intent_status="active",
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


def test_ticket_complete_creates_no_pr_row(client, project):
    """Plan 12.2: no PR row created for swarm ticket completion — PR model no longer exists."""
    from models.db import db, Ticket
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="T",
            intent_status="active",
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


# ---------------------------------------------------------------------------
# 12.2d  Worker fail endpoint records compose_failed
# ---------------------------------------------------------------------------

def test_worker_fail_records_compose_failed(client, project):
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


def test_multi_dependency_ticket_waits_for_temporary_base(client, project):
    """Multiple unshipped dependency leaves queue temporary base composition before dispatch."""
    from models.db import db, Ticket, TicketAttempt, CompositeWorkspace
    pid = project["id"]

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
    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "queued"
        workspace = CompositeWorkspace.query.filter_by(
            project_id=pid,
            created_by="dependency_base_composer",
            status="queued",
        ).one()
        assert workspace.selected_leaf_hashes == ["a" * 40, "b" * 40]
        assert workspace.selected_attempt_ids
        assert workspace.base_root_hash is None


def test_multi_dependency_ticket_dispatches_after_temporary_base_composes(client, project):
    """A composed temporary base becomes the child's base hash and unblocks dispatch."""
    from models.db import db, Ticket, TicketAttempt, CompositeWorkspace, AgentJob
    from api.services.job_service import job_to_response

    pid = project["id"]
    client.put(f"/api/projects/{pid}", json={"github_url": "https://github.com/owner/repo"})

    with client.application.app_context():
        parent_a = Ticket(project_id=pid, column_id="done", title="Mock schema migration", intent_status="active")
        parent_b = Ticket(project_id=pid, column_id="done", title="Mock API contract", intent_status="active")
        db.session.add_all([parent_a, parent_b])
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="queued",
            title="Mock integration consumer",
            intent_status="ready",
            depends_on_ticket_ids=[str(parent_a.id), str(parent_b.id)],
        )
        db.session.add(child)
        db.session.flush()
        db.session.add_all([
            TicketAttempt(
                project_id=pid,
                ticket_id=parent_a.id,
                agenthub_commit_hash="1" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="mock schema leaf",
            ),
            TicketAttempt(
                project_id=pid,
                ticket_id=parent_b.id,
                agenthub_commit_hash="2" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="accepted",
                summary="mock API leaf",
            ),
        ])
        db.session.commit()
        child_id = str(child.id)

    from api.services.ticket_service import dispatch_unblocked_queued
    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "queued"
        workspace = CompositeWorkspace.query.filter_by(
            project_id=pid,
            created_by="dependency_base_composer",
        ).one()
        workspace_id = str(workspace.id)
        assert workspace.selected_leaf_hashes == ["1" * 40, "2" * 40]

    resp = client.post(
        f"/api/worker/workspaces/{workspace_id}/composed",
        json={
            "composed_commit_hash": "c" * 40,
            "test_status": "passed",
            "changed_files": ["backend/mock_schema.py", "backend/mock_api.py"],
        },
        headers=_worker_headers(),
    )
    assert resp.status_code == 200

    with client.application.app_context():
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "in_progress"
        job = AgentJob.query.filter_by(ticket_id=child_id, status="pending").one()
        payload = job_to_response(job)

    assert payload["base_hash"] == "c" * 40
    assert payload["base_selection"] == {
        "base_hash": "c" * 40,
        "base_source": "temporary_dependency_base",
        "dependency_parent_hashes": ["1" * 40, "2" * 40],
        "temporary_base_workspace_id": workspace_id,
        "temporary_base_status": "preview_ready",
        "temporary_base_required": True,
    }


def test_multi_dependency_ticket_dispatches_after_parents_ship(client, project):
    """Once all parent leaves are shipped into the frontier, no temporary base is needed."""
    from models.db import db, Ticket, TicketAttempt
    pid = project["id"]

    with client.application.app_context():
        parent_a = Ticket(project_id=pid, column_id="done", title="Parent A", intent_status="active")
        parent_b = Ticket(project_id=pid, column_id="done", title="Parent B", intent_status="active")
        db.session.add_all([parent_a, parent_b])
        db.session.flush()
        child = Ticket(
            project_id=pid,
            column_id="queued",
            title="Child needs shipped parents",
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
                status="shipped",
                summary="parent a",
            ),
            TicketAttempt(
                project_id=pid,
                ticket_id=parent_b.id,
                agenthub_commit_hash="b" * 40,
                base_hash="f" * 40,
                wave_num=0,
                attempt_num=1,
                status="shipped",
                summary="parent b",
            ),
        ])
        db.session.commit()
        child_id = str(child.id)

    from api.services.ticket_service import dispatch_unblocked_queued
    with client.application.app_context():
        dispatch_unblocked_queued(pid)
        child = db.session.get(Ticket, child_id)
        assert child.column_id == "in_progress"


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
