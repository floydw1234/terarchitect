from types import SimpleNamespace
import json
from unittest.mock import patch

import pytest

from cli.commands import ticket as ticket_cmd


class StubAPI:
    def __init__(self, *, patch_response=None, get_responses=None, post_response=None):
        self.patch_response = patch_response or {}
        self.get_responses = list(get_responses or [])
        self.post_response = post_response or {}
        self.posts = []

    def patch(self, path, body):
        return self.patch_response

    def get(self, path):
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET {path}")
        return self.get_responses.pop(0)

    def post(self, path, body):
        self.posts.append((path, body))
        return self.post_response or {"id": "ticket-1", **body}


class RouteStubAPI:
    def __init__(self, *, get_map=None, post_map=None):
        self.get_map = dict(get_map or {})
        self.post_map = dict(post_map or {})
        self.get_calls = []
        self.post_calls = []
        self.text_calls = []

    def get(self, path):
        self.get_calls.append(path)
        if path not in self.get_map:
            raise AssertionError(f"Unexpected GET {path}")
        value = self.get_map[path]
        return value() if callable(value) else value

    def post(self, path, body):
        self.post_calls.append((path, body))
        if path not in self.post_map:
            raise AssertionError(f"Unexpected POST {path}")
        value = self.post_map[path]
        return value() if callable(value) else value

    def get_text(self, path, accept="text/plain"):
        self.text_calls.append((path, accept))
        if path not in self.get_map:
            raise AssertionError(f"Unexpected GET_TEXT {path}")
        value = self.get_map[path]
        return value() if callable(value) else value


def test_ticket_run_wait_prints_structured_final_receipt(capsys, monkeypatch):
    monkeypatch.setattr(ticket_cmd, "_POLL_INTERVAL", 0)
    monkeypatch.setattr(ticket_cmd, "_WAIT_TIMEOUT", 1)
    monkeypatch.setattr(ticket_cmd.time, "sleep", lambda _: None)
    monkeypatch.setattr(ticket_cmd.time, "time", lambda: 0)

    api = StubAPI(
        patch_response={"id": "ticket-1", "column_id": "in_progress"},
        get_responses=[
            {"id": "ticket-1", "column_id": "done", "is_running": False},
            [
                {
                    "step": "ticket_run_receipt",
                    "summary": "Ticket run succeeded",
                    "success": True,
                    "created_at": "2026-06-10T12:00:00",
                    "receipt": {
                        "status": "succeeded",
                        "attempt_hash": "abc123def456",
                        "agenthub_commit_hash": "abc123def4567890",
                        "base_hash": "fedcba9876543210",
                        "runner_workdir": "/tmp/run-1",
                        "evidence_summary": "pytest -q passed",
                        "next_actions": [
                            "ta ticket attempts proj-1 ticket-1",
                            "ta ticket logs proj-1 ticket-1 --raw",
                        ],
                    },
                }
            ],
        ],
    )
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", wait=True, run_local=False, output="human")

    ticket_cmd._cmd_run(args, api)

    stdout = capsys.readouterr().out
    assert "Ticket run succeeded" in stdout
    assert "Attempt:" in stdout
    assert "abc123def456" in stdout
    assert "pytest -q passed" in stdout
    assert "ta ticket attempts proj-1 ticket-1" in stdout


def test_ticket_create_passes_explicit_base_leaf_id(capsys):
    api = StubAPI(post_response={
        "id": "ticket-1",
        "title": "CLI ticket",
        "column_id": "backlog",
        "base_leaf_id": "leaf_01HZX3CLI0123456789ABCDEFG",
    })
    args = SimpleNamespace(
        file=None,
        title="CLI ticket",
        description=None,
        rationale=None,
        acceptance_criteria=None,
        constraints=None,
        column="backlog",
        priority="medium",
        intent_status="ready",
        base_leaf_id="leaf_01HZX3CLI0123456789ABCDEFG",
        project_id="proj-1",
        output="human",
    )

    ticket_cmd._cmd_create(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/tickets",
            {
                "title": "CLI ticket",
                "column_id": "backlog",
                "description": None,
                "priority": "medium",
                "status": "todo",
                "associated_node_ids": [],
                "associated_edge_ids": [],
                "depends_on_ticket_ids": [],
                "intent_status": "ready",
                "rationale": None,
                "acceptance_criteria": None,
                "constraints": None,
                "base_leaf_id": "leaf_01HZX3CLI0123456789ABCDEFG",
            },
        )
    ]
    assert "Created ticket ticket-1" in capsys.readouterr().out


