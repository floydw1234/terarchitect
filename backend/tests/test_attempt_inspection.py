"""Backend tests for project-level attempt inspection APIs."""
import os
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return (proc.stdout or "").strip()


def _make_local_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "attempt-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    base_hash = _git(repo, "rev-parse", "HEAD")

    src_dir = repo / "src"
    src_dir.mkdir()
    (repo / "README.md").write_text("base\nattempt\n", encoding="utf-8")
    (src_dir / "app.py").write_text("print('attempt')\n", encoding="utf-8")
    _git(repo, "add", "README.md", "src/app.py")
    _git(repo, "commit", "-m", "attempt")
    attempt_hash = _git(repo, "rev-parse", "HEAD")
    return repo, base_hash, attempt_hash


def _make_competing_attempt_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    repo = tmp_path / "competing-attempt-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    base_hash = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-b", "attempt-one")
    (repo / "README.md").write_text("base\nattempt one\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "attempt one")
    attempt_one_hash = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", base_hash)
    _git(repo, "checkout", "-b", "attempt-two")
    src_dir = repo / "src"
    src_dir.mkdir()
    (repo / "README.md").write_text("base\nattempt two\n", encoding="utf-8")
    (src_dir / "alt.py").write_text("print('attempt two')\n", encoding="utf-8")
    _git(repo, "add", "README.md", "src/alt.py")
    _git(repo, "commit", "-m", "attempt two")
    attempt_two_hash = _git(repo, "rev-parse", "HEAD")
    return repo, base_hash, attempt_one_hash, attempt_two_hash


def _create_local_project(client, repo: Path) -> dict:
    resp = client.post(
        "/api/projects",
        json={
            "name": f"local-{repo.name}",
            "execution_mode": "local",
            "project_path": str(repo),
            "git_mode": "swarm",
            "is_existing_repo": True,
        },
    )
    assert resp.status_code == 201
    return resp.get_json()


def test_project_attempt_list_and_detail_include_agent_friendly_fields(client, tmp_path):
    from models.db import Project, Ticket, TicketAttempt, db

    repo, base_hash, attempt_hash = _make_local_repo(tmp_path)
    project = _create_local_project(client, repo)
    pid = project["id"]

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = base_hash

        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Inspectable ticket",
            intent_status="active",
        )
        other_ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Other ticket",
            intent_status="active",
        )
        db.session.add_all([ticket, other_ticket])
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=attempt_hash,
            base_hash=base_hash,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="ready",
            test_status="passed",
        )
        other_attempt = TicketAttempt(
            project_id=pid,
            ticket_id=other_ticket.id,
            agenthub_commit_hash=None,
            base_hash=base_hash,
            wave_num=0,
            attempt_num=1,
            status="failed",
            summary="missing commit",
            validation_error="no commit",
        )
        db.session.add_all([attempt, other_attempt])
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_id = str(attempt.id)

    resp = client.get(f"/api/projects/{pid}/attempts", query_string={"status": "accepted"})
    assert resp.status_code == 200
    attempts = resp.get_json()
    assert len(attempts) == 1
    item = attempts[0]
    assert item["attempt_id"] == attempt_id
    assert item["ticket_id"] == ticket_id
    assert item["ticket_title"] == "Inspectable ticket"
    assert item["accepted"] is True
    assert item["satisfied"] is True
    assert item["agenthub_commit_hash"] == attempt_hash
    assert item["base_hash"] == base_hash
    assert item["base_leaf_id"] == base_hash
    assert item["parent_leaf_id"] == base_hash
    assert item["git_available"] is True
    assert item["commit_available"] is True
    assert sorted(item["changed_files"]) == ["README.md", "src/app.py"]
    assert item["next_actions"] == []

    ticket_filtered = client.get(
        f"/api/projects/{pid}/attempts",
        query_string={"ticket_id": ticket_id},
    )
    assert ticket_filtered.status_code == 200
    assert [row["attempt_id"] for row in ticket_filtered.get_json()] == [attempt_id]

    detail = client.get(f"/api/projects/{pid}/attempts/{attempt_id}")
    assert detail.status_code == 200
    detail_data = detail.get_json()
    assert detail_data["attempt_id"] == attempt_id
    assert detail_data["base_leaf_id"] == base_hash
    assert detail_data["parent_leaf_id"] == base_hash
    assert "stale" in detail_data
    assert detail_data["test_status"] == "passed"


