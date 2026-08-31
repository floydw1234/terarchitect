import os
from unittest.mock import patch

from models.db import Project, Ticket, TicketAttempt, db
from api.services.channel_service import ticket_channel


class _MockResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload


def _make_commit(hash_value: str, parent_hash: str, agent_id: str, message: str, created_at: str) -> dict:
    return {
        "hash": hash_value,
        "parent_hash": parent_hash,
        "agent_id": agent_id,
        "message": message,
        "created_at": created_at,
    }


def test_project_agenthub_graph_omits_auth_header_when_backend_key_blank(client):
    project_resp = client.post(
        "/api/projects",
        json={
            "name": "graph-proj",
            "git_mode": "swarm",
            "accepted_frontier_id": "b" * 40,
            "is_existing_repo": True,
        },
    )
    assert project_resp.status_code == 201
    project = project_resp.get_json()

    seen_headers = []

    def fake_get(*args, **kwargs):
        url = args[0]
        headers = kwargs.get("headers")
        seen_headers.append(headers)
        if url.endswith("/api/git/commits"):
            return _MockResponse([
                _make_commit("b" * 40, "a" * 40, "agent-1", "child", "2026-06-17T10:05:00Z"),
                _make_commit("a" * 40, "", "agent-1", "root", "2026-06-17T10:00:00Z"),
            ])
        if url.endswith(f"/api/git/commits/{'b' * 40}/lineage"):
            return _MockResponse([
                _make_commit("b" * 40, "a" * 40, "agent-1", "child", "2026-06-17T10:05:00Z"),
                _make_commit("a" * 40, "", "agent-1", "root", "2026-06-17T10:00:00Z"),
            ])
        if url.endswith(f"/api/git/commits/{'a' * 40}/lineage"):
            return _MockResponse([
                _make_commit("a" * 40, "", "agent-1", "root", "2026-06-17T10:00:00Z"),
            ])
        if url.endswith("/api/git/leaves"):
            return _MockResponse([
                _make_commit("b" * 40, "a" * 40, "agent-1", "child", "2026-06-17T10:05:00Z"),
            ])
        if url.endswith("/api/channels"):
            return _MockResponse([])
        raise AssertionError(f"Unhandled URL {url} params={kwargs.get('params')}")

    with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088", "AGENTHUB_API_KEY": ""}, clear=False):
        with patch("api.services.agenthub_graph_service.requests.Session.get", side_effect=fake_get):
            response = client.get(f"/api/projects/{project['id']}/agenthub/graph")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"]["auth_mode"] == "unauthenticated"
    assert payload["graph"]["commits"][0]["hash"] == "b" * 40
    assert all(not headers for headers in seen_headers)


