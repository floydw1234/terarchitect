"""Shared pytest fixtures for backend tests."""
import os
import sys
import uuid

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


@pytest.fixture(scope="function")
def app():
    """Minimal Flask app wired to an in-memory SQLite DB."""
    os.environ["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    os.environ.setdefault("MEMORY_SAVE_DIR", "/tmp/terarchitect_test")
    os.environ["ENABLE_COMPOSITE_WORKSPACE"] = "1"
    from main import create_app
    application = create_app()
    application.config["TESTING"] = True
    application.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    with application.app_context():
        from models.db import db
        db.create_all()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def project(client):
    """Swarm project."""
    resp = client.post(
        "/api/projects",
        json={
            "name": "test-proj",
            "git_mode": "swarm",
            "accepted_frontier_id": "leaf_01HZX3FIXTURE0123456789ABCDE",
            "is_existing_repo": True,
        },
    )
    assert resp.status_code == 201
    return resp.get_json()


@pytest.fixture
def accepted_ticket_and_attempt(client, project):
    """Ticket with an accepted attempt in wave 0."""
    from models.db import db, Ticket, TicketAttempt
    with client.application.app_context():
        ticket = Ticket(
            project_id=project["id"],
            column_id="done",
            title="Test ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=project["id"],
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="done",
        )
        db.session.add(attempt)
        db.session.commit()
        return str(ticket.id), str(attempt.id)
