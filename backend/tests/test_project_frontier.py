import os
import subprocess
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch


def test_project_frontier_helpers_require_explicit_frontier(app):
    with app.app_context():
        from api.services.project_service import (
            get_project_frontier_id,
            project_has_frontier,
        )

        project = SimpleNamespace(
            accepted_frontier_id=None,
            shipped_frontier="f" * 40,
            project_path="/tmp/example",
        )

        assert project_has_frontier(project) is False
        assert get_project_frontier_id(project) is None


def test_validate_project_frontier_candidate_requires_non_empty_value(app):
    with app.app_context():
        from api.services.project_service import validate_project_frontier_candidate

        project = SimpleNamespace(git_mode="swarm")

        valid, error = validate_project_frontier_candidate(project, "   ")
        assert valid is False
        assert "required" in error


def test_validate_project_frontier_candidate_rejects_invalid_shape(app):
    with app.app_context():
        from api.services.project_service import validate_project_frontier_candidate

        project = SimpleNamespace(git_mode="swarm")

        valid, error = validate_project_frontier_candidate(project, "bad frontier")
        assert valid is False
        assert "AgentHub frontier id" in error


def test_validate_project_frontier_candidate_accepts_explicit_leaf_id(app):
    with app.app_context():
        from api.services.project_service import validate_project_frontier_candidate

        project = SimpleNamespace(git_mode="swarm")

        valid, error = validate_project_frontier_candidate(project, "leaf_01HZX3ABCD9EF0123456789XYZ")
        assert valid is True
        assert error is None