def test_ticket_logs_renders_structured_failure_event_and_next_commands(capsys):
    api = StubAPI(
        get_responses=[
            [
                {
                    "step": "execution_failed",
                    "summary": "Pytest failed",
                    "success": False,
                    "created_at": "2026-06-10T12:00:00",
                    "event": {
                        "phase": "execution",
                        "status": "failed",
                        "timestamp": "2026-06-10T12:00:00Z",
                        "message": "Pytest failed",
                        "detail": "1 test failed",
                        "hint": "Run the targeted test locally.",
                        "next_commands": [
                            "pytest agent/tests/test_ticket_run_logging.py",
                            "ta ticket logs proj-1 ticket-1 --raw",
                        ],
                    },
                }
            ]
        ]
    )
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", raw=False, output="human")

    ticket_cmd._cmd_logs(args, api)

    stdout = capsys.readouterr().out
    assert "Execution failed" in stdout
    assert "Run the targeted test locally." in stdout
    assert "pytest agent/tests/test_ticket_run_logging.py" in stdout


def test_ticket_run_local_passes_explicit_base_and_agenthub_env(monkeypatch):
    captured = {}

    class LocalAPI:
        base_url = "http://backend:5000"

        def get(self, path):
            if path == "/api/projects/proj-1":
                return {
                    "id": "proj-1",
                    "github_url": "https://github.com/org/repo",
                    "git_mode": "swarm",
                    "project_path": "/repo/worktree",
                }
            if path == "/api/projects/proj-1/tickets/ticket-1":
                return {
                    "id": "ticket-1",
                    "base_leaf_id": "leaf_01HZX3BASE0123456789ABCDEFG",
                }
            raise AssertionError(f"Unexpected GET {path}")

    def fake_run(cmd, cwd, env):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return SimpleNamespace(returncode=0)

    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", output="human")
    monkeypatch.setenv("AGENTHUB_URL", "http://agenthub:8088")
    monkeypatch.setenv("AGENTHUB_API_KEY", "secret")
    monkeypatch.setattr(ticket_cmd, "prepare_local_job", lambda job: {
        **job,
        "base_hash": "leaf_01HZX3BASE0123456789ABCDEFG",
        "agenthub_root_hash": "leaf_01HZX3BASE0123456789ABCDEFG",
    })
    monkeypatch.setattr(ticket_cmd.subprocess, "run", fake_run)

    with patch.object(ticket_cmd.sys, "exit", side_effect=SystemExit(0)):
        try:
            ticket_cmd._run_local(args, LocalAPI())
        except SystemExit as exc:
            assert exc.code == 0

    assert captured["cmd"][-2:] == ["agent.agent_runner", "ticket"]
    assert captured["env"]["BASE_HASH"] == "leaf_01HZX3BASE0123456789ABCDEFG"
    assert captured["env"]["AGENTHUB_ROOT_HASH"] == "leaf_01HZX3BASE0123456789ABCDEFG"
    assert captured["env"]["AGENTHUB_URL"] == "http://agenthub:8088"
    assert captured["env"]["AGENTHUB_API_KEY"] == "secret"


def test_ticket_rerun_current_frontier_hits_explicit_endpoint(capsys):
    api = StubAPI(post_response={
        "id": "ticket-1",
        "column_id": "in_progress",
        "base_leaf_id": "leaf_01HZX3CURRENTFRONTIER01234567",
        "accepted_frontier_id": "leaf_01HZX3CURRENTFRONTIER01234567",
        "attempt_count": 3,
    })
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", attempt_count=3, output="human")

    ticket_cmd._cmd_rerun_current_frontier(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/tickets/ticket-1/rerun-from-current-frontier",
            {"attempt_count": 3},
        )
    ]
    stdout = capsys.readouterr().out
    assert "Attempts" in stdout
    assert "3" in stdout
    assert "current frontier" in stdout
    assert "leaf_01HZX3C" in stdout


