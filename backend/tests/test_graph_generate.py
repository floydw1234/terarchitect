"""Tests for POST /api/projects/<id>/graph/generate."""
import json
import os
from unittest.mock import MagicMock


SAMPLE_NODES = [
    {
        "id": "node-1",
        "type": "service",
        "position": {"x": 100, "y": 100},
        "data": {
            "label": "API Server",
            "description": "Handles HTTP requests",
            "tech": ["Flask"],
            "ports": ["5000"],
            "security": ["Bearer token"],
        },
    },
    {
        "id": "node-2",
        "type": "database",
        "position": {"x": 400, "y": 300},
        "data": {
            "label": "Postgres",
            "description": "Primary data store",
            "tech": ["PostgreSQL"],
            "ports": ["5432"],
            "security": ["TLS"],
        },
    },
]
SAMPLE_EDGES = [
    {
        "id": "edge-1",
        "source": "node-1",
        "target": "node-2",
        "data": {"label": "reads/writes", "protocol": "TCP"},
    },
]
SAMPLE_LLM_RESPONSE = json.dumps({"nodes": SAMPLE_NODES, "edges": SAMPLE_EDGES})


def _set_github_url(client, project_id, url="https://github.com/example/repo"):
    resp = client.put(f"/api/projects/{project_id}", json={"github_url": url})
    assert resp.status_code == 200


def _mock_llm_response(content: str):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"choices": [{"message": {"content": content}}]}
    return resp


def _write_repo_fixture(repo_dir):
    (repo_dir / "backend").mkdir()
    (repo_dir / "backend" / "app.py").write_text(
        "from flask import Flask\napp = Flask(__name__)\n", encoding="utf-8"
    )
    (repo_dir / "requirements.txt").write_text("flask\nsqlalchemy\n", encoding="utf-8")


def test_generate_success(client, project, tmp_path, monkeypatch):
    pid = project["id"]
    _set_github_url(client, pid)
    _write_repo_fixture(tmp_path)

    monkeypatch.setenv("FRONTEND_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("FRONTEND_LLM_API_KEY", "sk-test")
    monkeypatch.setattr("api.services.graph_service.tempfile.mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr("api.services.graph_service.shutil.rmtree", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.services.graph_service.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        "api.services.graph_service.requests.post",
        lambda *args, **kwargs: _mock_llm_response(SAMPLE_LLM_RESPONSE),
    )

    resp = client.post(f"/api/projects/{pid}/graph/generate")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["node_count"] == 2
    assert data["edge_count"] == 1
    assert data["nodes"] == SAMPLE_NODES


def test_generate_rejects_nonempty_graph(client, project):
    pid = project["id"]
    _set_github_url(client, pid)

    from models.db import db, Graph
    with client.application.app_context():
        graph = Graph.query.filter_by(project_id=pid).first()
        graph.nodes = SAMPLE_NODES
        db.session.commit()

    resp = client.post(f"/api/projects/{pid}/graph/generate")

    assert resp.status_code == 409
    assert "already has nodes" in resp.get_json()["error"]


def test_generate_rejects_missing_github_url(client, project):
    resp = client.post(f"/api/projects/{project['id']}/graph/generate")

    assert resp.status_code == 400
    assert "GitHub URL" in resp.get_json()["error"]


def test_generate_rejects_unconfigured_model(client, project, monkeypatch):
    pid = project["id"]
    _set_github_url(client, pid)
    for key in (
        "FRONTEND_LLM_URL",
        "FRONTEND_LLM_MODEL",
        "FRONTEND_LLM_API_KEY",
        "DIRECTOR_LLM_URL",
        "DIRECTOR_MODEL",
        "DIRECTOR_API_KEY",
        "openai_api_key",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)

    resp = client.post(f"/api/projects/{pid}/graph/generate")

    assert resp.status_code == 400
    assert "No LLM model configured" in resp.get_json()["error"]


def test_generate_handles_llm_invalid_json(client, project, tmp_path, monkeypatch):
    pid = project["id"]
    _set_github_url(client, pid)
    _write_repo_fixture(tmp_path)

    monkeypatch.setenv("FRONTEND_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr("api.services.graph_service.tempfile.mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr("api.services.graph_service.shutil.rmtree", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.services.graph_service.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        "api.services.graph_service.requests.post",
        lambda *args, **kwargs: _mock_llm_response("This is not JSON at all."),
    )

    resp = client.post(f"/api/projects/{pid}/graph/generate")

    assert resp.status_code == 502
    assert "invalid JSON" in resp.get_json()["error"]


def test_generate_strips_markdown_fences(client, project, tmp_path, monkeypatch):
    pid = project["id"]
    _set_github_url(client, pid)
    _write_repo_fixture(tmp_path)

    monkeypatch.setenv("FRONTEND_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setattr("api.services.graph_service.tempfile.mkdtemp", lambda prefix: str(tmp_path))
    monkeypatch.setattr("api.services.graph_service.shutil.rmtree", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "api.services.graph_service.subprocess.run",
        lambda *args, **kwargs: MagicMock(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        "api.services.graph_service.requests.post",
        lambda *args, **kwargs: _mock_llm_response(f"```json\n{SAMPLE_LLM_RESPONSE}\n```"),
    )

    resp = client.post(f"/api/projects/{pid}/graph/generate")

    assert resp.status_code == 200
    assert resp.get_json()["node_count"] == 2


def test_frontend_llm_settings_fall_back_to_director(monkeypatch):
    from utils.app_settings import get_frontend_llm_settings

    monkeypatch.delenv("FRONTEND_LLM_URL", raising=False)
    monkeypatch.delenv("FRONTEND_LLM_MODEL", raising=False)
    monkeypatch.delenv("FRONTEND_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DIRECTOR_LLM_URL", "http://example.com/v1")
    monkeypatch.setenv("DIRECTOR_MODEL", "gpt-4o")
    monkeypatch.setenv("DIRECTOR_API_KEY", "sk-director-key")

    settings = get_frontend_llm_settings()

    assert settings["url"] == "http://example.com/v1"
    assert settings["model"] == "gpt-4o"
    assert settings["api_key"] == "sk-director-key"


def test_frontend_llm_settings_prefer_frontend(monkeypatch):
    from utils.app_settings import get_frontend_llm_settings

    monkeypatch.setenv("FRONTEND_LLM_URL", "http://frontend.example.com/v1")
    monkeypatch.setenv("FRONTEND_LLM_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("FRONTEND_LLM_API_KEY", "sk-frontend-key")

    settings = get_frontend_llm_settings()

    assert settings["url"] == "http://frontend.example.com/v1"
    assert settings["model"] == "gpt-4o-mini"
    assert settings["api_key"] == "sk-frontend-key"
