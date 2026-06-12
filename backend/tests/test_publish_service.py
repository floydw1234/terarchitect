from types import SimpleNamespace
from unittest.mock import patch

import pytest

from models.db import Project, Ticket, TicketAttempt, db
from backend.api.services.publish_service import PublishError, publish_project


def _mk_result(args, returncode=0, stdout="", stderr=""):
    return SimpleNamespace(args=args, returncode=returncode, stdout=stdout, stderr=stderr)


def _create_project_with_attempts(app, tmp_path, *, project_path=None):
    with app.app_context():
        project = Project(
            name="publish-proj",
            git_mode="swarm",
            github_url="https://github.com/example/demo",
            github_ref="main",
            project_path=project_path or str(tmp_path),
            accepted_frontier_id="f" * 40,
        )
        db.session.add(project)
        db.session.flush()
        ticket = Ticket(project_id=project.id, column_id="done", title="Ship it", intent_status="active")
        db.session.add(ticket)
        db.session.flush()
        first = TicketAttempt(
            project_id=project.id,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash="b" * 40,
            attempt_num=1,
            status="accepted",
            summary="first",
        )
        second = TicketAttempt(
            project_id=project.id,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash="a" * 40,
            attempt_num=2,
            status="accepted",
            summary="second",
        )
        db.session.add_all([first, second])
        db.session.commit()
        return str(project.id), str(first.id), str(second.id)


def test_publish_project_dry_run_uses_latest_accepted_attempt(app, tmp_path):
    project_id, _first_attempt_id, second_attempt_id = _create_project_with_attempts(app, tmp_path)

    def fake_run(args, **kwargs):
        git_args = args[1:]
        if git_args == ["rev-parse", "--git-dir"]:
            return _mk_result(args, stdout=".git\n")
        if git_args == ["remote", "get-url", "origin"]:
            return _mk_result(args, stdout="git@github.com:example/demo.git\n")
        if git_args == ["status", "--porcelain", "--untracked-files=normal"]:
            return _mk_result(args)
        if git_args == ["cat-file", "-e", "c" * 40 + "^{commit}"]:
            return _mk_result(args)
        if git_args == ["fetch", "origin", "main"]:
            return _mk_result(args)
        if git_args == ["rev-parse", "refs/remotes/origin/main"]:
            return _mk_result(args, stdout="1" * 40 + "\n")
        if git_args == ["merge-base", "--is-ancestor", "1" * 40, "c" * 40]:
            return _mk_result(args)
        if git_args == ["branch", "--show-current"]:
            return _mk_result(args, stdout="feature\n")
        if git_args == ["rev-parse", "--verify", "main"]:
            return _mk_result(args, stdout="2" * 40 + "\n")
        raise AssertionError(f"Unexpected git command: {git_args}")

    with app.app_context():
        project = db.session.get(Project, project_id)
        with patch("backend.api.services.publish_service.subprocess.run", side_effect=fake_run):
            result = publish_project(project)

    assert result["selected_attempt_id"] == second_attempt_id
    assert result["selected_commit"] == "c" * 40
    assert result["fast_forward"] is True
    assert result["pushed"] is False


