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
  - coordinator env forwarding keeps BASE_HASH and AGENTHUB_ROOT_HASH explicit
"""
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

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


def test_coordinator_job_to_env_forwards_base_leaf_id():
    from coordinator.coordinator import job_to_env

    env = job_to_env(
        {
            "project_id": "p1",
            "ticket_id": "t1",
            "repo_url": "https://github.com/org/repo",
            "job_id": "j1",
            "kind": "ticket",
            "base_leaf_id": "leaf_01HZX3BASE0123456789ABCDEFG",
            "base_hash": "b" * 40,
            "agenthub_root_hash": "f" * 40,
        },
        for_docker=False,
    )

    assert env["BASE_LEAF_ID"] == "leaf_01HZX3BASE0123456789ABCDEFG"
    assert env["BASE_HASH"] == "b" * 40
    assert env["AGENTHUB_ROOT_HASH"] == "f" * 40


def test_coordinator_job_to_env_does_not_use_project_path_for_local_swarm_jobs():
    from coordinator.coordinator import job_to_env

    env = job_to_env(
        {
            "project_id": "p1",
            "ticket_id": "t1",
            "repo_url": "https://github.com/org/repo",
            "job_id": "j1",
            "kind": "ticket",
            "execution_mode": "local",
            "git_mode": "swarm",
            "project_path": "/repo/local-checkout",
            "base_leaf_id": "leaf_01HZX3BASE0123456789ABCDEFG",
        },
        for_docker=False,
    )

    assert env["BASE_LEAF_ID"] == "leaf_01HZX3BASE0123456789ABCDEFG"
    assert "AGENT_WORKSPACE" not in env


def test_coordinator_job_to_env_does_not_use_shipped_frontier_as_root_fallback():
    from coordinator.coordinator import job_to_env

    env = job_to_env(
        {
            "project_id": "p1",
            "ticket_id": "t1",
            "repo_url": "https://github.com/org/repo",
            "job_id": "j1",
            "kind": "ticket",
            "shipped_frontier": "f" * 40,
        },
        for_docker=False,
    )

    assert "AGENTHUB_ROOT_HASH" not in env


def test_coordinator_job_to_env_uses_base_hash_as_root_fallback():
    from coordinator.coordinator import job_to_env

    env = job_to_env(
        {
            "project_id": "p1",
            "ticket_id": "t1",
            "repo_url": "https://github.com/org/repo",
            "job_id": "j1",
            "kind": "ticket",
            "base_hash": "b" * 40,
        },
        for_docker=False,
    )

    assert env["BASE_HASH"] == "b" * 40
    assert env["AGENTHUB_ROOT_HASH"] == "b" * 40


def test_job_to_response_uses_ticket_base_leaf_for_swarm_jobs(app, tmp_path):
    from api.services.job_service import job_to_response
    from models.db import AgentJob, Project, Ticket, db

    repo = tmp_path / "job-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    head_hash = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with app.app_context():
        project = Project(
            name="local-job-project",
            execution_mode="local",
            git_mode="swarm",
            project_path=str(repo),
            shipped_frontier="s" * 40,
        )
        db.session.add(project)
        db.session.flush()
        ticket = Ticket(
            project_id=project.id,
            column_id="queued",
            title="Local job",
            intent_status="ready",
            base_leaf_id="leaf_01HZX3TICKETBASE0123456789ABC",
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
        db.session.commit()

        payload = job_to_response(job)

    assert payload["base_leaf_id"] == "leaf_01HZX3TICKETBASE0123456789ABC"
    assert payload["base_hash"] == "leaf_01HZX3TICKETBASE0123456789ABC"
    assert payload["agenthub_root_hash"] == "leaf_01HZX3TICKETBASE0123456789ABC"
    assert payload["base_selection"]["base_source"] == "ticket_base_leaf"
    assert payload["project_path"] == str(repo)


def test_prepare_local_job_refuses_shipped_frontier_fallback():
    from agenthub_preflight import AgenthubPreflightError, prepare_local_job

    with pytest.raises(
        AgenthubPreflightError,
        match="requires ticket.base_leaf_id.*import.*rerun",
    ):
        prepare_local_job(
            {
                "execution_mode": "local",
                "git_mode": "swarm",
                "shipped_frontier": "f" * 40,
            },
            env={"AGENTHUB_URL": "http://agenthub:8088", "AGENTHUB_API_KEY": "secret"},
        )


def test_prepare_local_job_raises_when_agenthub_base_is_unfetchable():
    from agenthub_preflight import AgenthubPreflightError, prepare_local_job

    missing = MagicMock()
    missing.status_code = 200
    missing.raise_for_status.return_value = None
    missing.json.return_value = {"exists": False, "bundle_fetchable": False}

    with patch("backend.api.services.agenthub_import_service.requests.get", return_value=missing):
        with pytest.raises(AgenthubPreflightError, match="import.*rerun"):
            prepare_local_job(
                {
                    "execution_mode": "local",
                    "git_mode": "swarm",
                    "base_leaf_id": "leaf_01HZX3BASE0123456789ABCDEFG",
                },
                env={"AGENTHUB_URL": "http://agenthub:8088", "AGENTHUB_API_KEY": "secret"},
            )


def test_seed_commit_from_repo_is_disabled_for_runtime_paths():
    from agenthub_preflight import AgenthubPreflightError, seed_commit_from_repo

    with pytest.raises(AgenthubPreflightError, match="Automatic AgentHub seeding.*explicit project import/admin path"):
        seed_commit_from_repo("/tmp/repo", "a" * 40, "http://agenthub:8088", "secret")


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
    initial_frontier = project["accepted_frontier_id"]
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
            base_hash=initial_frontier,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="first try",
        )
        second = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="b" * 40,
            base_hash="a" * 40,
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


def test_accept_attempt_advances_project_accepted_frontier(client, project):
    pid = project["id"]
    initial_frontier = project["accepted_frontier_id"]
    from models.db import db, Project, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Advance frontier ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="d" * 40,
            base_hash=initial_frontier,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="advance accepted frontier",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    resp = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept"
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["status"] == "accepted"
    assert payload["accepted_frontier_id"] == "d" * 40
    assert payload["project"]["accepted_frontier_id"] == "d" * 40

    with client.application.app_context():
        stored = db.session.get(Project, pid)
        assert stored.accepted_frontier_id == "d" * 40
        assert stored.updated_at is not None


def test_accept_attempt_fails_clearly_without_agenthub_commit_hash(client, project):
    pid = project["id"]
    original_frontier = project["accepted_frontier_id"]
    from models.db import db, Project, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Missing commit hash ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=None,
            base_hash=original_frontier,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="missing commit hash",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    resp = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept"
    )

    assert resp.status_code == 409
    assert "no AgentHub leaf/commit id" in resp.get_json()["error"]

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_attempt = db.session.get(TicketAttempt, attempt_id)
        assert stored_project.accepted_frontier_id == original_frontier
        assert stored_attempt.status == "proposed"


def test_accept_attempt_does_not_fallback_to_local_git_head(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="No git head fallback ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="e" * 40,
            base_hash=project["accepted_frontier_id"],
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="must use stored leaf only",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    with patch("api.routes._read_local_git_tip") as read_local_tip:
        resp = client.post(
            f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept"
        )

    assert resp.status_code == 200
    assert resp.get_json()["accepted_frontier_id"] == "e" * 40
    read_local_tip.assert_not_called()


def test_attempt_list_reports_stale_status_against_accepted_frontier(client, project):
    pid = project["id"]
    from models.db import db, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Attempt stale status ticket",
            intent_status="active",
            base_leaf_id="leaf_01HZX3TICKETBASE0123456789ABC",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="7" * 40,
            base_hash="leaf_01HZX3STALEATTEMPTBASE01234567",
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="stale attempt listing",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)

    resp = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/attempts")

    assert resp.status_code == 200
    payload = resp.get_json()[0]
    assert payload["stale"] is True
    assert payload["accepted_frontier_id"] == project["accepted_frontier_id"]
    assert "differs from project.accepted_frontier_id" in payload["stale_reason"]


def test_accept_attempt_rejects_stale_attempt_and_does_not_advance_frontier(client, project):
    pid = project["id"]
    original_frontier = project["accepted_frontier_id"]
    from models.db import db, Project, Ticket, TicketAttempt

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Stale attempt ticket",
            intent_status="active",
            base_leaf_id="leaf_01HZX3STALETICKETBASE012345678",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="f" * 40,
            base_hash="leaf_01HZX3OLDATTEMPTBASE0123456789",
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="stale attempt",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    resp = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept"
    )

    assert resp.status_code == 409
    payload = resp.get_json()
    assert "stale" in payload["error"].lower()
    assert payload["accepted_frontier_id"] == original_frontier

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_attempt = db.session.get(TicketAttempt, attempt_id)
        assert stored_project.accepted_frontier_id == original_frontier
        assert stored_attempt.status == "proposed"


def test_accept_attempt_rejects_when_staleness_cannot_be_determined(client, project):
    pid = project["id"]
    from models.db import db, Project, Ticket, TicketAttempt

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_project.accepted_frontier_id = None
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Unknown staleness ticket",
            intent_status="active",
            base_leaf_id=None,
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="9" * 40,
            base_hash=None,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="unknown staleness",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    resp = client.post(
        f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept"
    )

    assert resp.status_code == 409
    payload = resp.get_json()
    assert "cannot determine attempt staleness" in payload["error"].lower()

    with client.application.app_context():
        stored_project = db.session.get(Project, pid)
        stored_attempt = db.session.get(TicketAttempt, attempt_id)
        assert stored_project.accepted_frontier_id is None
        assert stored_attempt.status == "proposed"


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


# ---------------------------------------------------------------------------
# 12.3h  Promotion candidates
# ---------------------------------------------------------------------------

def test_create_promotion_candidate_resolves_dependency_closure(client, project):
    pid = project["id"]
    frontier = "f" * 40

    resp = client.post(f"/api/projects/{pid}/frontier", json={
        "hash": frontier,
        "source": "test",
    })
    assert resp.status_code == 200

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        parent = Ticket(project_id=pid, column_id="done", title="Parent", intent_status="active")
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
            agenthub_commit_hash="p" * 40,
            base_hash=frontier,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="parent accepted",
        )
        child_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash="c" * 40,
            base_hash="p" * 40,
            wave_num=1,
            attempt_num=1,
            status="accepted",
            summary="child accepted",
        )
        db.session.add_all([parent_attempt, child_attempt])
        db.session.commit()
        child_attempt_id = str(child_attempt.id)

    create_resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [child_attempt_id]},
    )

    assert create_resp.status_code == 201
    data = create_resp.get_json()
    assert data["status"] == "valid"
    assert set(data["selected_attempt_ids"]) == set(data["validation_summary"]["seed_attempt_ids"] + data["validation_summary"]["auto_included_dependency_attempt_ids"])
    assert data["selected_leaf_hashes"] == ["c" * 40]
    assert data["base_root_hash"] == frontier
    assert data["validation_summary"]["blockers"] == []


def test_create_promotion_candidate_blocks_missing_dependency_attempt(client, project):
    pid = project["id"]
    frontier = "f" * 40

    resp = client.post(f"/api/projects/{pid}/frontier", json={
        "hash": frontier,
        "source": "test",
    })
    assert resp.status_code == 200

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        parent = Ticket(project_id=pid, column_id="queued", title="Parent", intent_status="ready")
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
        child_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=child.id,
            agenthub_commit_hash="c" * 40,
            base_hash=frontier,
            wave_num=1,
            attempt_num=1,
            status="accepted",
            summary="child accepted",
        )
        db.session.add(child_attempt)
        db.session.commit()
        child_attempt_id = str(child_attempt.id)

    create_resp = client.post(
        f"/api/projects/{pid}/ship/candidates",
        json={"selected_attempt_ids": [child_attempt_id]},
    )

    assert create_resp.status_code == 201
    data = create_resp.get_json()
    assert data["status"] == "blocked"
    assert "no accepted attempt" in (data["conflict_summary"] or "")

    list_resp = client.get(f"/api/projects/{pid}/ship/candidates?status=blocked")
    assert list_resp.status_code == 200
    candidates = list_resp.get_json()
    assert len(candidates) == 1

    detail_resp = client.get(f"/api/projects/{pid}/ship/candidates/{data['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.get_json()["id"] == data["id"]


def test_agenthub_client_receipt_and_events_methods():
    from utils.agenthub_client import AgenthubClient

    client = AgenthubClient("http://agenthub:8088", "secret")
    receipt_payload = {"hash": "a" * 40, "exists": True}
    event_payload = [{"channel_name": "ticket-123", "event_type": "attempt_published"}]
    doctor_payload = {"status": "ok", "checks": [{"name": "database", "ok": True}]}

    with patch.object(client, "_get", side_effect=[receipt_payload, event_payload, doctor_payload]) as mocked_get:
        assert client.receipt("a" * 40)["exists"] is True
        assert client.events(channel_prefix="ticket-", limit=10)[0]["event_type"] == "attempt_published"
        assert client.doctor()["status"] == "ok"

    assert mocked_get.call_args_list[0].args[0] == f"/api/git/receipts/{'a' * 40}"
    assert mocked_get.call_args_list[1].args[0] == "/api/events"
    assert mocked_get.call_args_list[1].kwargs == {"channel_prefix": "ticket-", "limit": 10}
    assert mocked_get.call_args_list[2].args[0] == "/api/doctor"


def test_agenthub_client_seed_not_supported_surfaces_server_message():
    from utils.agenthub_client import AgenthubClient, AgenthubError

    client = AgenthubClient("http://agenthub:8088", "secret")
    response = MagicMock(spec=requests.Response)
    response.status_code = 501
    response.text = '{"error":"server-side seed not yet supported"}'

    with patch.object(client._session, "post", return_value=response):
        with pytest.raises(AgenthubError, match="not yet supported"):
            client.seed("/tmp/repo", "a" * 40)
