import json
from unittest.mock import MagicMock, patch


def test_ship_doctor_reports_partial_checks_and_next_commands(client, project):
    pid = project["id"]

    auth_response = MagicMock(returncode=1, stderr="not logged in")
    with patch("backend.api.routes.subprocess.run", return_value=auth_response):
        response = client.get(f"/api/projects/{pid}/ship/doctor")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["project_id"] == pid
    assert payload["status"] == "warn"
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["db_schema"]["status"] == "pass"
    assert checks["github_auth"]["status"] == "warn"
    assert checks["agenthub"]["status"] == "warn"
    assert checks["project_repo"]["status"] == "warn"
    assert checks["frontier"]["status"] == "warn"
    assert any(cmd.startswith("ta ship doctor ") for cmd in payload["next_commands"])


def test_ship_run_merge_failure_preserves_detail_and_hint(client, project):
    pid = project["id"]

    from models.db import ShipRun, db

    with client.application.app_context():
        run = ShipRun(
            project_id=pid,
            status="ready_to_ship",
            composed_commit_hash="c" * 40,
            base_main_hash="f" * 40,
            release_branch="terarchitect/release/ship-abc12345",
            release_pr_number=42,
            release_pr_url="https://github.com/owner/repo/pull/42",
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)

    update_resp = client.put(
        f"/api/projects/{pid}",
        json={"github_url": "https://github.com/owner/repo", "shipped_frontier": "f" * 40},
    )
    assert update_resp.status_code == 200

    view_response = MagicMock(returncode=0)
    view_response.stdout = json.dumps(
        {"state": "OPEN", "headRefName": "terarchitect/release/ship-abc12345", "headRefOid": "c" * 40}
    )
    merge_response = MagicMock(returncode=1)
    merge_response.stderr = "GraphQL: Base branch protection prevents merge"

    with patch("subprocess.run", side_effect=[view_response, merge_response]):
        response = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["error"] == "PR merge failed"
    assert "Base branch protection prevents merge" in payload["detail"]
    assert payload["phase"] == "merge"
    assert payload["request_id"] == f"ship-run:{run_id}"
    assert "ta ship doctor" in payload["hint"]
    assert f"ta ship doctor {pid}" in payload["next_commands"]


def test_ship_run_already_merged_reconciles_and_returns_evidence_summary(client, project):
    pid = project["id"]

    from models.db import PromotionCandidate, ShipRun, Ticket, TicketAttempt, db

    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Ship me", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="f" * 40,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.flush()
        candidate = PromotionCandidate(
            project_id=pid,
            selected_attempt_ids=[str(attempt.id)],
            selected_leaf_hashes=["a" * 40],
            base_root_hash="f" * 40,
            status="composed",
        )
        db.session.add(candidate)
        db.session.flush()
        run = ShipRun(
            project_id=pid,
            promotion_candidate_id=str(candidate.id),
            status="ready_to_ship",
            composed_commit_hash="c" * 40,
            base_main_hash="f" * 40,
            release_branch="terarchitect/release/ship-abc12345",
            release_pr_number=42,
            release_pr_url="https://github.com/owner/repo/pull/42",
            summary="Ship summary",
            test_status="passed",
            test_output="all green",
            changed_files=["src/app.py"],
        )
        db.session.add(run)
        db.session.commit()
        run_id = str(run.id)
        candidate_id = str(candidate.id)
        attempt_id = str(attempt.id)

    update_resp = client.put(
        f"/api/projects/{pid}",
        json={"github_url": "https://github.com/owner/repo", "shipped_frontier": "f" * 40},
    )
    assert update_resp.status_code == 200

    view_response = MagicMock(returncode=0)
    view_response.stdout = json.dumps(
        {
            "state": "MERGED",
            "mergedAt": "2026-06-10T12:00:00Z",
            "headRefName": "terarchitect/release/ship-abc12345",
            "headRefOid": "c" * 40,
        }
    )
    with patch("subprocess.run", side_effect=[view_response]):
        response = client.post(f"/api/projects/{pid}/ship/runs/{run_id}/ship", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "shipped"
    assert payload["shipped_commit_hash"] == "c" * 40
    assert payload["evidence_summary"]["status"] == "passed"
    assert payload["evidence_summary"]["target_type"] == "ship_run"

    with client.application.app_context():
        from models.db import Project

        refreshed_project = db.session.get(Project, pid)
        refreshed_run = db.session.get(ShipRun, run_id)
        refreshed_candidate = db.session.get(PromotionCandidate, candidate_id)
        refreshed_attempt = db.session.get(TicketAttempt, attempt_id)

        assert refreshed_project.shipped_frontier == "c" * 40
        assert refreshed_run.status == "shipped"
        assert refreshed_candidate.status == "shipped"
        assert refreshed_attempt.status == "shipped"


def test_ship_happy_path_creates_candidate_and_queued_run(client, project):
    pid = project["id"]

    from models.db import Ticket, TicketAttempt, db

    with client.application.app_context():
        ticket = Ticket(project_id=pid, column_id="done", title="Happy path", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="f" * 40,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        ticket_id = str(ticket.id)

    response = client.post(
        f"/api/projects/{pid}/ship/happy-path",
        json={"ticket_id": ticket_id, "merge_method": "merge"},
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["status"] == "queued"
    assert payload["candidate_id"]
    assert payload["ship_run_id"]
    assert f"ta ship run {pid}" in payload["next_commands"][0]