def test_publish_project_clones_missing_github_repo_before_preflight(app, tmp_path):
    missing_repo = tmp_path / "projects" / "demo"
    project_id, _first_attempt_id, second_attempt_id = _create_project_with_attempts(
        app,
        tmp_path,
        project_path=str(missing_repo),
    )

    def fake_run(args, **kwargs):
        git_args = args[1:]
        if git_args == ["clone", "https://github.com/example/demo", str(missing_repo)]:
            missing_repo.mkdir(parents=True, exist_ok=True)
            return _mk_result(args)
        if git_args == ["rev-parse", "--git-dir"]:
            return _mk_result(args, stdout=".git\n")
        if git_args == ["remote", "get-url", "origin"]:
            return _mk_result(args, stdout="https://github.com/example/demo.git\n")
        if git_args == ["status", "--porcelain", "--untracked-files=normal"]:
            return _mk_result(args)
        if git_args == ["cat-file", "-e", "c" * 40 + "^{commit}"]:
            return _mk_result(args)
        if git_args == ["fetch", "origin", "main"]:
            return _mk_result(args)
        if git_args == ["rev-parse", "refs/remotes/origin/main"]:
            return _mk_result(args, stdout="1" * 40 + "\n")
        if git_args == ["merge-base", "--is-ancestor", "1" * 40, "c" * 40]:
            return _mk_result(args)
        if git_args == ["branch", "--show-current"]:
            return _mk_result(args, stdout="feature\n")
        if git_args == ["rev-parse", "--verify", "main"]:
            return _mk_result(args, stdout="2" * 40 + "\n")
        raise AssertionError(f"Unexpected git command: {git_args}")

    with app.app_context():
        project = db.session.get(Project, project_id)
        with patch("backend.api.services.publish_service.subprocess.run", side_effect=fake_run):
            result = publish_project(project)

    assert result["selected_attempt_id"] == second_attempt_id
    assert result["project_path"] == str(missing_repo)
    assert result["commands"][0]["cmd"] == ["git", "clone", "https://github.com/example/demo", str(missing_repo)]
    assert result["commands"][0]["cwd"] == str(missing_repo.parent)
    assert result["commands"][0]["returncode"] == 0
    assert result["fast_forward"] is True
    assert result["pushed"] is False


def test_publish_project_refuses_dirty_repo(app, tmp_path):
    project_id, _first_attempt_id, _second_attempt_id = _create_project_with_attempts(app, tmp_path)

    def fake_run(args, **kwargs):
        git_args = args[1:]
        if git_args == ["rev-parse", "--git-dir"]:
            return _mk_result(args, stdout=".git\n")
        if git_args == ["remote", "get-url", "origin"]:
            return _mk_result(args, stdout="https://github.com/example/demo\n")
        if git_args == ["status", "--porcelain", "--untracked-files=normal"]:
            return _mk_result(args, stdout=" M src/app.py\n")
        raise AssertionError(f"Unexpected git command: {git_args}")

    with app.app_context():
        project = db.session.get(Project, project_id)
        with patch("backend.api.services.publish_service.subprocess.run", side_effect=fake_run):
            with pytest.raises(PublishError) as exc:
                publish_project(project)

    assert exc.value.phase == "preflight"
    assert "dirty" in exc.value.message.lower()


def test_publish_project_materializes_missing_commit_and_pushes(app, tmp_path):
    project_id, _first_attempt_id, second_attempt_id = _create_project_with_attempts(app, tmp_path)

    def fake_run(args, **kwargs):
        git_args = args[1:]
        if git_args == ["rev-parse", "--git-dir"]:
            return _mk_result(args, stdout=".git\n")
        if git_args == ["remote", "get-url", "origin"]:
            return _mk_result(args, stdout="https://github.com/example/demo.git\n")
        if git_args == ["status", "--porcelain", "--untracked-files=normal"]:
            return _mk_result(args)
        if git_args == ["cat-file", "-e", "c" * 40 + "^{commit}"]:
            if not hasattr(fake_run, "checked_once"):
                fake_run.checked_once = True
                return _mk_result(args, returncode=1, stderr="missing")
            return _mk_result(args)
        if git_args and git_args[:2] == ["bundle", "unbundle"]:
            return _mk_result(args)
        if git_args == ["fetch", "origin", "main"]:
            return _mk_result(args)
        if git_args == ["rev-parse", "refs/remotes/origin/main"]:
            return _mk_result(args, stdout="1" * 40 + "\n")
        if git_args == ["merge-base", "--is-ancestor", "1" * 40, "c" * 40]:
            return _mk_result(args)
        if git_args == ["branch", "--show-current"]:
            return _mk_result(args, stdout="main\n")
        if git_args == ["rev-parse", "--verify", "main"]:
            return _mk_result(args, stdout="1" * 40 + "\n")
        if git_args == ["checkout", "-B", "main", "refs/remotes/origin/main"]:
            return _mk_result(args)
        if git_args == ["merge", "--ff-only", "c" * 40]:
            return _mk_result(args)
        if git_args == ["push", "origin", "HEAD:refs/heads/main"]:
            return _mk_result(args)
        raise AssertionError(f"Unexpected git command: {git_args}")

    class FakeResponse:
        ok = True
        status_code = 200

        def iter_content(self, chunk_size=8192):
            yield b"bundle-bytes"

    with app.app_context():
        project = db.session.get(Project, project_id)
        with patch("backend.api.services.publish_service.subprocess.run", side_effect=fake_run):
            with patch("backend.api.services.publish_service.agenthub_connection_from_env", return_value=("http://agenthub", "key")):
                with patch("backend.api.services.publish_service.requests.get", return_value=FakeResponse()):
                    result = publish_project(project, push=True)
        shipped_attempt = db.session.get(TicketAttempt, second_attempt_id)

    assert result["selected_attempt_id"] == second_attempt_id
    assert result["pushed"] is True
    assert result["shipped_at"] is not None
    assert any(cmd["cmd"][:3] == ["git", "bundle", "unbundle"] for cmd in result["commands"])
    assert any(cmd["cmd"][:2] == ["git", "push"] for cmd in result["commands"])
    assert shipped_attempt.status == "shipped"