def test_ticket_rerun_current_frontier_allows_attempt_override():
    api = StubAPI(post_response={
        "id": "ticket-1",
        "column_id": "in_progress",
        "base_leaf_id": "leaf_01HZX3CURRENTFRONTIER01234567",
        "accepted_frontier_id": "leaf_01HZX3CURRENTFRONTIER01234567",
        "attempt_count": 5,
    })
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", attempt_count=5, output="human")

    ticket_cmd._cmd_rerun_current_frontier(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/tickets/ticket-1/rerun-from-current-frontier",
            {"attempt_count": 5},
        )
    ]


def test_ticket_attempts_next_commands_use_evaluate_choose_accept_flow(capsys):
    api = StubAPI(
        get_responses=[
            [
                {
                    "id": "attempt-2",
                    "status": "validated",
                    "validated": True,
                    "is_winner": False,
                    "integrated": False,
                    "short_commit_hash": "abc123def456",
                    "base_hash": "frontier-123",
                    "attempt_num": 2,
                    "test_status": "passed",
                }
            ]
        ]
    )
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", json=False, output="human")

    ticket_cmd._cmd_attempts(args, api)

    stdout = capsys.readouterr().out
    assert "ta ticket evaluate-attempts proj-1 ticket-1 --latest 1 --include-diff --include-files" in stdout
    assert "ta ticket choose-winner proj-1 ticket-1 attempt-2" in stdout
    assert "ta ticket accept-attempt proj-1 ticket-1 attempt-2" not in stdout


def test_ticket_attempts_omits_choose_winner_when_sibling_is_already_integrated_like(capsys):
    api = StubAPI(
        get_responses=[
            [
                {
                    "id": "attempt-3",
                    "status": "validated",
                    "validated": True,
                    "is_winner": False,
                    "integrated": False,
                    "short_commit_hash": "abc123def456",
                    "base_hash": "frontier-123",
                    "attempt_num": 3,
                    "test_status": "passed",
                },
                {
                    "id": "attempt-2",
                    "status": "release_pr_open",
                    "validated": True,
                    "is_winner": True,
                    "integrated": False,
                    "short_commit_hash": "def456abc123",
                    "base_hash": "frontier-123",
                    "attempt_num": 2,
                    "test_status": "passed",
                },
            ]
        ]
    )
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", json=False, output="human")

    ticket_cmd._cmd_attempts(args, api)

    stdout = capsys.readouterr().out
    assert "ta ticket evaluate-attempts proj-1 ticket-1 --latest 1 --include-diff --include-files" in stdout
    assert "ta ticket choose-winner proj-1 ticket-1 attempt-3" not in stdout


