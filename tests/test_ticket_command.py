from types import SimpleNamespace
from unittest.mock import patch

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
    })
    args = SimpleNamespace(project_id="proj-1", ticket_id="ticket-1", output="human")

    ticket_cmd._cmd_rerun_current_frontier(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/tickets/ticket-1/rerun-from-current-frontier",
            {},
        )
    ]
    stdout = capsys.readouterr().out
    assert "current frontier" in stdout
    assert "leaf_01HZX3C" in stdout
