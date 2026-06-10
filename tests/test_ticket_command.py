from types import SimpleNamespace

from cli.commands import ticket as ticket_cmd


class StubAPI:
    def __init__(self, *, patch_response=None, get_responses=None):
        self.patch_response = patch_response or {}
        self.get_responses = list(get_responses or [])

    def patch(self, path, body):
        return self.patch_response

    def get(self, path):
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET {path}")
        return self.get_responses.pop(0)


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