def test_evaluate_attempts_json_collects_review_artifacts_and_recommendation(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [
        {"id": "attempt-2", "attempt_num": 2, "status": "validated"},
        {"id": "attempt-1", "attempt_num": 1, "status": "failed"},
    ]
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-222222222222",
        "summary": "Add winner selection CLI",
        "attempt_num": 2,
        "changed_files": ["cli/commands/ticket.py"],
    }
    attempt_1 = {
        "id": "attempt-1",
        "attempt_id": "attempt-1",
        "ticket_id": "ticket-1",
        "status": "failed",
        "validated": False,
        "is_winner": False,
        "integrated": False,
        "stale": True,
        "stale_reason": "Project frontier moved.",
        "base_hash": "frontier-old",
        "agenthub_commit_hash": None,
        "summary": "Early failed attempt",
        "attempt_num": 1,
        "changed_files": ["cli/commands/attempt.py"],
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
            "/api/projects/proj-1/attempts/attempt-1": attempt_1,
            "/api/projects/proj-1/attempts/attempt-2/files": {
                "files": [{"path": "cli/commands/ticket.py", "status": "modified", "changes": 120}]
            },
            "/api/projects/proj-1/attempts/attempt-1/files": {
                "files": [{"path": "cli/commands/attempt.py", "status": "modified", "changes": 14}]
            },
            "/api/projects/proj-1/attempts/attempt-2/diff?max_bytes=4096": "diff --git a/cli/commands/ticket.py b/cli/commands/ticket.py\n+winner\n",
            "/api/projects/proj-1/attempts/attempt-1/diff?max_bytes=4096": "diff --git a/cli/commands/attempt.py b/cli/commands/attempt.py\n-bad\n",
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_ids=[],
        latest=2,
        include_diff=True,
        include_files=True,
        max_diff_bytes=4096,
        json=True,
        output="json",
    )

    ticket_cmd._cmd_evaluate_attempts(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["project_id"] == "proj-1"
    assert payload["ticket_id"] == "ticket-1"
    assert payload["review_complete"] is True
    assert [item["attempt_id"] for item in payload["attempts"]] == ["attempt-2", "attempt-1"]
    assert payload["recommendation"]["attempt_id"] == "attempt-2"
    assert payload["recommendation"]["next_command"] == "ta ticket choose-winner proj-1 ticket-1 attempt-2"
    assert payload["attempts"][0]["changed_files"] == ["cli/commands/ticket.py"]
    assert payload["attempts"][0]["files"][0]["path"] == "cli/commands/ticket.py"
    assert payload["attempts"][0]["diff"].startswith("diff --git")
    assert "ta attempt diff proj-1 attempt-2 --max-bytes 4096" in payload["attempts"][0]["review_commands"]
    assert payload["attempts"][0]["action_commands"]["choose_winner"] == "ta ticket choose-winner proj-1 ticket-1 attempt-2"
    assert payload["attempts"][1]["recommendation"]["risks"]


def test_evaluate_attempts_json_reads_backend_changed_files_payload(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}]
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-222222222222",
        "summary": "Add winner selection CLI",
        "attempt_num": 2,
        "changed_files": ["cli/commands/ticket.py"],
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
            "/api/projects/proj-1/attempts/attempt-2/files": {
                "changed_files": [
                    {"path": "cli/commands/ticket.py", "status": "modified", "changes": 120}
                ],
                "unavailable_reason": None,
                "next_actions": ["ta attempt diff proj-1 attempt-2"],
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_ids=[],
        latest=1,
        include_diff=False,
        include_files=True,
        max_diff_bytes=None,
        json=True,
        output="json",
    )

    ticket_cmd._cmd_evaluate_attempts(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["attempts"][0]["files"][0]["path"] == "cli/commands/ticket.py"
    assert payload["attempts"][0]["files_error"] is None
    assert payload["attempts"][0]["unavailable_reason"] is None
    assert payload["attempts"][0]["next_actions"] == ["ta attempt diff proj-1 attempt-2"]


def test_evaluate_attempts_json_marks_review_incomplete_when_files_are_unavailable(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}]
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-222222222222",
        "summary": "Add winner selection CLI",
        "attempt_num": 2,
        "changed_files": ["cli/commands/ticket.py"],
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
            "/api/projects/proj-1/attempts/attempt-2/files": {
                "changed_files": [],
                "unavailable_reason": "Git could not inspect the attempt diff in the local checkout.",
                "next_actions": ["Verify the local project checkout can diff the attempt commit."],
            },
            "/api/projects/proj-1/attempts/attempt-2/diff?max_bytes=4096": "diff --git a/cli/commands/ticket.py b/cli/commands/ticket.py\n+winner\n",
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_ids=[],
        latest=1,
        include_diff=True,
        include_files=True,
        max_diff_bytes=4096,
        json=True,
        output="json",
    )

    ticket_cmd._cmd_evaluate_attempts(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["attempts"][0]["files"] == []
    assert payload["attempts"][0]["files_error"] == "Git could not inspect the attempt diff in the local checkout."
    assert payload["attempts"][0]["unavailable_reason"] == "Git could not inspect the attempt diff in the local checkout."
    assert payload["attempts"][0]["next_actions"] == ["Verify the local project checkout can diff the attempt commit."]
    assert payload["review_complete"] is False
    assert payload["recommendation"]["review_complete"] is False


def test_evaluate_attempts_json_marks_review_incomplete_when_diff_is_unavailable(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}]
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-222222222222",
        "summary": "Add winner selection CLI",
        "attempt_num": 2,
        "changed_files": ["cli/commands/ticket.py"],
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
            "/api/projects/proj-1/attempts/attempt-2/files": {
                "changed_files": [
                    {"path": "cli/commands/ticket.py", "status": "modified", "changes": 120}
                ],
                "unavailable_reason": None,
            },
            "/api/projects/proj-1/attempts/attempt-2/diff?max_bytes=4096": {
                "diff": None,
                "bytes": 0,
                "truncated": False,
                "unavailable_reason": "Attempt diff is unavailable for this checkout.",
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_ids=[],
        latest=1,
        include_diff=True,
        include_files=True,
        max_diff_bytes=4096,
        json=True,
        output="json",
    )

    ticket_cmd._cmd_evaluate_attempts(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["attempts"][0]["files_error"] is None
    assert payload["attempts"][0]["diff"] is None
    assert payload["attempts"][0]["diff_error"] == "Attempt diff is unavailable for this checkout."
    assert payload["review_complete"] is False
    assert payload["recommendation"]["review_complete"] is False


def test_evaluate_attempts_json_suppresses_top_level_choose_winner_when_review_is_incomplete(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}]
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-222222222222",
        "summary": "Add winner selection CLI",
        "attempt_num": 2,
        "changed_files": ["cli/commands/ticket.py"],
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
            "/api/projects/proj-1/attempts/attempt-2/files": {
                "changed_files": [],
                "unavailable_reason": "Git could not inspect the attempt diff in the local checkout.",
            },
            "/api/projects/proj-1/attempts/attempt-2/diff?max_bytes=4096": "diff --git a/cli/commands/ticket.py b/cli/commands/ticket.py\n+winner\n",
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_ids=[],
        latest=1,
        include_diff=True,
        include_files=True,
        max_diff_bytes=4096,
        json=True,
        output="json",
    )

    ticket_cmd._cmd_evaluate_attempts(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["review_complete"] is False
    assert payload["recommendation"]["next_command"] is None
    assert payload["attempts"][0]["action_commands"]["choose_winner"] == "ta ticket choose-winner proj-1 ticket-1 attempt-2"
    assert all("choose-winner" not in command for command in payload["next_commands"])


def test_evaluate_attempts_json_omits_choose_winner_when_sibling_is_already_integrated_like(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [
        {"id": "attempt-3", "attempt_num": 3, "status": "validated"},
        {"id": "attempt-2", "attempt_num": 2, "status": "release_pr_open"},
    ]
    attempt_3 = {
        "id": "attempt-3",
        "attempt_id": "attempt-3",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-333333333333",
        "summary": "New candidate",
        "attempt_num": 3,
        "changed_files": ["cli/commands/ticket.py"],
    }
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "release_pr_open",
        "validated": True,
        "is_winner": True,
        "integrated": False,
        "stale": False,
        "stale_reason": None,
        "base_hash": "frontier-123",
        "agenthub_commit_hash": "commit-222222222222",
        "summary": "Already integrated-like",
        "attempt_num": 2,
        "changed_files": ["cli/commands/attempt.py"],
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-3": attempt_3,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_ids=[],
        latest=2,
        include_diff=False,
        include_files=False,
        max_diff_bytes=None,
        json=True,
        output="json",
    )

    ticket_cmd._cmd_evaluate_attempts(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["recommendation"]["next_command"] is None
    assert all("choose_winner" not in item["action_commands"] for item in payload["attempts"])


def test_choose_winner_dry_run_returns_frontier_unchanged_and_next_command(capsys):
    project = {"id": "proj-1", "accepted_frontier_id": "frontier-123"}
    attempts = [
        {"id": "attempt-2", "attempt_num": 2, "status": "validated"},
        {"id": "attempt-1", "attempt_num": 1, "status": "failed"},
    ]
    attempt_2 = {
        "id": "attempt-2",
        "attempt_id": "attempt-2",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "is_winner": False,
        "integrated": False,
        "agenthub_commit_hash": "commit-222222222222",
        "base_hash": "frontier-123",
        "attempt_num": 2,
        "summary": "Add winner selection CLI",
    }
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": project,
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": attempts,
            "/api/projects/proj-1/attempts/attempt-2": attempt_2,
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        reason="best validation",
        dry_run=True,
        expect_frontier="frontier-123",
        json=True,
        output="json",
    )

    ticket_cmd._cmd_choose_winner(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert api.post_calls == []
    assert payload["dry_run"] is True
    assert payload["frontier_changed"] is False
    assert payload["attempt_id"] == "attempt-2"
    assert payload["next_command"] == "ta ticket accept-winner proj-1 ticket-1 attempt-2 --expect-frontier frontier-123"


def test_choose_winner_dry_run_rejects_integrated_sibling_locally_in_json_mode(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-123"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [
                {"id": "attempt-3", "attempt_num": 3, "status": "validated"},
                {"id": "attempt-2", "attempt_num": 2, "status": "release_pr_open"},
            ],
            "/api/projects/proj-1/attempts/attempt-3": {
                "id": "attempt-3",
                "attempt_id": "attempt-3",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": False,
                "integrated": False,
                "agenthub_commit_hash": "commit-333333333333",
                "base_hash": "frontier-123",
                "attempt_num": 3,
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-3",
        reason=None,
        dry_run=True,
        expect_frontier="frontier-123",
        json=True,
        output="json",
    )

    with pytest.raises(SystemExit):
        ticket_cmd._cmd_choose_winner(args, api)

    payload = json.loads(capsys.readouterr().err)
    assert "already has an integrated sibling attempt" in payload["error"]["message"]
    assert "release_pr_open" in payload["error"]["message"]
    assert api.post_calls == []


def test_choose_winner_rejects_frontier_mismatch_locally_in_json_mode(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-live"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": False,
                "integrated": False,
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-live",
                "attempt_num": 2,
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        reason=None,
        dry_run=True,
        expect_frontier="frontier-expected",
        json=True,
        output="json",
    )

    with pytest.raises(SystemExit):
        ticket_cmd._cmd_choose_winner(args, api)

    payload = json.loads(capsys.readouterr().err)
    assert "frontier-expected" in payload["error"]["message"]
    assert api.post_calls == []


def test_accept_winner_requires_chosen_winner_before_posting(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-123"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": False,
                "integrated": False,
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-123",
                "attempt_num": 2,
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier="frontier-123",
        json=True,
        output="json",
    )

    with pytest.raises(SystemExit):
        ticket_cmd._cmd_accept_winner(args, api)

    payload = json.loads(capsys.readouterr().err)
    assert "chosen winner" in payload["error"]["message"]
    assert api.post_calls == []


def test_accept_winner_rejects_stale_attempt_locally_before_posting(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-123"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": True,
                "integrated": False,
                "stale": True,
                "stale_reason": "Project frontier moved.",
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-old",
                "attempt_num": 2,
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier="frontier-123",
        json=True,
        output="json",
    )

    with pytest.raises(SystemExit):
        ticket_cmd._cmd_accept_winner(args, api)

    payload = json.loads(capsys.readouterr().err)
    assert "stale" in payload["error"]["message"].lower()
    assert api.post_calls == []


def test_accept_winner_rejects_stale_base_mismatch_signal_before_posting(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-123"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": True,
                "integrated": False,
                "stale": False,
                "stale_reason": "attempt.base_hash differs from project.accepted_frontier_id.",
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-old",
                "attempt_num": 2,
            },
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier="frontier-123",
        json=True,
        output="json",
    )

    with pytest.raises(SystemExit):
        ticket_cmd._cmd_accept_winner(args, api)

    payload = json.loads(capsys.readouterr().err)
    assert "base_hash differs" in payload["error"]["message"]
    assert api.post_calls == []


@pytest.mark.parametrize(
    ("attempt_payload", "expected_message"),
    [
        (
            {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": True,
                "integrated": False,
                "stale": None,
                "stale_reason": "Cannot determine attempt staleness: attempt.base_hash is not set.",
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": None,
                "attempt_num": 2,
            },
            "Cannot determine attempt staleness",
        ),
        (
            {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": True,
                "integrated": False,
                "stale": False,
                "stale_reason": "Cannot determine attempt staleness: project.accepted_frontier_id is not set.",
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-123",
                "attempt_num": 2,
            },
            "Cannot determine attempt staleness",
        ),
    ],
)
def test_accept_winner_rejects_indeterminate_staleness_locally_before_posting(
    capsys, attempt_payload, expected_message
):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": None},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": attempt_payload,
        }
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier=None,
        json=True,
        output="json",
    )

    with pytest.raises(SystemExit):
        ticket_cmd._cmd_accept_winner(args, api)

    payload = json.loads(capsys.readouterr().err)
    assert expected_message in payload["error"]["message"]
    assert api.post_calls == []


