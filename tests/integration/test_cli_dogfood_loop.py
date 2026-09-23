"""
Decomposed operator CLI dogfood loop (Wed MH1 + MH2).

Drives the explicit sequence (no happy-path shortcut):
  evaluate-attempts → choose-winner → accept-winner → create-candidate
  → compose-candidate --sync → ship-run

Uses a live in-process backend + CLI subprocesses (host path). compose-candidate
--sync runs the real local shipper with AGENTHUB_URL docker-host remap; ship-run
mocks gh like backend/tests/test_e2e.py::test_e2e_ship_release_pr_merge_advances_frontier.
"""

from __future__ import annotations

import json
import os

# Must be set before session compose fixture runs (in-process backend; no Docker).
os.environ["TERARCHITECT_CLI_DOGFOOD_LOCAL"] = "1"
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.integration.conftest import REPO_ROOT, STUBS_DIR, make_local_git_repo

pytestmark = [pytest.mark.integration]


def _git_rev(work_dir: Path, ref: str = "HEAD") -> str:
    r = subprocess.run(
        ["git", "rev-parse", ref],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _feature_commit(work_dir: Path) -> tuple[str, str]:
    """Return (base_main_sha, attempt_commit_sha) after one feature commit on main."""
    base = _git_rev(work_dir)
    (work_dir / "dogfood.txt").write_text("dogfood slice\n")
    subprocess.run(["git", "add", "dogfood.txt"], cwd=work_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@test.com", "commit", "-m", "attempt"],
        cwd=work_dir,
        check=True,
        capture_output=True,
    )
    attempt = _git_rev(work_dir)
    return base, attempt


def _run_ta(api_url: str, args: list[str], *, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "cli", "--api-url", api_url, "--output", "json", *args]
    env = {**os.environ, "TERARCHITECT_API_URL": api_url, **(extra_env or {})}
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _cli_json(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


@pytest.fixture
def live_api_url(app):
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    yield url
    server.shutdown()


def test_dogfood_cli_decomposed_ship_loop_advances_frontier(client, live_api_url, tmp_path: Path):
    """Full decomposed CLI spine: accept → candidate → compose --sync → ship-run."""
    work_dir, _origin = make_local_git_repo(tmp_path)
    base_hash, attempt_hash = _feature_commit(work_dir)

    resp = client.post(
        "/api/projects",
        json={
            "name": "dogfood-cli",
            "git_mode": "swarm",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "github_url": "https://github.com/owner/repo",
            "accepted_frontier_id": base_hash,
            "is_existing_repo": True,
        },
    )
    assert resp.status_code == 201
    project = resp.get_json()
    pid = project["id"]

    from models.db import db, Project

    with client.application.app_context():
        stored = db.session.get(Project, pid)
        stored.shipped_frontier = base_hash
        db.session.commit()

    ticket_resp = client.post(
        f"/api/projects/{pid}/tickets",
        json={
            "column_id": "backlog",
            "title": "Dogfood ticket",
            "intent_status": "ready",
        },
    )
    assert ticket_resp.status_code == 201
    ticket_id = ticket_resp.get_json()["id"]

    from datetime import UTC, datetime

    from models.db import Ticket, TicketAttempt

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
            summary="Dogfood validated attempt",
            validated_at=datetime.now(UTC),
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)
        db.session.remove()

    api_url = live_api_url

    ev = _run_ta(
        api_url,
        ["ticket", "evaluate-attempts", pid, ticket_id, "--attempt", attempt_id],
    )
    ev_payload = _cli_json(ev)
    assert ev_payload["attempt_count"] >= 1
    assert ev_payload["attempts"][0]["status"] == "validated"

    cw = _run_ta(api_url, ["ticket", "choose-winner", pid, ticket_id, attempt_id])
    cw_payload = _cli_json(cw)
    assert cw_payload.get("is_winner") is True or cw_payload.get("status") == "validated"

    aw = _run_ta(
        api_url,
        [
            "ticket",
            "accept-winner",
            pid,
            ticket_id,
            attempt_id,
            "--expect-frontier",
            base_hash,
        ],
    )
    aw_payload = _cli_json(aw)
    assert aw_payload["status"] == "accepted"

    cc = _run_ta(
        api_url,
        ["ship", "create-candidate", pid, "--attempt", attempt_id, "--ticket", ticket_id],
    )
    cc_payload = _cli_json(cc)
    candidate_id = cc_payload["id"]
    assert cc_payload["status"] == "valid"

    stub_path = str(STUBS_DIR)
    compose_env = {
        "PATH": f"{stub_path}:{os.environ.get('PATH', '')}",
        "AGENTHUB_URL": "http://agenthub:8080",
        "AGENTHUB_API_KEY": "stub-ah-key",
        "MERGE_TEST_COMMAND": "true",
        "GH_TOKEN": "stub-gh-token",
        "GITHUB_TOKEN": "stub-gh-token",
    }
    comp = _run_ta(
        api_url,
        ["ship", "compose-candidate", pid, candidate_id, "--sync"],
        extra_env=compose_env,
    )
    assert comp.returncode == 0, (comp.stdout, comp.stderr)
    assert "remapped AGENTHUB_URL" in comp.stderr

    cand_detail = client.get(f"/api/projects/{pid}/ship/candidates/{candidate_id}")
    assert cand_detail.status_code == 200
    run_id = cand_detail.get_json()["latest_ship_run"]["id"]
    run_resp = client.get(f"/api/projects/{pid}/ship/runs/{run_id}")
    assert run_resp.status_code == 200
    run_payload = run_resp.get_json()
    assert run_payload["status"] == "ready_to_ship"
    composed_hash = run_payload.get("composed_commit_hash")
    assert composed_hash

    merged_main_sha = "f" * 40
    real_subprocess_run = subprocess.run

    def _gh_aware_subprocess_run(args, *pargs, **kwargs):
        argv = list(args) if args is not None else []
        if len(argv) >= 2 and argv[0] == "gh":
            if argv[1] == "pr" and len(argv) >= 3 and argv[2] == "view":
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "state": "OPEN",
                            "mergedAt": None,
                            "headRefName": run_payload.get("release_branch")
                            or "terarchitect/release/ship",
                            "headRefOid": composed_hash,
                        }
                    ),
                )
            if argv[1] == "pr" and len(argv) >= 3 and argv[2] == "merge":
                return MagicMock(returncode=0, stdout="", stderr="")
            if argv[1] == "api":
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps({"object": {"sha": merged_main_sha}}),
                )
        return real_subprocess_run(args, *pargs, **kwargs)

    with patch("subprocess.run", side_effect=_gh_aware_subprocess_run):
        ship = _run_ta(api_url, ["ship", "ship-run", pid, run_id])

    ship_payload = _cli_json(ship)
    assert ship_payload["status"] == "shipped"
    assert ship_payload["shipped_commit_hash"] == merged_main_sha

    with client.application.app_context():
        p = db.session.get(Project, pid)
        assert p.shipped_frontier == merged_main_sha

        from models.db import TicketAttempt

        attempt = db.session.get(TicketAttempt, attempt_id)
        assert attempt.status == "shipped"
        assert attempt.agenthub_commit_hash == attempt_hash
