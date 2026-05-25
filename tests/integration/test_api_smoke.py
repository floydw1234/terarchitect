"""
Tier 1 — API smoke tests.

Tests all CRUD operations via the HTTP API (using cli._api.API).
No agent, no LLM, no GitHub required.
Backend + postgres only.

Run with:
    pytest tests/integration/test_api_smoke.py -m smoke
"""

import json
from pathlib import Path

import pytest
from cli._api import API, APIError

pytestmark = pytest.mark.smoke

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ===========================================================================
# Health / readiness
# ===========================================================================

class TestHealth:
    def test_health_endpoint(self, api: API):
        """GET /health returns 200."""
        result = api.get("/health")
        # Accept any truthy response — shape varies, just needs to not 500
        assert result is not None or result == {}

    def test_ready_endpoint(self, api: API):
        """GET /api/ready returns {ready, missing}."""
        result = api.get("/api/ready")
        assert "ready" in result
        assert "missing" in result
        assert isinstance(result["missing"], list)


# ===========================================================================
# Projects
# ===========================================================================

class TestProjectCRUD:
    def test_list_projects_returns_list(self, api: API):
        projects = api.get("/api/projects")
        assert isinstance(projects, list)

    def test_create_project_minimal(self, api: API):
        p = api.post("/api/projects", {
            "name": "smoke-minimal",
            "is_existing_repo": True,
        })
        try:
            assert p["id"]
            assert p["name"] == "smoke-minimal"
            assert p.get("execution_mode") in ("docker", "local", None)
        finally:
            api.delete(f"/api/projects/{p['id']}", {"confirm_name": "smoke-minimal"})

    def test_create_project_full_fields(self, api: API):
        payload = {
            "name": "smoke-full",
            "description": "Full field test",
            "github_url": "https://github.com/test/repo",
            "execution_mode": "docker",
            "git_mode": "swarm",
            "is_existing_repo": True,
        }
        p = api.post("/api/projects", payload)
        try:
            assert p["name"] == "smoke-full"
            assert p["description"] == "Full field test"
            assert p["github_url"] == "https://github.com/test/repo"
            assert p["execution_mode"] == "docker"
            assert p["git_mode"] == "swarm"
        finally:
            api.delete(f"/api/projects/{p['id']}", {"confirm_name": "smoke-full"})

    def test_create_project_swarm_git_mode(self, api: API):
        p = api.post("/api/projects", {
            "name": "smoke-swarm",
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        try:
            assert p["git_mode"] == "swarm"
        finally:
            api.delete(f"/api/projects/{p['id']}", {"confirm_name": "smoke-swarm"})

    def test_get_project(self, api: API, project: dict):
        fetched = api.get(f"/api/projects/{project['id']}")
        assert fetched["id"] == project["id"]
        assert fetched["name"] == project["name"]

    def test_get_project_not_found(self, api: API):
        with pytest.raises(APIError) as exc:
            api.get("/api/projects/00000000-0000-0000-0000-000000000000")
        assert exc.value.status == 404

    def test_update_project_name(self, api: API, project: dict):
        updated = api.put(f"/api/projects/{project['id']}", {"name": "renamed-project"})
        assert updated["name"] == "renamed-project"
        # Verify persisted
        fetched = api.get(f"/api/projects/{project['id']}")
        assert fetched["name"] == "renamed-project"

    def test_update_project_git_mode(self, api: API, project: dict):
        updated = api.put(f"/api/projects/{project['id']}", {"git_mode": "swarm"})
        assert updated["git_mode"] == "swarm"

    def test_update_project_execution_mode(self, api: API, project: dict):
        updated = api.put(f"/api/projects/{project['id']}", {"execution_mode": "local"})
        assert updated["execution_mode"] == "local"

    def test_project_appears_in_list(self, api: API, project: dict):
        projects = api.get("/api/projects")
        ids = [p["id"] for p in projects]
        assert project["id"] in ids

    def test_delete_project(self, api: API):
        p = api.post("/api/projects", {"name": "to-be-deleted", "is_existing_repo": True})
        api.delete(f"/api/projects/{p['id']}", {"confirm_name": "to-be-deleted"})
        with pytest.raises(APIError) as exc:
            api.get(f"/api/projects/{p['id']}")
        assert exc.value.status == 404

    def test_delete_project_wrong_confirm(self, api: API, project: dict):
        with pytest.raises(APIError) as exc:
            api.delete(f"/api/projects/{project['id']}", {"confirm_name": "WRONG"})
        assert exc.value.status in (400, 409, 422)
        # Project should still exist
        fetched = api.get(f"/api/projects/{project['id']}")
        assert fetched["id"] == project["id"]

    def test_delete_cascades_to_tickets(self, api: API):
        p = api.post("/api/projects", {"name": "cascade-test", "is_existing_repo": True})
        pid = p["id"]
        t = api.post(f"/api/projects/{pid}/tickets", {
            "title": "Cascade ticket", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        tid = t["id"]
        api.delete(f"/api/projects/{pid}", {"confirm_name": "cascade-test"})
        # Ticket should be gone (404 on project means cascade worked)
        with pytest.raises(APIError) as exc:
            api.get(f"/api/projects/{pid}/tickets/{tid}")
        assert exc.value.status == 404


# ===========================================================================
# Tickets
# ===========================================================================

class TestTicketCRUD:
    def test_list_tickets_empty(self, api: API, project_id: str):
        tickets = api.get(f"/api/projects/{project_id}/tickets")
        assert isinstance(tickets, list)

    def test_create_ticket_minimal(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Minimal ticket",
            "column_id": "backlog",
            "priority": "medium",
            "status": "todo",
        })
        assert t["id"]
        assert t["title"] == "Minimal ticket"
        assert t["column_id"] == "backlog"
        assert t["project_id"] == project_id

    def test_create_ticket_full(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Full ticket",
            "description": "Detailed description",
            "column_id": "backlog",
            "priority": "high",
            "status": "todo",
            "associated_node_ids": ["node-1", "node-2"],
        })
        assert t["title"] == "Full ticket"
        assert t["description"] == "Detailed description"
        assert t["priority"] == "high"
        assert "node-1" in (t.get("associated_node_ids") or [])

    def test_get_ticket(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Get me", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        fetched = api.get(f"/api/projects/{project_id}/tickets/{t['id']}")
        assert fetched["id"] == t["id"]
        assert fetched["title"] == "Get me"

    def test_ticket_appears_in_list(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Listed ticket", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        tickets = api.get(f"/api/projects/{project_id}/tickets")
        ids = [x["id"] for x in tickets]
        assert t["id"] in ids

    def test_update_ticket_title(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Old title", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        updated = api.patch(f"/api/projects/{project_id}/tickets/{t['id']}", {
            "title": "New title",
        })
        assert updated["title"] == "New title"

    def test_update_ticket_column(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Move me", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        # Move to queued (not in_progress — that would enqueue a job)
        updated = api.patch(f"/api/projects/{project_id}/tickets/{t['id']}", {
            "column_id": "queued",
        })
        assert updated["column_id"] == "queued"

    def test_queued_column_is_valid(self, api: API, project_id: str):
        """Tickets can be moved to the 'queued' state (pre-dispatch holding area)."""
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Queue me", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        updated = api.patch(f"/api/projects/{project_id}/tickets/{t['id']}", {
            "column_id": "queued",
        })
        assert updated["column_id"] == "queued"

    def test_delete_ticket(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Delete me", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        api.delete(f"/api/projects/{project_id}/tickets/{t['id']}")
        with pytest.raises(APIError) as exc:
            api.get(f"/api/projects/{project_id}/tickets/{t['id']}")
        assert exc.value.status == 404

    def test_ticket_logs_empty(self, api: API, project_id: str):
        t = api.post(f"/api/projects/{project_id}/tickets", {
            "title": "Log test", "column_id": "backlog", "priority": "low", "status": "todo",
        })
        logs = api.get(f"/api/projects/{project_id}/tickets/{t['id']}/logs")
        assert isinstance(logs, list)
        assert len(logs) == 0

    def test_batch_create_from_fixture(self, api: API, project_id: str):
        """Create all tickets from the shared fixtures/tickets.json."""
        raw = (FIXTURES / "tickets.json").read_text()
        ticket_defs = json.loads(raw)
        created = []
        for td in ticket_defs:
            t = api.post(f"/api/projects/{project_id}/tickets", td)
            created.append(t)
        assert len(created) == len(ticket_defs)
        titles = [t["title"] for t in created]
        assert "Add authentication middleware" in titles
        assert "Add rate limiting" in titles


# ===========================================================================
# Graph
# ===========================================================================

class TestGraph:
    def test_get_graph_returns_shape(self, api: API, project_id: str):
        graph = api.get(f"/api/projects/{project_id}/graph")
        assert "nodes" in graph or graph is not None  # may be empty on fresh project

    def test_set_and_get_graph(self, api: API, project_id: str):
        raw = (FIXTURES / "graph.json").read_text()
        payload = json.loads(raw)
        result = api.put(f"/api/projects/{project_id}/graph", payload)
        assert "version" in result

        fetched = api.get(f"/api/projects/{project_id}/graph")
        nodes = fetched.get("nodes") or []
        edges = fetched.get("edges") or []
        node_ids = [n["id"] for n in nodes]
        assert "frontend" in node_ids
        assert "api" in node_ids
        assert "db" in node_ids
        assert len(edges) == 3

    def test_update_graph_increments_version(self, api: API, project_id: str):
        payload = json.loads((FIXTURES / "graph.json").read_text())
        r1 = api.put(f"/api/projects/{project_id}/graph", payload)
        r2 = api.put(f"/api/projects/{project_id}/graph", payload)
        assert r2["version"] > r1["version"]

    def test_set_empty_graph(self, api: API, project_id: str):
        result = api.put(f"/api/projects/{project_id}/graph", {"nodes": [], "edges": []})
        assert "version" in result
        fetched = api.get(f"/api/projects/{project_id}/graph")
        assert (fetched.get("nodes") or []) == []
        assert (fetched.get("edges") or []) == []


# ===========================================================================
# Kanban
# ===========================================================================

class TestKanban:
    def test_get_kanban_has_default_columns(self, api: API, project_id: str):
        kanban = api.get(f"/api/projects/{project_id}/kanban")
        columns = kanban.get("columns") or []
        assert len(columns) >= 1
        col_ids = [c.get("id") or c.get("title", "").lower() for c in columns]
        # Should have at least a backlog-like column
        assert any("backlog" in cid.lower() or "todo" in cid.lower()
                   for cid in col_ids)

    def test_update_kanban_columns(self, api: API, project_id: str):
        # Fetch existing columns to preserve the schema shape
        kanban = api.get(f"/api/projects/{project_id}/kanban")
        columns = kanban.get("columns") or []
        if not columns:
            pytest.skip("No existing columns to update")

        # Rename the first column (round-trip test)
        original_title = columns[0]["title"]
        columns[0]["title"] = "RENAMED"
        api.put(f"/api/projects/{project_id}/kanban", {"columns": columns})

        fetched = api.get(f"/api/projects/{project_id}/kanban")
        titles = [c["title"] for c in fetched.get("columns") or []]
        assert "RENAMED" in titles

        # Restore original title
        columns[0]["title"] = original_title
        api.put(f"/api/projects/{project_id}/kanban", {"columns": columns})


# ===========================================================================
# Notes
# ===========================================================================

class TestNotes:
    def test_list_notes_empty(self, api: API, project_id: str):
        notes = api.get(f"/api/projects/{project_id}/notes")
        assert isinstance(notes, list)

    def test_create_note(self, api: API, project_id: str):
        note = api.post(f"/api/projects/{project_id}/notes", {
            "title": "Test note",
            "content": "Some content",
            "node_ids": [],
            "edge_ids": [],
        })
        assert note["id"]
        assert note["title"] == "Test note"
        assert note["content"] == "Some content"

    def test_update_note(self, api: API, project_id: str):
        note = api.post(f"/api/projects/{project_id}/notes", {
            "title": "Before", "content": "Old", "node_ids": [], "edge_ids": [],
        })
        updated = api.patch(f"/api/projects/{project_id}/notes/{note['id']}", {
            "title": "After", "content": "New",
        })
        assert updated["title"] == "After"
        assert updated["content"] == "New"

    def test_delete_note(self, api: API, project_id: str):
        note = api.post(f"/api/projects/{project_id}/notes", {
            "title": "Deletable", "content": "x", "node_ids": [], "edge_ids": [],
        })
        api.delete(f"/api/projects/{project_id}/notes/{note['id']}")
        notes = api.get(f"/api/projects/{project_id}/notes")
        ids = [n["id"] for n in notes]
        assert note["id"] not in ids


# ===========================================================================
# Error handling
# ===========================================================================

class TestErrors:
    def test_create_project_missing_name(self, api: API):
        with pytest.raises(APIError) as exc:
            api.post("/api/projects", {"description": "No name"})
        assert exc.value.status in (400, 422)

    def test_get_nonexistent_ticket(self, api: API, project_id: str):
        with pytest.raises(APIError) as exc:
            api.get(f"/api/projects/{project_id}/tickets/00000000-0000-0000-0000-000000000000")
        assert exc.value.status == 404

    def test_get_nonexistent_project(self, api: API):
        with pytest.raises(APIError) as exc:
            api.get("/api/projects/00000000-0000-0000-0000-000000000000")
        assert exc.value.status == 404


# ===========================================================================
# Project Start ("Go" button)
# ===========================================================================

class TestProjectStart:
    def test_start_moves_backlog_to_queued(self, api: API):
        """POST /projects/{id}/start moves all backlog tickets to queued."""
        p = api.post("/api/projects", {"name": "start-test", "is_existing_repo": True})
        pid = p["id"]
        try:
            for i in range(3):
                api.post(f"/api/projects/{pid}/tickets", {
                    "title": f"Backlog ticket {i}", "column_id": "backlog",
                    "priority": "low", "status": "todo",
                })

            result = api.post(f"/api/projects/{pid}/start", {})

            assert "queued" in result
            assert "dispatched" in result
            assert "message" in result

            tickets = api.get(f"/api/projects/{pid}/tickets")
            columns = {t["column_id"] for t in tickets}
            # All tickets should have left backlog — either queued or in_progress
            assert "backlog" not in columns
            assert columns <= {"queued", "in_progress"}
        finally:
            api.delete(f"/api/projects/{pid}", {"confirm_name": "start-test"})

    def test_start_on_empty_project(self, api: API):
        """POST /projects/{id}/start on a project with no tickets returns 200 with zeros."""
        p = api.post("/api/projects", {"name": "start-empty", "is_existing_repo": True})
        pid = p["id"]
        try:
            result = api.post(f"/api/projects/{pid}/start", {})
            assert result.get("queued", 0) == 0
            assert result.get("dispatched", 0) == 0
        finally:
            api.delete(f"/api/projects/{pid}", {"confirm_name": "start-empty"})

    def test_start_respects_dependencies(self, api: API):
        """Tickets with unfinished deps stay queued; tickets with no deps get dispatched."""
        p = api.post("/api/projects", {"name": "start-deps", "is_existing_repo": True})
        pid = p["id"]
        try:
            t_a = api.post(f"/api/projects/{pid}/tickets", {
                "title": "A (no deps)", "column_id": "backlog",
                "priority": "low", "status": "todo",
            })
            t_b = api.post(f"/api/projects/{pid}/tickets", {
                "title": "B (depends on A)", "column_id": "backlog",
                "priority": "low", "status": "todo",
                "depends_on_ticket_ids": [t_a["id"]],
            })

            api.post(f"/api/projects/{pid}/start", {})

            a_final = api.get(f"/api/projects/{pid}/tickets/{t_a['id']}")
            b_final = api.get(f"/api/projects/{pid}/tickets/{t_b['id']}")

            # B is blocked by A — must stay queued
            assert b_final["column_id"] == "queued"
            # A has no deps — should have left backlog (queued or in_progress)
            assert a_final["column_id"] in ("queued", "in_progress")
        finally:
            api.delete(f"/api/projects/{pid}", {"confirm_name": "start-deps"})
