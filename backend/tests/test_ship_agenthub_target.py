"""Ship target modes: AgentHub-only (default) vs GitHub publish."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _enable_github_ship(client, project_id: str, github_url: str = "https://github.com/owner/repo"):
    return client.put(
        f"/api/projects/{project_id}",
        json={"ship_target": "github", "github_url": github_url},
    )


def test_project_defaults_to_agenthub_ship_target(client):
    resp = client.post(
        "/api/projects",
        json={"name": "agenthub-default", "git_mode": "swarm", "is_existing_repo": True},
    )
    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload.get("ship_target") == "agenthub"


def test_agenthub_ship_advances_frontier_without_github(client, project):
    pid = project["id"]
    frontier = project.get("shipped_frontier") or project["accepted_frontier_id"]

    from models.db import ShipRun, db

    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            status="ready_to_ship",
            composed_commit_hash="d" * 40,
            base_main_hash=frontier,
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    with patch("api.routes._ensure_commit_in_agenthub") as ensure_mock:
        ensure_mock.return_value = {"hash": "d" * 40, "exists": True, "bundle_fetchable": True}
        ship_resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert ship_resp.status_code == 200, ship_resp.get_json()
    body = ship_resp.get_json()
    assert body["status"] == "shipped"
    assert body["shipped_commit_hash"] == "d" * 40

    with client.application.app_context():
        from models.db import Project

        refreshed = db.session.get(Project, pid)
        assert refreshed.shipped_frontier == "d" * 40
        assert refreshed.accepted_frontier_id == "d" * 40


def test_github_ship_imports_merge_tip_into_agenthub(client, project):
    pid = project["id"]
    frontier = project.get("shipped_frontier") or project["accepted_frontier_id"]
    merged_sha = "f" * 40

    from models.db import ShipRun, db

    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            status="ready_to_ship",
            composed_commit_hash="c" * 40,
            base_main_hash=frontier,
            release_branch="terarchitect/release/ship-test",
            release_pr_number=99,
            release_pr_url="https://github.com/owner/repo/pull/99",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    assert _enable_github_ship(client, pid).status_code == 200

    view_ok = MagicMock(
        returncode=0,
        stdout=json.dumps(
            {
                "state": "OPEN",
                "headRefName": "terarchitect/release/ship-test",
                "headRefOid": "c" * 40,
            }
        ),
    )
    merge_ok = MagicMock(returncode=0, stdout="", stderr="")
    tip_ok = MagicMock(
        returncode=0,
        stdout=json.dumps({"object": {"sha": merged_sha}}),
    )

    with patch("api.routes.subprocess.run", side_effect=[view_ok, merge_ok, tip_ok]):
        with patch("api.routes._ensure_commit_in_agenthub") as ensure_mock:
            ensure_mock.return_value = {"hash": merged_sha, "exists": True, "bundle_fetchable": True}
            ship_resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert ship_resp.status_code == 200
    ensure_mock.assert_called_once()
    assert ensure_mock.call_args.kwargs.get("allow_github_import") is True


def test_github_ship_requires_github_url(client, project):
    pid = project["id"]
    frontier = project.get("shipped_frontier") or project["accepted_frontier_id"]

    from models.db import Project, ShipRun, db

    with client.application.app_context():
        stored = db.session.get(Project, pid)
        stored.github_url = None
        stored.ship_target = "github"
        db.session.commit()

        run = ShipRun(
            project_id=pid,
            status="ready_to_ship",
            composed_commit_hash="c" * 40,
            base_main_hash=frontier,
            release_pr_number=1,
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    ship_resp = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})
    assert ship_resp.status_code == 409


def test_fetch_agenthub_receipt_connection_error_is_clear():
    from api.services.agenthub_import_service import AgenthubImportError, fetch_agenthub_receipt

    import requests as requests_lib

    with patch(
        "api.services.agenthub_import_service.requests.get",
        side_effect=requests_lib.exceptions.ConnectionError("refused"),
    ):
        with pytest.raises(AgenthubImportError) as exc:
            fetch_agenthub_receipt("http://127.0.0.1:9", "key", "a" * 40)
    assert "Could not reach AgentHub" in str(exc.value)