def test_project_agenthub_graph_scopes_commits_and_sends_backend_bearer_key(client):
    project_resp = client.post(
        "/api/projects",
        json={
            "name": "Scoped Project",
            "git_mode": "swarm",
            "accepted_frontier_id": "b" * 40,
            "is_existing_repo": True,
        },
    )
    assert project_resp.status_code == 201
    project = project_resp.get_json()

    with client.application.app_context():
        project_row = db.session.get(Project, project["id"])
        project_row.github_resolved_sha = "a" * 40
        project_row.shipped_frontier = "a" * 40
        ticket = Ticket(
            project_id=project["id"],
            column_id="done",
            title="Scoped ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        db.session.add(TicketAttempt(
            project_id=project["id"],
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
            summary="scoped attempt",
        ))
        other_project = Project(name="Other", git_mode="swarm", source_type="agenthub_leaf")
        db.session.add(other_project)
        db.session.flush()
        db.session.add(Ticket(
            project_id=other_project.id,
            column_id="done",
            title="Other ticket",
            intent_status="active",
        ))
        db.session.commit()
        scoped_channel = ticket_channel(str(ticket.id))

    seen_headers = []

    def fake_get(*args, **kwargs):
        url = args[0]
        headers = kwargs.get("headers")
        seen_headers.append(headers)
        if url.endswith("/api/git/commits"):
            return _MockResponse([
                _make_commit("z" * 40, "y" * 40, "agent-9", "unrelated leaf", "2026-06-17T10:15:00Z"),
                _make_commit("c" * 40, "b" * 40, "agent-1", "project attempt", "2026-06-17T10:10:00Z"),
                _make_commit("b" * 40, "a" * 40, "agent-1", "project frontier", "2026-06-17T10:05:00Z"),
                _make_commit("a" * 40, "", "agent-1", "project root", "2026-06-17T10:00:00Z"),
                _make_commit("y" * 40, "", "agent-9", "unrelated root", "2026-06-17T09:55:00Z"),
            ])
        if url.endswith(f"/api/git/commits/{'b' * 40}/lineage"):
            return _MockResponse([
                _make_commit("b" * 40, "a" * 40, "agent-1", "project frontier", "2026-06-17T10:05:00Z"),
                _make_commit("a" * 40, "", "agent-1", "project root", "2026-06-17T10:00:00Z"),
            ])
        if url.endswith(f"/api/git/commits/{'a' * 40}/lineage"):
            return _MockResponse([
                _make_commit("a" * 40, "", "agent-1", "project root", "2026-06-17T10:00:00Z"),
            ])
        if url.endswith(f"/api/git/commits/{'c' * 40}/lineage"):
            return _MockResponse([
                _make_commit("c" * 40, "b" * 40, "agent-1", "project attempt", "2026-06-17T10:10:00Z"),
                _make_commit("b" * 40, "a" * 40, "agent-1", "project frontier", "2026-06-17T10:05:00Z"),
                _make_commit("a" * 40, "", "agent-1", "project root", "2026-06-17T10:00:00Z"),
            ])
        if url.endswith("/api/git/leaves"):
            return _MockResponse([
                _make_commit("z" * 40, "y" * 40, "agent-9", "unrelated leaf", "2026-06-17T10:15:00Z"),
                _make_commit("c" * 40, "b" * 40, "agent-1", "project attempt", "2026-06-17T10:10:00Z"),
            ])
        if url.endswith("/api/channels"):
            return _MockResponse([
                {"id": 1, "name": scoped_channel, "description": "ticket ledger", "created_at": "2026-06-17T09:30:00Z"},
                {"id": 2, "name": "ops", "description": "unrelated", "created_at": "2026-06-17T09:00:00Z"},
            ])
        if url.endswith(f"/api/channels/{scoped_channel}/posts"):
            return _MockResponse([
                {
                    "id": 88,
                    "channel_id": 1,
                    "agent_id": "agent-1",
                    "parent_id": None,
                    "content": "attempt_published: project attempt",
                    "created_at": "2026-06-17T10:11:00Z",
                },
            ])
        raise AssertionError(f"Unhandled URL {url} params={kwargs.get('params')}")

    with patch.dict(os.environ, {"AGENTHUB_URL": "http://agenthub:8088", "AGENTHUB_API_KEY": "backend-secret"}, clear=False):
        with patch("api.services.agenthub_graph_service.requests.Session.get", side_effect=fake_get):
            response = client.get(f"/api/projects/{project['id']}/agenthub/graph")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"]["auth_mode"] == "backend_api_key"
    assert {item["hash"] for item in payload["graph"]["commits"]} == {"a" * 40, "b" * 40, "c" * 40}
    assert payload["graph"]["leaves"][0]["hash"] == "c" * 40
    assert [channel["name"] for channel in payload["graph"]["channels"]] == [scoped_channel]
    assert payload["graph"]["posts"][0]["channel_name"] == scoped_channel
    assert all(headers == {"Authorization": "Bearer backend-secret"} for headers in seen_headers)


def test_project_agenthub_graph_returns_empty_message_when_project_has_no_hashes(client):
    project_resp = client.post(
        "/api/projects",
        json={
            "name": "no-hashes",
            "git_mode": "swarm",
            "is_existing_repo": True,
        },
    )
    assert project_resp.status_code == 201
    project = project_resp.get_json()

    with patch("api.services.agenthub_graph_service.requests.Session.get") as mocked_get:
        response = client.get(f"/api/projects/{project['id']}/agenthub/graph")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"]["code"] == "no_project_hashes"
    assert payload["graph"]["commits"] == []
    mocked_get.assert_not_called()
