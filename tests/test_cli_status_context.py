import argparse
import json

from cli.commands import context, status


class FakeAPI:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path, None))
        if path == "/api/projects/proj/tickets/ticket-1/ledger":
            return {
                "project": {"id": "proj", "name": "Demo"},
                "ticket": {"id": "ticket-1", "title": "Status lane", "column_id": "done"},
                "accepted_attempt": {"id": "attempt-1", "status": "accepted"},
                "promotion_candidate": {"id": "cand-1", "status": "composed"},
                "ship_run": {"id": "run-1", "status": "ready_to_ship"},
                "evidence_summary": {
                    "canonical_source": ["EvidenceBundle", "EvidenceRun", "EvidenceCheck"],
                    "bundle_count": 1,
                    "run_count": 1,
                    "check_counts": {"passed": 2},
                },
                "timeline": [
                    {"kind": "ticket", "label": "Ticket created", "id": "ticket-1"},
                    {"kind": "attempt", "label": "Attempt #1 published", "id": "attempt-1"},
                    {"kind": "ship_run", "label": "ShipRun queued", "id": "run-1"},
                ],
                "next_commands": [
                    "ta context proj --ticket ticket-1 --agent",
                    "ta ship run proj run-1",
                ],
            }
        if path == "/api/projects/proj/tickets/ticket-1/context?agent=true":
            return {
                "project": {"id": "proj", "name": "Demo", "project_path": "/repo/demo"},
                "ticket": {"id": "ticket-1", "title": "Context lane", "column_id": "in_progress"},
                "attempts": [{"id": "attempt-1", "status": "accepted", "attempt_num": 1}],
                "channels": {
                    "project": "project-proj",
                    "ticket": "ticket-ticket1",
                    "candidate": "cand-demo-1",
                },
                "recent_events": [
                    {"event_type": "attempt_published", "message": "Attempt published"},
                    {"event_type": "human_feedback", "message": "Needs more logs"},
                ],
                "candidate": {"id": "cand-1", "status": "valid"},
                "ship_run": {"id": "run-1", "status": "ready_to_ship"},
                "paths": {
                    "project_path": "/repo/demo",
                    "runner_workdir_hint": "/tmp/terarchitect_runner_abc123",
                    "recovery_artifact_hints": ["/tmp/terarchitect_runner_abc123/plan/recovery.md"],
                },
                "worker_context": {
                    "current_ticket": {"id": "ticket-1", "title": "Context lane"},
                    "notes": [],
                },
                "next_commands": [
                    "ta status proj --ticket ticket-1",
                    "ta ticket logs proj ticket-1 --raw",
                ],
            }
        raise AssertionError(f"Unexpected GET path: {path}")


def _parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    sub.required = True
    status.register(sub)
    context.register(sub)
    return parser


def test_status_and_context_parsers_support_ticket_and_agent_flags():
    parser = _parser()

    status_args = parser.parse_args(["status", "proj", "--ticket", "ticket-1"])
    assert status_args.group == "status"
    assert status_args.project_id == "proj"
    assert status_args.ticket_id == "ticket-1"

    context_args = parser.parse_args(["context", "proj", "--ticket", "ticket-1", "--agent"])
    assert context_args.group == "context"
    assert context_args.project_id == "proj"
    assert context_args.ticket_id == "ticket-1"
    assert context_args.agent is True


def test_status_human_output_renders_ledger_and_next_commands(capsys):
    args = argparse.Namespace(
        project_id="proj",
        ticket_id="ticket-1",
        output="human",
    )

    status.run(args, FakeAPI())

    stdout = capsys.readouterr().out
    assert "Ticket status" in stdout
    assert "Attempt:" in stdout
    assert "attempt-1" in stdout
    assert "Evidence:" in stdout
    assert "1 bundle(s), 1 run(s)" in stdout
    assert "[attempt] Attempt #1 published" in stdout
    assert "ta ship run proj run-1" in stdout


def test_status_json_output_emits_machine_readable_payload(capsys):
    args = argparse.Namespace(
        project_id="proj",
        ticket_id="ticket-1",
        output="json",
    )

    status.run(args, FakeAPI())

    payload = json.loads(capsys.readouterr().out)
    assert payload["ticket"]["id"] == "ticket-1"
    assert payload["timeline"][1]["kind"] == "attempt"


def test_context_human_output_renders_channels_paths_and_events(capsys):
    args = argparse.Namespace(
        project_id="proj",
        ticket_id="ticket-1",
        agent=True,
        output="human",
    )

    context.run(args, FakeAPI())

    stdout = capsys.readouterr().out
    assert "Agent context" in stdout
    assert "Channels:" in stdout
    assert "ticket-ticket1" in stdout
    assert "Runner WD:   /tmp/terarchitect_runner_abc123" in stdout
    assert "attempt_published" in stdout
    assert "ta ticket logs proj ticket-1 --raw" in stdout


def test_context_json_output_includes_worker_context(capsys):
    args = argparse.Namespace(
        project_id="proj",
        ticket_id="ticket-1",
        agent=True,
        output="json",
    )

    context.run(args, FakeAPI())

    payload = json.loads(capsys.readouterr().out)
    assert payload["worker_context"]["current_ticket"]["id"] == "ticket-1"
    assert payload["paths"]["project_path"] == "/repo/demo"