def test_accept_winner_posts_to_accept_endpoint_and_reports_frontier_change(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-123"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": True,
                "integrated": False,
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-123",
                "attempt_num": 2,
                "summary": "Add winner selection CLI",
            },
        },
        post_map={
            "/api/projects/proj-1/tickets/ticket-1/attempts/attempt-2/accept": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "accepted",
                "validated": True,
                "is_winner": True,
                "integrated": True,
                "agenthub_commit_hash": "commit-222222222222",
                "accepted_frontier_id": "commit-222222222222",
                "project": {"accepted_frontier_id": "commit-222222222222"},
            }
        },
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier="frontier-123",
        json=True,
        output="json",
    )

    ticket_cmd._cmd_accept_winner(args, api)

    assert api.post_calls == [
        ("/api/projects/proj-1/tickets/ticket-1/attempts/attempt-2/accept", {})
    ]
    payload = json.loads(capsys.readouterr().out)
    assert payload["frontier_changed"] is True
    assert payload["accepted_frontier_id"] == "commit-222222222222"
    assert "ta ship candidates proj-1" in payload["next_commands"]


def test_accept_winner_reports_no_frontier_change_when_attempt_already_integrated(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "frontier-123"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "accepted"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "accepted",
                "validated": True,
                "is_winner": True,
                "integrated": True,
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "frontier-123",
                "attempt_num": 2,
            },
        },
        post_map={
            "/api/projects/proj-1/tickets/ticket-1/attempts/attempt-2/accept": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "accepted",
                "validated": True,
                "is_winner": True,
                "integrated": True,
                "agenthub_commit_hash": "commit-222222222222",
                "accepted_frontier_id": "commit-222222222222",
                "project": {"accepted_frontier_id": "commit-222222222222"},
            }
        },
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier="frontier-123",
        json=False,
        output="human",
    )

    ticket_cmd._cmd_accept_winner(args, api)

    stdout = capsys.readouterr().out
    assert "Frontier changed:" in stdout
    assert "false" in stdout


