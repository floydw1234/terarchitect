"""Tests for auto_ship setting and hook."""

from unittest.mock import patch

from models.db import db, Project, Ticket


def test_project_auto_ship_defaults_off(client, project):
    resp = client.get(f"/api/projects/{project['id']}")
    assert resp.status_code == 200
    assert resp.get_json().get("auto_ship") is False


def test_project_put_auto_ship(client, project):
    pid = project["id"]
    resp = client.put(f"/api/projects/{pid}", json={"auto_ship": True})
    assert resp.status_code == 200
    assert resp.get_json()["auto_ship"] is True


def test_ticket_complete_triggers_auto_ship_when_enabled(client, project):
    pid = project["id"]
    frontier = project["accepted_frontier_id"]

    with client.application.app_context():
        stored = db.session.get(Project, pid)
        stored.shipped_frontier = frontier
        stored.auto_ship = True
        db.session.commit()

    ticket_resp = client.post(
        f"/api/projects/{pid}/tickets",
        json={"column_id": "in_progress", "title": "Auto ship ticket", "intent_status": "active"},
    )
    assert ticket_resp.status_code == 201
    ticket_id = ticket_resp.get_json()["id"]

    with client.application.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        ticket.base_leaf_id = frontier
        db.session.commit()

    fake_result = {
        "shipped": True,
        "ship_run_id": "run-auto",
        "shipped_commit_hash": "e" * 40,
    }
    with patch(
        "api.routes._maybe_auto_ship_after_validation",
        return_value=fake_result,
    ) as mocked:
        complete = client.post(
            f"/api/projects/{pid}/tickets/{ticket_id}/complete",
            json={
                "commit_hash": "b" * 40,
                "base_hash": frontier,
                "agent_id": "agent-1",
                "summary": "done",
            },
        )
    assert complete.status_code == 200
    payload = complete.get_json()
    assert payload.get("auto_ship") == fake_result
    mocked.assert_called_once()




def _running_job(client, pid):
    from models.db import AgentJob

    ticket_resp = client.post(
        f"/api/projects/{pid}/tickets",
        json={"column_id": "backlog", "title": "Job exit ticket", "intent_status": "active"},
    )
    ticket_id = ticket_resp.get_json()["id"]
    with client.application.app_context():
        job = AgentJob(ticket_id=ticket_id, project_id=pid, status="running")
        db.session.add(job)
        db.session.commit()
        return str(job.id), ticket_id


def test_worker_job_complete_rechecks_auto_ship(client, project):
    """The last job leaving ``running`` must re-run auto-ship (batch now settled)."""
    pid = project["id"]
    job_id, ticket_id = _running_job(client, pid)
    client.application.config["AUTO_SHIP_JOB_EXIT_SYNC"] = True
    try:
        with patch("api.routes._maybe_auto_ship_after_validation", return_value=None) as mocked:
            resp = client.post(f"/api/worker/jobs/{job_id}/complete", json={})
    finally:
        client.application.config.pop("AUTO_SHIP_JOB_EXIT_SYNC", None)
    assert resp.status_code == 200
    mocked.assert_called_once_with(str(pid), str(ticket_id))


def test_worker_job_fail_rechecks_auto_ship(client, project):
    pid = project["id"]
    job_id, ticket_id = _running_job(client, pid)
    client.application.config["AUTO_SHIP_JOB_EXIT_SYNC"] = True
    try:
        with patch("api.routes._maybe_auto_ship_after_validation", return_value=None) as mocked:
            resp = client.post(f"/api/worker/jobs/{job_id}/fail", json={})
    finally:
        client.application.config.pop("AUTO_SHIP_JOB_EXIT_SYNC", None)
    assert resp.status_code == 200
    mocked.assert_called_once_with(str(pid), str(ticket_id))
