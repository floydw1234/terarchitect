"""Integration: auto-ship hook, revert, and sequential AgentHub ships."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.integration.conftest import REPO_ROOT, STUBS_DIR, make_local_git_repo
from tests.integration.test_cli_dogfood_loop import (
    _cli_json,
    _feature_commit,
    _git_rev,
    _run_ta,
    live_api_url,
)

pytestmark = [pytest.mark.integration]


@pytest.fixture(autouse=True)
def _dogfood_local_env():
    os.environ["TERARCHITECT_CLI_DOGFOOD_LOCAL"] = "1"


@pytest.fixture
def stub_agenthub_server():
    from tests.stubs.ah_server import Handler

    server = HTTPServer(("127.0.0.1", 8088), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:8088"
    server.shutdown()


def _seed_validated_attempt(client, pid, ticket_id, base_hash, attempt_hash):
    from models.db import Ticket, TicketAttempt, db

    with client.application.app_context():
        t = db.session.get(Ticket, ticket_id)
        t.column_id = "done"
        t.status = "completed"
        t.intent_status = "active"
        t.base_leaf_id = base_hash
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket_id,
            agenthub_commit_hash=attempt_hash,
            base_hash=base_hash,
            attempt_num=1,
            status="validated",
            summary="integration validated attempt",
            validated_at=datetime.now(UTC),
        )
        db.session.add(attempt)
        db.session.commit()
        return str(attempt.id)


def test_auto_ship_api_hook_ships_when_enabled(client):
    create = client.post(
        "/api/projects",
        json={
            "name": "auto-ship-hook",
            "git_mode": "swarm",
            "accepted_frontier_id": "a" * 40,
            "is_existing_repo": True,
        },
    )
    assert create.status_code == 201
    pid = create.get_json()["id"]
    frontier = "a" * 40

    from models.db import Project, Ticket, db

    with client.application.app_context():
        stored = db.session.get(Project, pid)
        stored.shipped_frontier = frontier
        stored.auto_ship = True
        db.session.commit()

    ticket_resp = client.post(
        f"/api/projects/{pid}/tickets",
        json={"column_id": "in_progress", "title": "Auto", "intent_status": "active"},
    )
    ticket_id = ticket_resp.get_json()["id"]
    with client.application.app_context():
        ticket = db.session.get(Ticket, ticket_id)
        ticket.base_leaf_id = frontier
        db.session.commit()

    fake = {"shipped": True, "shipped_commit_hash": "d" * 40, "ship_run_id": "run-1"}
    with patch("api.services.auto_ship_service.run_promotion_spine_for_attempt", return_value=fake):
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
    assert complete.get_json().get("auto_ship") == fake


def test_agenthub_two_ships_and_revert_linear_frontier(
    client, live_api_url, tmp_path: Path, stub_agenthub_server, monkeypatch
):
    monkeypatch.setenv("AGENTHUB_URL", stub_agenthub_server)
    monkeypatch.setenv("AGENTHUB_API_KEY", "stub-ah-key")
    monkeypatch.setenv("TERARCHITECT_AGENTHUB_URL", stub_agenthub_server)

    work_dir, _origin = make_local_git_repo(tmp_path)
    base_hash, attempt1_hash = _feature_commit(work_dir)

    resp = client.post(
        "/api/projects",
        json={
            "name": "auto-ship-seq",
            "git_mode": "swarm",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "accepted_frontier_id": base_hash,
            "is_existing_repo": True,
            "ship_target": "agenthub",
        },
    )
    pid = resp.get_json()["id"]

    from models.db import Project, db

    with client.application.app_context():
        stored = db.session.get(Project, pid)
        stored.shipped_frontier = base_hash
        db.session.commit()

    ticket1 = client.post(
        f"/api/projects/{pid}/tickets",
        json={"column_id": "backlog", "title": "First", "intent_status": "ready"},
    ).get_json()["id"]
    attempt1_id = _seed_validated_attempt(client, pid, ticket1, base_hash, attempt1_hash)

    compose_env = {
        "PATH": f"{STUBS_DIR}:{os.environ.get('PATH', '')}",
        "TERARCHITECT_AGENTHUB_URL": stub_agenthub_server,
        "AGENTHUB_API_KEY": "stub-ah-key",
        "MERGE_TEST_COMMAND": "true",
    }

    loop1 = _run_ta(
        live_api_url,
        ["ship", "operator-loop", pid, ticket1, attempt1_id, "--expect-frontier", base_hash, "--sync"],
        extra_env=compose_env,
    )
    p1 = _cli_json(loop1)
    frontier1 = p1["shipped_frontier_after"]
    assert frontier1 and frontier1 != base_hash

    run1 = client.get(f"/api/projects/{pid}/ship/runs/{p1['ship_run_id']}").get_json()
    assert run1["base_main_hash"] == base_hash

    (work_dir / "second.txt").write_text("second ship\n")
    subprocess.run(["git", "add", "second.txt"], cwd=work_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=t@test.com", "commit", "-m", "attempt2"],
        cwd=work_dir,
        check=True,
        capture_output=True,
    )
    attempt2_hash = _git_rev(work_dir)

    ticket2 = client.post(
        f"/api/projects/{pid}/tickets",
        json={"column_id": "backlog", "title": "Second", "intent_status": "ready"},
    ).get_json()["id"]
    attempt2_id = _seed_validated_attempt(client, pid, ticket2, frontier1, attempt2_hash)

    loop2 = _run_ta(
        live_api_url,
        ["ship", "operator-loop", pid, ticket2, attempt2_id, "--expect-frontier", frontier1, "--sync"],
        extra_env=compose_env,
    )
    p2 = _cli_json(loop2)
    frontier2 = p2["shipped_frontier_after"]
    assert frontier2 and frontier2 != frontier1

    run2 = client.get(f"/api/projects/{pid}/ship/runs/{p2['ship_run_id']}").get_json()
    assert run2["base_main_hash"] == frontier1

    revert = _run_ta(live_api_url, ["ship", "revert", pid], extra_env=compose_env)
    rev = _cli_json(revert)
    assert rev["shipped_frontier_after"] == frontier1

    with client.application.app_context():
        p = db.session.get(Project, pid)
        assert p.shipped_frontier == frontier1
