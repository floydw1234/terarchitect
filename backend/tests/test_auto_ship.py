"""Tests for auto_ship setting and hook."""

from unittest.mock import patch

from models.db import db, Project, Ticket, TicketAttempt


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


def test_pick_auto_winner_prefers_passed_test(client, project):
    from api.services.promotion_spine_service import pick_auto_winner_attempt

    pid = project["id"]
    frontier = project["accepted_frontier_id"]

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = frontier
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Pick winner",
            intent_status="active",
            base_leaf_id=frontier,
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add_all(
            [
                TicketAttempt(
                    project_id=pid,
                    ticket_id=ticket.id,
                    agenthub_commit_hash="a" * 40,
                    base_hash=frontier,
                    attempt_num=1,
                    status="validated",
                    test_status="failed",
                ),
                TicketAttempt(
                    project_id=pid,
                    ticket_id=ticket.id,
                    agenthub_commit_hash="b" * 40,
                    base_hash=frontier,
                    attempt_num=2,
                    status="validated",
                    test_status="passed",
                ),
            ]
        )
        db.session.commit()
        winner = pick_auto_winner_attempt(proj, ticket)
        assert winner is not None
        assert winner.agenthub_commit_hash == "b" * 40
