import argparse

from cli import __main__
from cli.commands import publish as publish_cmd


class FakeAPI:
    def __init__(self):
        self.calls = []

    def post(self, path, body=None):
        self.calls.append((path, body))
        return {
            "project_id": "proj",
            "target": "github",
            "branch": "main",
            "remote": "origin",
            "selected_commit": "a" * 40,
            "selected_attempt_id": "attempt-1",
            "fast_forward": True,
            "pushed": body.get("push", False),
        }


def test_publish_parser_supports_selection_and_push_flags():
    parser = __main__.build_parser()

    args = parser.parse_args([
        "publish",
        "proj",
        "--attempt-id",
        "attempt-1",
        "--branch",
        "release",
        "--push",
        "--force",
        "--json",
    ])

    assert args.group == "publish"
    assert args.project_id == "proj"
    assert args.attempt_id == "attempt-1"
    assert args.branch == "release"
    assert args.push is True
    assert args.force is True
    assert args.json is True


def test_publish_cli_posts_expected_payload(capsys):
    api = FakeAPI()
    args = argparse.Namespace(
        project_id="proj",
        target="github",
        attempt_id="attempt-1",
        commit=None,
        branch="main",
        push=False,
        force=False,
        json=False,
        output="human",
    )

    publish_cmd._dispatch(args, api)

    assert api.calls == [
        (
            "/api/projects/proj/publish",
            {
                "target": "github",
                "push": False,
                "force": False,
                "attempt_id": "attempt-1",
                "branch": "main",
            },
        )
    ]
    stdout = capsys.readouterr().out
    assert "Publish dry-run" in stdout
    assert "aaaaaaaaaaaa" in stdout
