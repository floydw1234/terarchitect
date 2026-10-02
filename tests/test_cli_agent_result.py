"""CLI tests for agent-result JSON envelopes."""

import argparse
import json

import pytest

from cli.commands import attempt as attempt_cmd
from cli.commands import ship as ship_cmd
from cli.commands import status as status_cmd

from tests.test_agent_result import STABLE_KEYS as EXPECTED_KEYS


def test_status_json_stdout_only_agent_envelope(capsys):
    ledger = {
        "project": {"id": "proj", "shipped_frontier": "d" * 40},
        "ticket": {"id": "ticket-1"},
        "accepted_attempt": None,
        "promotion_candidate": None,
        "ship_run": None,
        "evidence_summary": {},
        "attempts": [{"id": "attempt-1", "status": "validated", "test_status": "passed"}],
        "jobs": [],
        "timeline": [],
        "next_commands": [],
    }

    class FakeAPI:
        def get(self, path):
            assert path == "/api/projects/proj/tickets/ticket-1/ledger"
            return ledger

    args = argparse.Namespace(project_id="proj", ticket_id="ticket-1", output="json")
    with pytest.raises(SystemExit) as exc:
        status_cmd.run(args, FakeAPI())
    assert exc.value.code == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert set(payload.keys()) == EXPECTED_KEYS
    assert payload["schema_version"] == 1
    assert payload["status"] in {"shipped", "failed", "needs_input", "running"}


def test_attempt_show_json_failed_exits_nonzero(capsys):
    class FakeAPI:
        def get(self, path):
            if path.endswith("/attempts/attempt-1"):
                return {
                    "id": "attempt-1",
                    "ticket_id": "ticket-1",
                    "status": "failed",
                    "validation_error": "boom",
                }
            if path == "/api/projects/proj":
                return {"shipped_frontier": "a" * 40}
            raise AssertionError(path)

    args = argparse.Namespace(
        attempt_cmd="show",
        project_id="proj",
        attempt_id="attempt-1",
        output="json",
        json=True,
    )
    with pytest.raises(SystemExit) as exc:
        attempt_cmd._dispatch(args, FakeAPI())
    assert exc.value.code == 1

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert payload["failure_reason"] == "boom"


def test_ship_run_show_json_envelope(capsys):
    class FakeAPI:
        def get(self, path):
            if path == "/api/projects/proj/ship/runs/run-1":
                return {
                    "id": "run-1",
                    "status": "ready_to_ship",
                    "promotion_candidate_id": "cand-1",
                    "base_main_hash": "a" * 40,
                }
            if path == "/api/projects/proj":
                return {"id": "proj", "shipped_frontier": "a" * 40}
            raise AssertionError(path)

    args = argparse.Namespace(
        ship_cmd="run",
        project_id="proj",
        run_id="run-1",
        json=True,
        output="json",
    )
    with pytest.raises(SystemExit) as exc:
        ship_cmd._dispatch(args, FakeAPI())
    assert exc.value.code == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert set(payload.keys()) == EXPECTED_KEYS
    assert payload["ship_run_id"] == "run-1"
    assert payload["status"] == "needs_input"