def test_project_attempt_files_and_diff_return_git_inspection(client, tmp_path):
    from models.db import Project, Ticket, TicketAttempt, db

    repo, base_hash, attempt_hash = _make_local_repo(tmp_path)
    project = _create_local_project(client, repo)
    pid = project["id"]

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = base_hash
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Diff ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=attempt_hash,
            base_hash=base_hash,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="ready",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    files_resp = client.get(f"/api/projects/{pid}/attempts/{attempt_id}/files")
    assert files_resp.status_code == 200
    files_data = files_resp.get_json()
    assert files_data["attempt_id"] == attempt_id
    changed = {entry["path"]: entry for entry in files_data["changed_files"]}
    assert changed["README.md"]["status"] == "M"
    assert changed["README.md"]["additions"] >= 1
    assert changed["src/app.py"]["status"] == "A"
    assert changed["src/app.py"]["additions"] == 1

    diff_resp = client.get(
        f"/api/projects/{pid}/attempts/{attempt_id}/diff",
        query_string={"file": "src/app.py"},
    )
    assert diff_resp.status_code == 200
    diff_data = diff_resp.get_json()
    assert diff_data["file"] == "src/app.py"
    assert "src/app.py" in diff_data["diff"]
    assert "+print('attempt')" in diff_data["diff"]
    assert diff_data["truncated"] is False

    truncated = client.get(
        f"/api/projects/{pid}/attempts/{attempt_id}/diff",
        query_string={"max_bytes": "32"},
    )
    assert truncated.status_code == 200
    truncated_data = truncated.get_json()
    assert truncated_data["truncated"] is True
    assert truncated_data["bytes"] == 32


def test_project_attempt_list_keeps_same_ticket_sibling_attempts_visible(client, tmp_path):
    from models.db import Project, Ticket, TicketAttempt, db

    repo, base_hash, attempt_one_hash, attempt_two_hash = _make_competing_attempt_repo(tmp_path)
    project = _create_local_project(client, repo)
    pid = project["id"]

    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = base_hash
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Competing attempt ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        first = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=attempt_one_hash,
            base_hash=base_hash,
            wave_num=0,
            attempt_num=1,
            status="proposed",
            summary="first sibling",
        )
        second = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash=attempt_two_hash,
            base_hash=base_hash,
            wave_num=0,
            attempt_num=2,
            status="proposed",
            summary="second sibling",
        )
        db.session.add_all([first, second])
        db.session.commit()
        ticket_id = str(ticket.id)
        attempt_ids = [str(second.id), str(first.id)]

    ticket_attempts = client.get(f"/api/projects/{pid}/tickets/{ticket_id}/attempts")
    assert ticket_attempts.status_code == 200
    ticket_data = ticket_attempts.get_json()
    assert [row["id"] for row in ticket_data] == attempt_ids
    assert [row["base_hash"] for row in ticket_data] == [base_hash, base_hash]

    project_attempts = client.get(
        f"/api/projects/{pid}/attempts",
        query_string={"ticket_id": ticket_id},
    )
    assert project_attempts.status_code == 200
    project_data = project_attempts.get_json()
    assert [row["attempt_id"] for row in project_data] == attempt_ids
    assert [row["base_hash"] for row in project_data] == [base_hash, base_hash]
    assert [row["ticket_id"] for row in project_data] == [ticket_id, ticket_id]
    assert all(row["commit_available"] is True for row in project_data)
    assert all(row["changed_files"] for row in project_data)


def test_project_attempt_inspection_handles_unavailable_commit(client, tmp_path):
    from models.db import Ticket, TicketAttempt, db

    repo, base_hash, _ = _make_local_repo(tmp_path)
    project = _create_local_project(client, repo)
    pid = project["id"]

    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Unavailable commit ticket",
            intent_status="active",
        )
        db.session.add(ticket)
        db.session.flush()
        attempt = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="f" * 40,
            base_hash=base_hash,
            wave_num=0,
            attempt_num=1,
            status="accepted",
            summary="missing locally",
        )
        db.session.add(attempt)
        db.session.commit()
        attempt_id = str(attempt.id)

    detail = client.get(f"/api/projects/{pid}/attempts/{attempt_id}")
    assert detail.status_code == 200
    detail_data = detail.get_json()
    assert detail_data["commit_available"] is False
    assert "missing" in (detail_data["unavailable_reason"] or "").lower()
    assert detail_data["changed_files"] == []
    assert detail_data["next_actions"]

    files_resp = client.get(f"/api/projects/{pid}/attempts/{attempt_id}/files")
    assert files_resp.status_code == 200
    files_data = files_resp.get_json()
    assert files_data["changed_files"] == []
    assert files_data["commit_available"] is False
    assert files_data["next_actions"]

    diff_resp = client.get(f"/api/projects/{pid}/attempts/{attempt_id}/diff")
    assert diff_resp.status_code == 200
    diff_data = diff_resp.get_json()
    assert diff_data["diff"] == ""
    assert diff_data["commit_available"] is False
    assert diff_data["next_actions"]
