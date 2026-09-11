import argparse
from unittest.mock import patch

from cli._api import APIError
from cli.commands import ship


def _ship_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="group")
    sub.required = True
    ship.register(sub)
    return parser


def test_ship_parser_registers_doctor_and_happy_path_subcommands():
    parser = _ship_parser()

    doctor_args = parser.parse_args(["ship", "doctor", "proj"])
    assert doctor_args.ship_cmd == "doctor"
    assert doctor_args.project_id == "proj"

    happy_args = parser.parse_args(["ship", "happy-path", "proj", "--ticket", "ticket-1"])
    assert happy_args.ship_cmd == "happy-path"
    assert happy_args.project_id == "proj"
    assert happy_args.ticket_id == "ticket-1"

    help_text = parser._subparsers._group_actions[0].choices["ship"].format_help()
    assert "doctor" in help_text
    assert "happy-path" in help_text
    dry_args = parser.parse_args(["ship", "dry-compose", "proj", "cand-1"])
    assert dry_args.ship_cmd == "dry-compose"
    assert dry_args.candidate_id == "cand-1"
    assert parser.parse_args(["ship", "diff", "proj", "cand-1"]).ship_cmd == "diff"
    assert parser.parse_args(["ship", "timeline", "proj", "cand-1"]).ship_cmd == "timeline"


def test_ship_run_cli_preserves_api_error_context():
    error = APIError(
        502,
        "PR merge failed",
        detail="GraphQL: Base branch protection prevents merge",
        hint="Run ta ship doctor proj before retrying.",
        request_id="ship-run:req-1",
        phase="merge",
        next_commands=["ta ship doctor proj", "ta ship run proj run-1"],
    )

    class FailingAPI:
        def post(self, path, body=None):
            raise error

    args = argparse.Namespace(
        ship_cmd="ship-run",
        project_id="proj",
        run_id="run-1",
        method="merge",
        json=False,
        output="human",
    )

    with patch("cli.commands.ship.die", side_effect=lambda err, **kwargs: (_ for _ in ()).throw(SystemExit(err))):
        try:
            ship._dispatch(args, FailingAPI())
        except SystemExit as exc:
            rendered = exc.code

    assert rendered is error


def test_ship_doctor_cli_renders_receipt(capsys):
    class FakeAPI:
        def get(self, path):
            assert path == "/api/projects/proj/ship/doctor"
            return {
                "project_id": "proj",
                "status": "warn",
                "checks": [
                    {"name": "db_schema", "status": "pass", "summary": "Ship Room tables are present."},
                    {"name": "github_auth", "status": "warn", "summary": "gh CLI is unavailable in backend runtime."},
                ],
                "next_commands": ["ta ship doctor proj"],
            }

    args = argparse.Namespace(ship_cmd="doctor", project_id="proj", json=False, output="human")
    ship._dispatch(args, FakeAPI())

    stdout = capsys.readouterr().out
    assert "Ship doctor: WARN" in stdout
    assert "db_schema" in stdout
    assert "github_auth" in stdout


def test_ship_parser_registers_sync_and_compose_run():
    parser = _ship_parser()

    compose_args = parser.parse_args(
        ["ship", "compose-candidate", "proj", "cand-1", "--sync"]
    )
    assert compose_args.ship_cmd == "compose-candidate"
    assert compose_args.sync is True

    happy_args = parser.parse_args(
        ["ship", "happy-path", "proj", "--ticket", "ticket-1", "--sync"]
    )
    assert happy_args.sync is True

    run_args = parser.parse_args(["ship", "compose-run", "proj", "run-1"])
    assert run_args.ship_cmd == "compose-run"
    assert run_args.run_id == "run-1"