def test_accept_winner_reports_no_frontier_change_when_frontier_is_unchanged(capsys):
    api = RouteStubAPI(
        get_map={
            "/api/projects/proj-1": {"id": "proj-1", "accepted_frontier_id": "commit-222222222222"},
            "/api/projects/proj-1/tickets/ticket-1": {"id": "ticket-1"},
            "/api/projects/proj-1/tickets/ticket-1/attempts": [{"id": "attempt-2", "attempt_num": 2, "status": "validated"}],
            "/api/projects/proj-1/attempts/attempt-2": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "validated",
                "validated": True,
                "is_winner": True,
                "integrated": False,
                "agenthub_commit_hash": "commit-222222222222",
                "base_hash": "commit-222222222222",
                "attempt_num": 2,
            },
        },
        post_map={
            "/api/projects/proj-1/tickets/ticket-1/attempts/attempt-2/accept": {
                "id": "attempt-2",
                "attempt_id": "attempt-2",
                "ticket_id": "ticket-1",
                "status": "accepted",
                "validated": True,
                "is_winner": True,
                "integrated": True,
                "agenthub_commit_hash": "commit-222222222222",
                "accepted_frontier_id": "commit-222222222222",
                "project": {"accepted_frontier_id": "commit-222222222222"},
            }
        },
    )
    args = SimpleNamespace(
        project_id="proj-1",
        ticket_id="ticket-1",
        attempt_id="attempt-2",
        expect_frontier="commit-222222222222",
        json=True,
        output="json",
    )

    ticket_cmd._cmd_accept_winner(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["frontier_changed"] is False
