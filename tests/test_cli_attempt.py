import argparse

import pytest

from cli._api import APIError
from cli.commands import attempt, ticket


class FakeAPI:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path, None))
        if path.startswith("/api/projects/proj/attempts/attempt-1/files"):
            return {"files": [{"path": "src/app.py", "status": "modified", "changes": 12}]}
        if path.startswith("/api/projects/proj/attempts/attempt-1"):
            return {
                "id": "attempt-1",
                "ticket_id": "ticket-1",
                "status": "proposed",
                "short_commit_hash": "abc123def456",
                "wave_num": 2,
                "attempt_num": 3,
                "summary": "Add CLI flow",
                "changed_files": ["src/app.py", "tests/test_app.py"],
            }
        if path.startswith("/api/projects/proj/attempts"):
            return [{
                "id": "attempt-1",
                "ticket_id": "ticket-1",
                "status": "proposed",
                "short_commit_hash": "abc123def456",
                "wave_num": 2,
                "changed_files": ["src/app.py"],
            }]
        if path.startswith("/api/projects/proj/tickets/ticket-1/attempts"):
            return [{
                "id": "attempt-1",
                "status": "proposed",
                "short_commit_hash": "abc123def456",
                "wave_num": 2,
                "attempt_num": 3,
                "test_status": "passed",
            }]
        raise AssertionError(f"Unexpected GET path: {path}")

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        return {
            "id": "attempt-1",
            "ticket_id": "ticket-1",
            "status": "accepted" if path.endswith("/accept") else "rejected",
            "short_commit_hash": "abc123def456",
            "wave_num": 2,
        }

    def get_text(self, path, accept="text/plain"):
        self.calls.append(("GET_TEXT", path, accept))
        return "diff --git a/src/app.py b/src/app.py\n+hello\n"


def _attempt_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    sub.required = True
    attempt.register(sub)
    return parser


def _ticket_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    sub.required = True
    ticket.register(sub)
    return parser


def test_attempt_parser_supports_required_subcommands():
    parser = _attempt_parser()

    args = parser.parse_args(["attempt", "list", "proj", "--ticket", "ticket-1", "--status", "pending", "--json"])
    assert args.group == "attempt"
    assert args.attempt_cmd == "list"
    assert args.project_id == "proj"
    assert args.ticket_id == "ticket-1"
    assert args.status == "pending"
    assert args.json is True

    args = parser.parse_args(["attempt", "diff", "proj", "attempt-1", "--file", "src/app.py", "--max-bytes", "1024"])
    assert args.attempt_cmd == "diff"
    assert args.file_path == "src/app.py"
    assert args.max_bytes == 1024


def test_ticket_parser_supports_attempt_aliases():
    parser = _ticket_parser()

    args = parser.parse_args(["ticket", "attempts", "proj", "ticket-1", "--json"])
    assert args.ticket_cmd == "attempts"
    assert args.json is True

    args = parser.parse_args(["ticket", "reject-attempt", "proj", "ticket-1", "attempt-1", "--reason", "needs work"])
    assert args.ticket_cmd == "reject-attempt"
    assert args.reason == "needs work"


def test_attempt_list_uses_project_attempt_endpoint(capsys):
    api = FakeAPI()
    args = argparse.Namespace(
        attempt_cmd="list",
        project_id="proj",
        ticket_id="ticket-1",
        status="pending",
        json=False,
        output="human",
    )

    attempt._dispatch(args, api)

    assert api.calls[0] == ("GET", "/api/projects/proj/attempts?ticket_id=ticket-1&status=pending", None)
    stdout = capsys.readouterr().out
    assert "attempt show proj attempt-1" in stdout


def test_attempt_diff_uses_text_endpoint(capsys):
    api = FakeAPI()
    args = argparse.Namespace(
        attempt_cmd="diff",
        project_id="proj",
        attempt_id="attempt-1",
        file_path="src/app.py",
        max_bytes=512,
        output="human",
    )

    attempt._dispatch(args, api)

    assert api.calls[0] == (
        "GET_TEXT",
        "/api/projects/proj/attempts/attempt-1/diff?file=src%2Fapp.py&max_bytes=512",
        "text/plain, application/json",
    )
    assert "+hello" in capsys.readouterr().out


def test_attempt_show_404_reports_actionable_hint(capsys):
    class MissingAPI(FakeAPI):
        def get(self, path):
            raise APIError(404, "missing")

    args = argparse.Namespace(
        attempt_cmd="show",
        project_id="proj",
        attempt_id="attempt-1",
        json=False,
        output="human",
    )

    with pytest.raises(SystemExit):
        attempt._dispatch(args, MissingAPI())

    stderr = capsys.readouterr().err
    assert "ta ticket attempts proj <ticket_id>" in stderr


def test_ticket_accept_attempt_hits_accept_endpoint(capsys):
    api = FakeAPI()
    args = argparse.Namespace(
        ticket_cmd="accept-attempt",
        project_id="proj",
        ticket_id="ticket-1",
        attempt_id="attempt-1",
        reason=None,
        json=False,
        output="human",
    )

    ticket._dispatch(args, api)

    assert api.calls[0] == (
        "POST",
        "/api/projects/proj/tickets/ticket-1/attempts/attempt-1/accept",
        {},
    )
    assert "ta ship candidates proj" in capsys.readouterr().out
