import json
import urllib.error

import pytest

from cli import __main__
from cli._api import APIError
from cli._output import die, print_receipt


def test_global_json_alias_sets_output_to_json():
    parser = __main__.build_parser()

    args = parser.parse_args(["--json", "project", "list"])
    __main__.normalize_output_args(args)

    assert args.output == "json"


def test_global_json_alias_matches_output_json():
    parser = __main__.build_parser()

    alias_args = parser.parse_args(["--json", "project", "list"])
    explicit_args = parser.parse_args(["--output", "json", "project", "list"])

    __main__.normalize_output_args(alias_args)
    __main__.normalize_output_args(explicit_args)

    assert alias_args.output == explicit_args.output == "json"


def test_die_renders_human_api_error_with_actionable_fields(capsys):
    error = APIError(
        502,
        "compose failed",
        detail="Coordinator rejected the candidate graph",
        hint="Re-run compose after fixing the candidate membership.",
        request_id="req-123",
        phase="compose",
        next_commands=["ta ship candidates proj", "ta ship candidate proj cand-1"],
    )

    with pytest.raises(SystemExit) as exc:
        die(error, output="human")

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "compose failed" in stderr
    assert "Coordinator rejected the candidate graph" in stderr
    assert "Re-run compose after fixing the candidate membership." in stderr
    assert "req-123" in stderr
    assert "compose" in stderr
    assert "ta ship candidates proj" in stderr
    assert "ta ship candidate proj cand-1" in stderr


def test_die_renders_json_error_to_stderr_without_polluting_stdout(capsys):
    error = APIError(
        409,
        "candidate blocked",
        detail="Validation blockers remain.",
        request_id="req-456",
        next_commands=["ta ship candidate proj cand-1"],
    )

    with pytest.raises(SystemExit):
        die(error, output="json")

    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["status"] == 409
    assert payload["error"]["message"] == "candidate blocked"
    assert payload["error"]["detail"] == "Validation blockers remain."
    assert payload["error"]["request_id"] == "req-456"
    assert payload["error"]["next_commands"] == ["ta ship candidate proj cand-1"]


def test_main_renders_connection_failure_as_json_error_envelope(capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["ta", "--json", "--api-url", "http://127.0.0.1:9", "project", "list"],
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        ),
    )

    with pytest.raises(SystemExit) as exc:
        __main__.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error"]["status"] == 0
    assert payload["error"]["message"] == "Cannot reach http://127.0.0.1:9"
    assert "Connection refused" in payload["error"]["detail"]
    assert (
        payload["error"]["hint"]
        == "Is the backend running? Set TERARCHITECT_API_URL if needed."
    )


def test_print_receipt_renders_fields_and_next_commands(capsys):
    print_receipt(
        "Accepted attempt",
        fields=[
            ("Attempt", "attempt-1"),
            ("Status", "accepted"),
        ],
        next_commands=[
            "ta attempt show proj attempt-1",
            "ta ship candidates proj",
        ],
    )

    stdout = capsys.readouterr().out
    assert "Accepted attempt" in stdout
    assert "Attempt:  attempt-1" in stdout
    assert "Status:   accepted" in stdout
    assert "ta attempt show proj attempt-1" in stdout
    assert "ta ship candidates proj" in stdout
