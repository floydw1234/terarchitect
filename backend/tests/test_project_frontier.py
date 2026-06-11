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