def test_create_project_accepts_explicit_frontier_id(client):
    frontier_id = "leaf_01HZX3ABCD9EF0123456789XYZ"

    response = client.post(
        "/api/projects",
        json={
            "name": "frontier-project",
            "accepted_frontier_id": frontier_id,
            "is_existing_repo": True,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["accepted_frontier_id"] == frontier_id
    assert payload["frontier_warning"] is None

    detail = client.get(f"/api/projects/{payload['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["accepted_frontier_id"] == frontier_id


def test_create_project_does_not_infer_frontier_from_local_checkout(client):
    with patch("api.routes._read_local_git_tip") as read_local_tip:
        response = client.post(
            "/api/projects",
            json={
                "name": "local-import",
                "project_path": "/tmp/local-import",
                "execution_mode": "local",
                "is_existing_repo": True,
            },
        )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["accepted_frontier_id"] is None
    assert payload["frontier_warning"]
    read_local_tip.assert_not_called()


def test_project_migration_status_reports_missing_frontier_and_ticket_bases(client):
    create = client.post(
        "/api/projects",
        json={
            "name": "migration-status",
            "project_path": "/tmp/migration-status",
            "execution_mode": "local",
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    from models.db import Ticket, TicketAttempt, db
    with client.application.app_context():
        missing_base_ticket = Ticket(
            project_id=project_id,
            column_id="backlog",
            title="Missing base",
            intent_status="ready",
            base_leaf_id=None,
        )
        stale_ticket = Ticket(
            project_id=project_id,
            column_id="backlog",
            title="Stale ticket",
            intent_status="ready",
            base_leaf_id="leaf_01HZX3STALEBASE0123456789AB",
        )
        db.session.add_all([missing_base_ticket, stale_ticket])
        db.session.flush()
        attempt = TicketAttempt(
            project_id=project_id,
            ticket_id=stale_ticket.id,
            agenthub_commit_hash="leaf_01HZX3ATTEMPT0123456789ABCD",
            base_hash=None,
            attempt_num=1,
            status="accepted",
        )
        db.session.add(attempt)
        db.session.commit()
        missing_base_ticket_id = str(missing_base_ticket.id)
        stale_ticket_id = str(stale_ticket.id)
        attempt_id = str(attempt.id)

    response = client.get(f"/api/projects/{project_id}/migration/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["accepted_frontier_id"] is None
    assert payload["has_accepted_frontier"] is False
    assert payload["ticket_counts"]["missing_base_leaf_id"] == 1
    assert payload["ticket_counts"]["stale"] == 0
    assert payload["tickets_missing_base_leaf_ids"][0]["id"] == missing_base_ticket_id
    assert payload["attempt_counts"]["missing_base_hash"] == 1
    assert payload["attempt_counts"]["missing_parent_leaf_id"] == 1
    assert payload["attempts_missing_lineage"][0]["id"] == attempt_id
    assert payload["local_path"]["path"] == "/tmp/migration-status"
    assert payload["local_path"]["exists"] is False
    assert payload["local_path"]["is_directory"] is False
    assert stale_ticket_id not in {item["id"] for item in payload["stale_tickets"]}


def test_project_migration_status_reports_stale_tickets_when_frontier_present(client):
    frontier_id = "leaf_01HZX3CURRENT0123456789ABCDEF"
    create = client.post(
        "/api/projects",
        json={
            "name": "migration-stale",
            "accepted_frontier_id": frontier_id,
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    from models.db import Ticket, db
    with client.application.app_context():
        stale_ticket = Ticket(
            project_id=project_id,
            column_id="backlog",
            title="Stale",
            intent_status="ready",
            base_leaf_id="leaf_01HZX3OLD0123456789ABCDEFGHI",
        )
        db.session.add(stale_ticket)
        db.session.commit()
        ticket_id = str(stale_ticket.id)

    response = client.get(f"/api/projects/{project_id}/migration/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ticket_counts"]["stale"] == 1
    assert payload["stale_tickets"][0]["id"] == ticket_id


def test_project_migration_set_frontier_requires_explicit_valid_id_and_updates_project(client):
    create = client.post(
        "/api/projects",
        json={"name": "set-frontier", "is_existing_repo": True},
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    invalid = client.post(
        f"/api/projects/{project_id}/migration/set-frontier",
        json={"accepted_frontier_id": "bad frontier"},
    )
    assert invalid.status_code == 400
    assert "accepted_frontier_id" in invalid.get_json()["error"]

    response = client.post(
        f"/api/projects/{project_id}/migration/set-frontier",
        json={"accepted_frontier_id": "leaf_01HZX3REPAIR0123456789ABCDE"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["project"]["accepted_frontier_id"] == "leaf_01HZX3REPAIR0123456789ABCDE"

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    assert detail.get_json()["accepted_frontier_id"] == "leaf_01HZX3REPAIR0123456789ABCDE"


def test_project_migration_backfill_ticket_bases_dry_run_reports_without_writing(client):
    frontier_id = "leaf_01HZX3BACKFILL0123456789ABCD"
    create = client.post(
        "/api/projects",
        json={
            "name": "backfill-dry-run",
            "accepted_frontier_id": frontier_id,
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    from models.db import Ticket, db
    with client.application.app_context():
        missing = Ticket(project_id=project_id, column_id="backlog", title="Missing", base_leaf_id=None)
        present = Ticket(project_id=project_id, column_id="backlog", title="Present", base_leaf_id="leaf_01HZX3EXISTING0123456789AB")
        db.session.add_all([missing, present])
        db.session.commit()
        missing_id = str(missing.id)

    response = client.post(
        f"/api/projects/{project_id}/migration/backfill-ticket-bases",
        json={"dry_run": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dry_run"] is True
    assert payload["updated_count"] == 1
    assert payload["tickets_to_update"] == [{"id": missing_id, "base_leaf_id": frontier_id}]

    detail = client.get(f"/api/projects/{project_id}/tickets/{missing_id}")
    assert detail.status_code == 200
    assert detail.get_json()["base_leaf_id"] is None


def test_project_migration_backfill_ticket_bases_apply_writes_missing_bases(client):
    frontier_id = "leaf_01HZX3BACKFILLAPPLY012345678"
    create = client.post(
        "/api/projects",
        json={
            "name": "backfill-apply",
            "accepted_frontier_id": frontier_id,
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    from models.db import Ticket, db
    with client.application.app_context():
        missing = Ticket(project_id=project_id, column_id="backlog", title="Missing", base_leaf_id=None)
        present = Ticket(project_id=project_id, column_id="backlog", title="Present", base_leaf_id="leaf_01HZX3EXISTING0123456789AB")
        db.session.add_all([missing, present])
        db.session.commit()
        missing_id = str(missing.id)

    response = client.post(
        f"/api/projects/{project_id}/migration/backfill-ticket-bases",
        json={"dry_run": False},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dry_run"] is False
    assert payload["updated_count"] == 1
    assert payload["tickets_to_update"] == [{"id": missing_id, "base_leaf_id": frontier_id}]

    detail = client.get(f"/api/projects/{project_id}/tickets/{missing_id}")
    assert detail.status_code == 200
    assert detail.get_json()["base_leaf_id"] == frontier_id


def test_project_migration_actions_do_not_use_local_git_head_fallback(client):
    create = client.post(
        "/api/projects",
        json={
            "name": "no-local-head-fallback",
            "project_path": "/tmp/no-local-head-fallback",
            "execution_mode": "local",
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    with patch("api.routes._read_local_git_tip") as read_local_tip:
        status_response = client.get(f"/api/projects/{project_id}/migration/status")
        set_response = client.post(
            f"/api/projects/{project_id}/migration/set-frontier",
            json={"accepted_frontier_id": "leaf_01HZX3NOFALLBACK0123456789AB"},
        )
        backfill_response = client.post(
            f"/api/projects/{project_id}/migration/backfill-ticket-bases",
            json={"dry_run": True},
        )

    assert status_response.status_code == 200
    assert set_response.status_code == 200
    assert backfill_response.status_code == 200
    read_local_tip.assert_not_called()


def _make_import_repo(tmp_path):
    repo = tmp_path / "import-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True, text=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "README.md").write_text("base\ndirty\n", encoding="utf-8")
    (repo / "UNTRACKED.txt").write_text("new\n", encoding="utf-8")
    return repo, head, branch


def test_import_agenthub_root_sets_accepted_frontier_id_and_returns_git_metadata(client, tmp_path):
    repo, head, branch = _make_import_repo(tmp_path)
    create = client.post(
        "/api/projects",
        json={
            "name": "import-target",
            "project_path": str(repo),
            "execution_mode": "local",
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    push_response = SimpleNamespace(
        status_code=200,
        text='{"ok":true}',
        raise_for_status=lambda: None,
    )
    missing_receipt_response = SimpleNamespace(
        status_code=404,
        raise_for_status=lambda: None,
    )
    receipt_response = SimpleNamespace(
        status_code=200,
        raise_for_status=lambda: None,
        json=lambda: {
            "hash": head,
            "exists": True,
            "is_leaf": True,
            "bundle_fetchable": True,
            "parents": [],
        },
    )

    with patch.dict(
        os.environ,
        {"AGENTHUB_URL": "http://agenthub:8088", "AGENTHUB_API_KEY": "secret"},
        clear=False,
    ):
        service = import_module("api.services.agenthub_import_service")
        with patch.object(service.requests, "post", return_value=push_response) as post_mock:
            with patch.object(
                service.requests,
                "get",
                side_effect=[missing_receipt_response, receipt_response],
            ) as get_mock:
                response = client.post(f"/api/projects/{project_id}/import-agenthub-root", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["project"]["accepted_frontier_id"] == head
    assert payload["import_result"]["accepted_frontier_id"] == head
    assert payload["import_result"]["path"] == str(repo.resolve())
    assert payload["import_result"]["git"]["head_sha"] == head
    assert payload["import_result"]["git"]["branch"] == branch
    assert payload["import_result"]["git"]["is_dirty"] is True
    assert payload["import_result"]["git"]["has_untracked"] is True
    assert payload["import_result"]["git"]["is_git_repo"] is True
    assert payload["import_result"]["agenthub_receipt"]["hash"] == head
    assert post_mock.called
    assert get_mock.called

    detail = client.get(f"/api/projects/{project_id}")
    assert detail.status_code == 200
    assert detail.get_json()["accepted_frontier_id"] == head


def test_import_agenthub_root_requires_project_path(client):
    create = client.post(
        "/api/projects",
        json={"name": "missing-import-path", "is_existing_repo": True},
    )
    assert create.status_code == 201
    project_id = create.get_json()["id"]

    response = client.post(f"/api/projects/{project_id}/import-agenthub-root", json={})

    assert response.status_code == 400
    assert "project_path" in response.get_json()["error"]


def test_create_ticket_defaults_base_leaf_id_from_project_frontier(client):
    frontier_id = "leaf_01HZX3ABCD9EF0123456789XYZ"
    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-frontier-default",
            "git_mode": "swarm",
            "accepted_frontier_id": frontier_id,
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/tickets",
        json={
            "title": "Default base leaf",
            "column_id": "backlog",
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["base_leaf_id"] == frontier_id

    detail = client.get(f"/api/projects/{project_id}/tickets/{payload['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["base_leaf_id"] == frontier_id


def test_create_ticket_accepts_explicit_base_leaf_id(client):
    project_response = client.post(
        "/api/projects",
        json={
            "name": "ticket-explicit-base",
            "git_mode": "swarm",
            "accepted_frontier_id": "leaf_01HZX3PROJECTDEFAULT01234567",
            "is_existing_repo": True,
        },
    )
    assert project_response.status_code == 201
    project_id = project_response.get_json()["id"]
    explicit_base_leaf_id = "leaf_01HZX3TICKETBASE0123456789ABC"

    response = client.post(
        f"/api/projects/{project_id}/tickets",
        json={
            "title": "Explicit base leaf",
            "column_id": "backlog",
            "base_leaf_id": explicit_base_leaf_id,
        },
    )

    assert response.status_code == 201
    assert response.get_json()["base_leaf_id"] == explicit_base_leaf_id


def test_create_ticket_requires_explicit_base_leaf_when_project_has_no_frontier(client):
    project_response = client.post(
        "/api/projects",
        json={
            "name": "ticket-missing-base",
            "git_mode": "swarm",
            "is_existing_repo": True,
        },
    )
    assert project_response.status_code == 201
    project_id = project_response.get_json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/tickets",
        json={
            "title": "Missing base leaf",
            "column_id": "backlog",
        },
    )

    assert response.status_code == 400
    assert "base_leaf_id is required for swarm projects" in response.get_json()["error"]


def test_run_ticket_fails_clearly_when_swarm_ticket_has_no_base_leaf_id(client):
    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-run-missing-base",
            "git_mode": "swarm",
            "github_url": "https://github.com/example/repo",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    client.put(
        f"/api/projects/{project_id}/graph",
        json={"nodes": [{"id": "node-1", "label": "Node 1"}], "edges": []},
    )

    from models.db import Ticket, db
    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="backlog",
            title="Legacy ticket without base",
            intent_status="ready",
            base_leaf_id=None,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    with patch("api.routes.check_execution_readiness", return_value=(True, [])):
        response = client.patch(
            f"/api/projects/{project_id}/tickets/{ticket_id}",
            json={"column_id": "in_progress"},
        )

    assert response.status_code == 400
    assert "base_leaf_id is required for swarm projects" in response.get_json()["error"]


def test_ticket_detail_reports_stale_status_against_accepted_frontier(client):
    from models.db import Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-stale-status",
            "git_mode": "swarm",
            "accepted_frontier_id": "leaf_01HZX3CURRENTFRONTIER01234567",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="backlog",
            title="Stale ticket",
            intent_status="ready",
            base_leaf_id="leaf_01HZX3STALETICKETBASE012345678",
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    response = client.get(f"/api/projects/{project_id}/tickets/{ticket_id}")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["stale"] is True
    assert payload["accepted_frontier_id"] == "leaf_01HZX3CURRENTFRONTIER01234567"
    assert "differs from project.accepted_frontier_id" in payload["stale_reason"]


def test_ticket_rerun_from_current_frontier_updates_base_and_enqueues_job(client):
    from models.db import AgentJob, Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-rerun-current-frontier",
            "git_mode": "swarm",
            "github_url": "https://github.com/example/repo",
            "accepted_frontier_id": "leaf_01HZX3CURRENTFRONTIER01234567",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    client.put(
        f"/api/projects/{project_id}/graph",
        json={"nodes": [{"id": "node-1", "label": "Node 1"}], "edges": []},
    )

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="done",
            title="Needs rerun",
            intent_status="active",
            base_leaf_id="leaf_01HZX3OLDTICKETBASE0123456789",
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    response = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/rerun-from-current-frontier",
        json={},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["base_leaf_id"] == "leaf_01HZX3CURRENTFRONTIER01234567"
    assert payload["accepted_frontier_id"] == "leaf_01HZX3CURRENTFRONTIER01234567"
    assert payload["column_id"] == "in_progress"
    assert payload["intent_status"] == "active"
    assert payload["stale"] is False

    with client.application.app_context():
        stored_ticket = db.session.get(Ticket, ticket_id)
        jobs = AgentJob.query.filter_by(ticket_id=ticket_id).all()
        assert stored_ticket.base_leaf_id == "leaf_01HZX3CURRENTFRONTIER01234567"
        assert stored_ticket.column_id == "in_progress"
        assert len(jobs) == 1
        assert jobs[0].status == "pending"


def test_ticket_rerun_from_current_frontier_fails_when_project_frontier_missing(client):
    from models.db import Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-rerun-no-frontier",
            "git_mode": "swarm",
            "github_url": "https://github.com/example/repo",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="done",
            title="Missing frontier",
            intent_status="active",
            base_leaf_id="leaf_01HZX3OLDTICKETBASE0123456789",
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    response = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/rerun-from-current-frontier",
        json={},
    )

    assert response.status_code == 409
    assert "accepted_frontier_id is not set" in response.get_json()["error"]
