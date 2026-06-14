from models.db import CompositeWorkspace, Project, PromotionCandidate, ShipRun, Ticket, TicketAttempt, db


def test_worker_ship_run_composed_echoes_runtime_metadata(client):
    with client.application.app_context():
        project = Project(
            name="ship-runtime-proj",
            git_mode="swarm",
            github_url="https://github.com/example/demo",
            accepted_frontier_id="f" * 40,
        )
        db.session.add(project)
        db.session.flush()

        ticket = Ticket(
            project_id=project.id,
            column_id="done",
            title="Ship runtime",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()

        attempt = TicketAttempt(
            project_id=project.id,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.flush()

        candidate = PromotionCandidate(
            project_id=project.id,
            selected_attempt_ids=[str(attempt.id)],
            selected_leaf_hashes=["a" * 40],
            base_root_hash="b" * 40,
            status="queued",
        )
        db.session.add(candidate)
        db.session.flush()

        run = ShipRun(
            project_id=project.id,
            promotion_candidate_id=candidate.id,
            wave_num=0,
            status="composing",
        )
        db.session.add(run)
        db.session.commit()

        run_id = str(run.id)

    response = client.post(f"/api/worker/ship-run/{run_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "base_main_hash": "d" * 40,
        "test_status": "passed",
        "changed_files": ["src/app.py"],
        "runtime": {
            "project_path": "/tmp/terarchitect/runtime/repo",
            "requested_project_path": None,
            "repo_source": "github_ephemeral_clone",
            "cache_source": "github_url",
            "ephemeral_repo": True,
        },
    })

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ready_to_ship"
    assert data["runtime"] == {
        "project_path": "/tmp/terarchitect/runtime/repo",
        "requested_project_path": None,
        "repo_source": "github_ephemeral_clone",
        "cache_source": "github_url",
        "ephemeral_repo": True,
    }


def test_worker_workspace_get_includes_github_url(client):
    with client.application.app_context():
        project = Project(
            name="workspace-runtime-proj",
            git_mode="swarm",
            github_url="https://github.com/example/demo",
            accepted_frontier_id="f" * 40,
        )
        db.session.add(project)
        db.session.flush()

        workspace = CompositeWorkspace(
            project_id=project.id,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="composing",
        )
        db.session.add(workspace)
        db.session.commit()
        workspace_id = str(workspace.id)

    response = client.get(f"/api/worker/workspaces/{workspace_id}")

    assert response.status_code == 200
    data = response.get_json()
    assert data["project"]["github_url"] == "https://github.com/example/demo"


def test_worker_workspace_composed_echoes_runtime_metadata(client):
    with client.application.app_context():
        project = Project(
            name="workspace-runtime-proj",
            git_mode="swarm",
            github_url="https://github.com/example/demo",
            accepted_frontier_id="f" * 40,
        )
        db.session.add(project)
        db.session.flush()

        workspace = CompositeWorkspace(
            project_id=project.id,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="composing",
        )
        db.session.add(workspace)
        db.session.commit()
        workspace_id = str(workspace.id)

    response = client.post(f"/api/worker/workspaces/{workspace_id}/composed", json={
        "composed_commit_hash": "c" * 40,
        "test_status": "passed",
        "changed_files": ["src/app.py"],
        "runtime": {
            "project_path": "/tmp/terarchitect/runtime/repo",
            "requested_project_path": None,
            "repo_source": "github_ephemeral_clone",
            "cache_source": "github_url",
            "ephemeral_repo": True,
        },
    })

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "preview_ready"
    assert data["runtime"] == {
        "project_path": "/tmp/terarchitect/runtime/repo",
        "requested_project_path": None,
        "repo_source": "github_ephemeral_clone",
        "cache_source": "github_url",
        "ephemeral_repo": True,
    }


def test_worker_workspace_fail_echoes_runtime_metadata(client):
    with client.application.app_context():
        project = Project(
            name="workspace-runtime-proj",
            git_mode="swarm",
            github_url="https://github.com/example/demo",
            accepted_frontier_id="f" * 40,
        )
        db.session.add(project)
        db.session.flush()

        workspace = CompositeWorkspace(
            project_id=project.id,
            selected_attempt_ids=[],
            selected_leaf_hashes=["a" * 40],
            status="composing",
        )
        db.session.add(workspace)
        db.session.commit()
        workspace_id = str(workspace.id)

    response = client.post(f"/api/worker/workspaces/{workspace_id}/fail", json={
        "error": "project_path not found",
        "failure_type": "no_project_path",
        "runtime": {
            "project_path": None,
            "requested_project_path": None,
            "repo_source": "unavailable",
            "cache_source": "none",
            "ephemeral_repo": False,
        },
    })

    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "conflicted"
    assert data["runtime"] == {
        "project_path": None,
        "requested_project_path": None,
        "repo_source": "unavailable",
        "cache_source": "none",
        "ephemeral_repo": False,
    }