def test_compose_candidate_sync_runs_local_shipper(capsys):
    calls: list[tuple[str, str]] = []

    class FakeAPI:
        base_url = "http://localhost:5010"

        def post(self, path, body=None):
            assert path == "/api/projects/proj/ship/candidates/cand-1/compose"
            return {"id": "run-1", "status": "queued", "promotion_candidate_id": "cand-1"}

        def get(self, path):
            assert path == "/api/projects/proj/ship/runs/run-1"
            calls.append(("get", path))
            return {
                "id": "run-1",
                "status": "ready_to_ship",
                "promotion_candidate_id": "cand-1",
            }

    with patch("cli.commands.ship.run_local_shipper", side_effect=lambda url, run_id: calls.append(("shipper", run_id)) or 0):
        args = argparse.Namespace(
            ship_cmd="compose-candidate",
            project_id="proj",
            candidate_id="cand-1",
            sync=True,
            json=False,
            output="human",
        )
        ship._dispatch(args, FakeAPI())

    assert ("shipper", "run-1") in calls
    stdout = capsys.readouterr().out
    assert "ready_to_ship" in stdout


def test_compose_run_sync_for_queued_run(capsys):
    seen: dict[str, int] = {"get": 0}

    class FakeAPI:
        base_url = "http://localhost:5010"

        def get(self, path):
            seen["get"] += 1
            if seen["get"] == 1:
                return {"id": "run-1", "status": "queued", "promotion_candidate_id": "cand-1"}
            return {"id": "run-1", "status": "ready_to_ship", "promotion_candidate_id": "cand-1"}

    with patch("cli.commands.ship.run_local_shipper", return_value=0) as shipper:
        args = argparse.Namespace(
            ship_cmd="compose-run",
            project_id="proj",
            run_id="run-1",
            json=False,
            output="human",
        )
        ship._dispatch(args, FakeAPI())

    shipper.assert_called_once_with("http://localhost:5010", "run-1")
    assert "ready_to_ship" in capsys.readouterr().out


def test_happy_path_sync_compose_then_retries_ship(capsys):
    posts: list[dict] = []

    class FakeAPI:
        base_url = "http://localhost:5010"

        def post(self, path, body=None):
            assert path == "/api/projects/proj/ship/happy-path"
            posts.append(body or {})
            if len(posts) == 1:
                return {
                    "status": "queued",
                    "ship_run_id": "run-1",
                    "candidate_id": "cand-1",
                    "attempt_id": "attempt-1",
                }
            return {
                "status": "shipped",
                "ship_run_id": "run-1",
                "candidate_id": "cand-1",
                "attempt_id": "attempt-1",
                "shipped_commit_hash": "b" * 40,
            }

        def get(self, path):
            return {"id": "run-1", "status": "ready_to_ship"}

    with patch("cli.commands.ship.run_local_shipper", return_value=0) as shipper:
        args = argparse.Namespace(
            ship_cmd="happy-path",
            project_id="proj",
            ticket_id="ticket-1",
            method="merge",
            sync=True,
            json=False,
            output="human",
        )
        ship._dispatch(args, FakeAPI())

    shipper.assert_called_once_with("http://localhost:5010", "run-1")
    assert len(posts) == 2
    stdout = capsys.readouterr().out
    assert "shipped" in stdout.lower()


def test_ship_happy_path_cli_posts_expected_endpoint(capsys):
    class FakeAPI:
        def post(self, path, body=None):
            assert path == "/api/projects/proj/ship/happy-path"
            assert body == {"ticket_id": "ticket-1", "merge_method": "squash"}
            return {
                "status": "shipped",
                "attempt_id": "attempt-1",
                "candidate_id": "candidate-1",
                "ship_run_id": "run-1",
                "shipped_commit_hash": "a" * 40,
                "next_commands": ["ta ship run proj run-1"],
            }

    args = argparse.Namespace(
        ship_cmd="happy-path",
        project_id="proj",
        ticket_id="ticket-1",
        method="squash",
        json=False,
        output="human",
    )
    ship._dispatch(args, FakeAPI())

    stdout = capsys.readouterr().out
    assert "Ship happy path" in stdout
    assert "ticket-1" in stdout
    assert "aaaaaaaaaaaa" in stdout