def test_publish_project_rejects_non_fast_forward_without_force(app, tmp_path):
    project_id, _first_attempt_id, _second_attempt_id = _create_project_with_attempts(app, tmp_path)

    def fake_run(args, **kwargs):
        git_args = args[1:]
        if git_args == ["rev-parse", "--git-dir"]:
            return _mk_result(args, stdout=".git\n")
        if git_args == ["remote", "get-url", "origin"]:
            return _mk_result(args, stdout="https://github.com/example/demo\n")
        if git_args == ["status", "--porcelain", "--untracked-files=normal"]:
            return _mk_result(args)
        if git_args == ["cat-file", "-e", "c" * 40 + "^{commit}"]:
            return _mk_result(args)
        if git_args == ["fetch", "origin", "main"]:
            return _mk_result(args)
        if git_args == ["rev-parse", "refs/remotes/origin/main"]:
            return _mk_result(args, stdout="1" * 40 + "\n")
        if git_args == ["merge-base", "--is-ancestor", "1" * 40, "c" * 40]:
            return _mk_result(args, returncode=1)
        if git_args == ["branch", "--show-current"]:
            return _mk_result(args, stdout="feature\n")
        if git_args == ["rev-parse", "--verify", "main"]:
            return _mk_result(args, stdout="2" * 40 + "\n")
        raise AssertionError(f"Unexpected git command: {git_args}")

    with app.app_context():
        project = db.session.get(Project, project_id)
        with patch("backend.api.services.publish_service.subprocess.run", side_effect=fake_run):
            with pytest.raises(PublishError) as exc:
                publish_project(project)

    assert exc.value.phase == "preflight"
    assert "fast-forward" in exc.value.message


def test_publish_project_rejects_mismatched_origin_before_fetch(app, tmp_path):
    project_id, _first_attempt_id, _second_attempt_id = _create_project_with_attempts(app, tmp_path)

    def fake_run(args, **kwargs):
        git_args = args[1:]
        if git_args == ["rev-parse", "--git-dir"]:
            return _mk_result(args, stdout=".git\n")
        if git_args == ["remote", "get-url", "origin"]:
            return _mk_result(args, stdout="git@github.com:example/other.git\n")
        if git_args[:2] == ["fetch", "origin"]:
            raise AssertionError("fetch should not run when origin mismatches project.github_url")
        raise AssertionError(f"Unexpected git command: {git_args}")

    with app.app_context():
        project = db.session.get(Project, project_id)
        with patch("backend.api.services.publish_service.subprocess.run", side_effect=fake_run):
            with pytest.raises(PublishError) as exc:
                publish_project(project)

    assert exc.value.phase == "project"
    assert "origin does not match" in exc.value.message
