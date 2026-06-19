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
    assert payload["source_type"] == "agenthub_leaf"
    assert payload["github_ref"] is None
    assert payload["github_resolved_sha"] is None

    detail = client.get(f"/api/projects/{payload['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["accepted_frontier_id"] == frontier_id


def test_project_workflow_file_round_trips_through_create_and_update(client):
    response = client.post(
        "/api/projects",
        json={
            "name": "workflow-project",
            "workflow_file": "config/workflow.json",
            "is_existing_repo": True,
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["workflow_file"] == "config/workflow.json"

    detail = client.get(f"/api/projects/{payload['id']}")
    assert detail.status_code == 200
    assert detail.get_json()["workflow_file"] == "config/workflow.json"

    update = client.put(
        f"/api/projects/{payload['id']}",
        json={"workflow_file": "ops/workflows/handoff.json"},
    )
    assert update.status_code == 200
    assert update.get_json()["workflow_file"] == "ops/workflows/handoff.json"


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
    assert payload["source_type"] == "local_path"
    read_local_tip.assert_not_called()


def test_create_github_project_imports_agenthub_and_persists_source_metadata(client):
    with patch("api.routes._import_github_project_to_agenthub") as import_mock:
        def _apply_import(project, *, github_url, github_ref):
            project.accepted_frontier_id = "leaf_01HZX3GITHUB0123456789ABCDE"
            project.github_resolved_sha = "a" * 40
            project.source_type = "github"
            return {
                "github_url": github_url,
                "github_ref": github_ref,
                "github_resolved_sha": "a" * 40,
                "accepted_frontier_id": "leaf_01HZX3GITHUB0123456789ABCDE",
            }

        import_mock.side_effect = _apply_import
        response = client.post(
            "/api/projects",
            json={
                "name": "github-import",
                "github_url": "https://github.com/example/repo",
                "base_ref": "release/1.0",
                "import_to_agenthub": True,
                "is_existing_repo": True,
            },
        )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["source_type"] == "github"
    assert payload["project_path"] is None
    assert payload["github_url"] == "https://github.com/example/repo"
    assert payload["github_ref"] == "release/1.0"
    assert payload["github_resolved_sha"] == "a" * 40
    assert payload["accepted_frontier_id"] == "leaf_01HZX3GITHUB0123456789ABCDE"
    assert payload["frontier_warning"] is None
    assert payload["import_result"]["github_ref"] == "release/1.0"
    import_mock.assert_called_once()

    detail = client.get(f"/api/projects/{payload['id']}")
    assert detail.status_code == 200
    detail_payload = detail.get_json()
    assert detail_payload["source_type"] == "github"
    assert detail_payload["github_ref"] == "release/1.0"
    assert detail_payload["github_resolved_sha"] == "a" * 40


def test_create_github_project_defaults_ref_to_main_when_missing(client):
    with patch("api.routes._import_github_project_to_agenthub") as import_mock:
        def _apply_import(project, *, github_url, github_ref):
            project.accepted_frontier_id = "leaf_01HZX3MAIN0123456789ABCDEF"
            project.github_resolved_sha = "b" * 40
            return {
                "github_url": github_url,
                "github_ref": github_ref,
                "github_resolved_sha": "b" * 40,
                "accepted_frontier_id": "leaf_01HZX3MAIN0123456789ABCDEF",
            }

        import_mock.side_effect = _apply_import
        response = client.post(
            "/api/projects",
            json={
                "name": "github-main-default",
                "github_url": "https://github.com/example/repo",
                "import_to_agenthub": True,
                "is_existing_repo": True,
            },
        )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["github_ref"] == "main"
    import_mock.assert_called_once()
    assert import_mock.call_args.kwargs["github_ref"] == "main"


def test_agenthub_github_import_uses_repository_url_and_base_ref(app):
    response = SimpleNamespace(
        status_code=201,
        text='{"leaf_id":"a"}',
        json=lambda: {
            "leaf_id": "a" * 40,
            "resolved_commit_sha": "b" * 40,
            "repository_url": "https://github.com/example/repo",
            "requested_ref": "main",
        },
    )

    with app.app_context():
        with patch.dict(
            os.environ,
            {"AGENTHUB_URL": "http://agenthub:8088", "AGENTHUB_API_KEY": "secret"},
            clear=False,
        ):
            service = import_module("api.services.agenthub_import_service")
            project = SimpleNamespace(git_mode="swarm")
            with patch.object(service.requests, "post", return_value=response) as post_mock:
                result = service.import_github_project_to_agenthub(
                    project,
                    github_url="https://github.com/example/repo",
                    github_ref="main",
                )

    post_mock.assert_called_once_with(
        "http://agenthub:8088/api/git/import/github",
        headers={"Authorization": "Bearer secret"},
        json={
            "repository_url": "https://github.com/example/repo",
            "base_ref": "main",
        },
        timeout=120,
    )
    assert result["accepted_frontier_id"] == "a" * 40
    assert result["github_resolved_sha"] == "b" * 40
    assert project.github_url == "https://github.com/example/repo"
    assert project.github_ref == "main"
    assert project.github_resolved_sha == "b" * 40
    assert project.accepted_frontier_id == "a" * 40


def test_project_doctor_reports_github_first_project_without_local_path(client):
    response = client.post(
        "/api/projects",
        json={
            "name": "doctor-github",
            "github_url": "https://github.com/example/repo",
            "base_ref": "main",
            "accepted_frontier_id": "leaf_01HZX3DOCTOR0123456789ABCD",
            "is_existing_repo": True,
        },
    )
    assert response.status_code == 201
    project_id = response.get_json()["id"]

    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="done",
            title="Operator status",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=project_id,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="leaf_01HZX3DOCTOR0123456789ABCD",
            attempt_num=1,
            status="accepted",
            summary="Healthy attempt",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)

    with patch.dict(
        os.environ,
        {
            "AGENTHUB_URL": "https://agenthub.example",
            "AGENTHUB_API_KEY": "test-key",
            "GITHUB_TOKEN": "ghs_test",
            "MEMORY_EMBEDDING_MODEL": "text-embedding-3-small",
            "OPENAI_API_KEY": "sk-test",
        },
        clear=False,
    ):
        doctor = client.get(f"/api/projects/{project_id}/doctor")

    assert doctor.status_code == 200
    payload = doctor.get_json()
    assert payload["source_type"] == "github"
    assert payload["source_url"] == "https://github.com/example/repo"
    assert payload["source_ref"] == "main"
    assert payload["project_path"] is None
    assert payload["accepted_frontier_id"] == "leaf_01HZX3DOCTOR0123456789ABCD"
    assert payload["accepted_frontier_hash"] == "leaf_01HZX3DOCTOR0123456789ABCD"
    assert payload["root_hash"] == "leaf_01HZX3DOCTOR0123456789ABCD"
    assert payload["latest_attempt"]["ticket_id"] == ticket_id
    assert payload["latest_attempt"]["stale"] is False
    assert payload["execution_readiness"]["ready"] is True
    assert payload["execution_readiness"]["issues"] == []
    assert "No pending or running jobs." in payload["execution_readiness"]["observations"]


def test_create_github_project_agenthub_failure_does_not_persist_project(client):
    from api.routes import _AgenthubImportError
    from models.db import Project

    with patch(
        "api.routes._import_github_project_to_agenthub",
        side_effect=_AgenthubImportError("agenthub import failed"),
    ):
        response = client.post(
            "/api/projects",
            json={
                "name": "github-import-fail",
                "github_url": "https://github.com/example/repo",
                "import_to_agenthub": True,
                "is_existing_repo": True,
            },
        )

    assert response.status_code == 422
    assert "agenthub import failed" in response.get_json()["error"]
    with client.application.app_context():
        assert Project.query.filter_by(name="github-import-fail").count() == 0


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
    assert "No AgentHub frontier/base available for ticket dispatch" in response.get_json()["error"]


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


def test_ticket_complete_records_attempt_with_frontier_lineage_and_latest_attempt_not_stale(client):
    from models.db import Ticket, TicketAttempt, db

    frontier_id = "leaf_01HZX3CURRENTFRONTIER01234567"
    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-complete-current-frontier",
            "git_mode": "swarm",
            "accepted_frontier_id": frontier_id,
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="in_progress",
            title="Fresh frontier ticket",
            intent_status="active",
            base_leaf_id=frontier_id,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    complete = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/complete",
        json={
            "commit_hash": "a" * 40,
            "base_hash": frontier_id,
            "agent_id": "agenthub-worker-1",
            "summary": "automatic completion",
        },
    )
    assert complete.status_code == 200
    assert complete.get_json()["attempt_created"] is True

    with client.application.app_context():
        attempt = TicketAttempt.query.filter_by(ticket_id=ticket_id).one()
        assert attempt.base_hash == frontier_id
        assert attempt.agenthub_commit_hash == "a" * 40
        assert attempt.agent_id == "agenthub-worker-1"

    detail = client.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
    assert detail.status_code == 200
    payload = detail.get_json()
    assert payload["accepted_frontier_id"] == frontier_id
    assert payload["latest_attempt"]["attempt_num"] == 1
    assert payload["latest_attempt"]["status"] == "validated"
    assert payload["latest_attempt"]["validated"] is True
    assert payload["latest_attempt"]["integrated"] is False
    assert payload["latest_attempt"]["stale"] is False
    assert payload["latest_attempt"]["stale_reason"] is None


def test_ticket_complete_is_idempotent_for_matching_swarm_attempt_payload(client, project):
    from models.db import Ticket, TicketAttempt, db

    frontier_id = project["accepted_frontier_id"]
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="in_progress",
            title="Idempotent completion ticket",
            intent_status="active",
            base_leaf_id=frontier_id,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    payload = {
        "commit_hash": "b" * 40,
        "base_hash": frontier_id,
        "agent_id": "agenthub-worker-2",
        "summary": "automatic completion",
    }
    first = client.post(f"/api/projects/{project['id']}/tickets/{ticket_id}/complete", json=payload)
    second = client.post(f"/api/projects/{project['id']}/tickets/{ticket_id}/complete", json=payload)

    assert first.status_code == 200
    assert first.get_json()["attempt_created"] is True
    assert second.status_code == 200
    assert second.get_json()["attempt_created"] is False
    assert second.get_json()["attempt_id"] == first.get_json()["attempt_id"]

    with client.application.app_context():
        attempts = TicketAttempt.query.filter_by(ticket_id=ticket_id).all()
        assert len(attempts) == 1


def test_ticket_rerun_from_current_frontier_uses_ticket_default_attempt_count_when_omitted(client):
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
            default_attempt_count=4,
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
    assert payload["default_attempt_count"] == 4
    assert payload["attempt_count"] == 4
    assert payload["job_count"] == 4
    assert len(payload["job_ids"]) == 4

    with client.application.app_context():
        stored_ticket = db.session.get(Ticket, ticket_id)
        jobs = AgentJob.query.filter_by(ticket_id=ticket_id).order_by(AgentJob.created_at.asc()).all()
        assert stored_ticket.base_leaf_id == "leaf_01HZX3CURRENTFRONTIER01234567"
        assert stored_ticket.column_id == "in_progress"
        assert len(jobs) == 4
        assert all(job.status == "pending" for job in jobs)
        assert len({job.attempt_metadata["attempt_batch_id"] for job in jobs}) == 1
        assert [job.attempt_metadata["attempt_index"] for job in jobs] == [1, 2, 3, 4]
        assert [job.attempt_metadata["attempt_count"] for job in jobs] == [4, 4, 4, 4]
        assert [job.attempt_metadata["attempt_strategy"] for job in jobs] == [
            "conservative-minimalist",
            "test-first-verifier",
            "architecture-cleanup",
            "performance-simplicity",
        ]


def test_ticket_rerun_from_current_frontier_enqueues_explicit_parallel_jobs(client):
    from models.db import AgentJob, Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-rerun-parallel-frontier",
            "git_mode": "swarm",
            "github_url": "https://github.com/example/repo",
            "accepted_frontier_id": "leaf_01HZX3PARALLELFRONTIER123456",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="done",
            title="Needs parallel rerun",
            intent_status="active",
            base_leaf_id="leaf_01HZX3OLDTICKETBASE0123456789",
            default_attempt_count=4,
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    response = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/rerun-from-current-frontier",
        json={"attempt_count": 2},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["base_leaf_id"] == "leaf_01HZX3PARALLELFRONTIER123456"
    assert payload["default_attempt_count"] == 4
    assert payload["attempt_count"] == 2
    assert payload["job_count"] == 2
    assert len(payload["job_ids"]) == 2
    assert "competing attempts" in payload["message"]

    with client.application.app_context():
        jobs = AgentJob.query.filter_by(ticket_id=ticket_id).order_by(AgentJob.created_at.asc()).all()
        assert len(jobs) == 2
        assert all(job.status == "pending" for job in jobs)
        assert [job.attempt_metadata["attempt_strategy"] for job in jobs] == [
            "conservative-minimalist",
            "test-first-verifier",
        ]


def test_ticket_rerun_from_current_frontier_validates_attempt_count(client):
    from api.services.ticket_service import MAX_PARALLEL_ATTEMPTS
    from models.db import Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "ticket-rerun-invalid-attempt-count",
            "git_mode": "swarm",
            "github_url": "https://github.com/example/repo",
            "accepted_frontier_id": "leaf_01HZX3INVALIDCOUNT123456789",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="done",
            title="Bad attempt count",
            intent_status="active",
            base_leaf_id="leaf_01HZX3OLDTICKETBASE0123456789",
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = str(ticket.id)

    too_small = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/rerun-from-current-frontier",
        json={"attempt_count": 0},
    )
    assert too_small.status_code == 400
    assert too_small.get_json()["error"] == "attempt_count must be at least 1"

    too_large = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/rerun-from-current-frontier",
        json={"attempt_count": MAX_PARALLEL_ATTEMPTS + 1},
    )
    assert too_large.status_code == 400
    assert too_large.get_json()["error"] == (
        f"attempt_count must be at most {MAX_PARALLEL_ATTEMPTS}"
    )

    not_integer = client.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/rerun-from-current-frontier",
        json={"attempt_count": 1.5},
    )
    assert not_integer.status_code == 400
    assert not_integer.get_json()["error"] == "attempt_count must be an integer"


def test_enqueue_ticket_job_duplicate_guard_still_skips_existing_pending_job(client):
    from api.services.ticket_service import enqueue_ticket_job
    from models.db import AgentJob, Ticket, db

    create_project = client.post(
        "/api/projects",
        json={
            "name": "duplicate-guard-project",
            "git_mode": "swarm",
            "github_url": "https://github.com/example/repo",
            "accepted_frontier_id": "leaf_01HZX3DUPLICATEGUARD1234567",
            "is_existing_repo": True,
        },
    )
    assert create_project.status_code == 201
    project_id = create_project.get_json()["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=project_id,
            column_id="in_progress",
            title="Duplicate guard ticket",
            intent_status="active",
            base_leaf_id="leaf_01HZX3DUPLICATEGUARD1234567",
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = ticket.id

        first_job = enqueue_ticket_job(ticket_id)
        second_job = enqueue_ticket_job(ticket_id)

        jobs = AgentJob.query.filter_by(ticket_id=ticket_id).all()
        assert first_job is not None
        assert second_job is None
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
