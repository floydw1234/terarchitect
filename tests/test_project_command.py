import json
from types import SimpleNamespace

import pytest

from cli import __main__
from cli.commands import project as project_cmd


class StubAPI:
    def __init__(self):
        self.posts = []
        self.puts = []
        self.gets = []

    def post(self, path, body):
        self.posts.append((path, body))
        return {"id": "proj-1", **body}

    def put(self, path, body):
        self.puts.append((path, body))
        return {"id": "proj-1", **body}

    def get(self, path):
        self.gets.append(path)
        return {
            "project_id": "proj-1",
            "accepted_frontier_id": None,
            "has_accepted_frontier": False,
            "ticket_counts": {"missing_base_leaf_id": 1, "stale": 0},
            "attempt_counts": {"missing_base_hash": 0, "missing_parent_leaf_id": 0},
            "local_path": {"path": "/repo/demo", "exists": True, "is_directory": True},
            "tickets_missing_base_leaf_ids": [],
            "stale_tickets": [],
            "attempts_missing_lineage": [],
        }


class StubGitHubProjectAPI(StubAPI):
    def post(self, path, body):
        self.posts.append((path, body))
        return {
            "id": "proj-1",
            "name": body.get("name"),
            "github_url": body.get("github_url"),
            "github_ref": body.get("base_ref"),
            "github_resolved_sha": "a" * 40,
            "accepted_frontier_id": "leaf_01HZX3ABCD9EF0123456789XYZ",
            "execution_mode": body.get("execution_mode", "docker"),
            "git_mode": body.get("git_mode", "swarm"),
        }


def test_project_create_parser_accepts_github_first_without_project_path():
    parser = __main__.build_parser()

    args = parser.parse_args(
        [
            "project",
            "create",
            "--name",
            "vid_splitter",
            "--github-url",
            "https://github.com/floydw1234/vid_splitter",
            "--base-ref",
            "main",
            "--import-to-agenthub",
            "--execution-mode",
            "docker",
            "--git-mode",
            "swarm",
        ]
    )

    assert args.group == "project"
    assert args.project_cmd == "create"
    assert args.github_url == "https://github.com/floydw1234/vid_splitter"
    assert args.project_path is None
    assert args.base_ref == "main"
    assert args.import_to_agenthub is True


def test_project_create_passes_explicit_frontier_id(capsys):
    api = StubAPI()
    args = SimpleNamespace(
        config=None,
        name="CLI Project",
        description=None,
        github_url="https://github.com/floydw1234/cli-project",
        base_ref=None,
        import_to_agenthub=False,
        execution_mode="docker",
        git_mode="swarm",
        project_path=None,
        accepted_frontier_id="leaf_01HZX3ABCD9EF0123456789XYZ",
        existing_repo=True,
        output="human",
    )

    project_cmd._cmd_create(args, api)

    assert api.posts == [
        (
            "/api/projects",
            {
                "name": "CLI Project",
                "github_url": "https://github.com/floydw1234/cli-project",
                "execution_mode": "docker",
                "git_mode": "swarm",
                "accepted_frontier_id": "leaf_01HZX3ABCD9EF0123456789XYZ",
                "is_existing_repo": True,
            },
        )
    ]
    assert "Created project" in capsys.readouterr().out


def test_project_create_github_first_includes_github_fields_in_payload_and_output(capsys):
    api = StubGitHubProjectAPI()
    args = SimpleNamespace(
        config=None,
        name="vid_splitter",
        description=None,
        github_url="https://github.com/floydw1234/vid_splitter",
        base_ref="main",
        import_to_agenthub=True,
        execution_mode="docker",
        git_mode="swarm",
        project_path=None,
        accepted_frontier_id=None,
        existing_repo=True,
        output="human",
    )

    project_cmd._cmd_create(args, api)

    assert api.posts == [
        (
            "/api/projects",
            {
                "name": "vid_splitter",
                "github_url": "https://github.com/floydw1234/vid_splitter",
                "base_ref": "main",
                "execution_mode": "docker",
                "git_mode": "swarm",
                "is_existing_repo": True,
                "import_to_agenthub": True,
            },
        )
    ]
    stdout = capsys.readouterr().out
    assert "proj-1" in stdout
    assert "https://github.com/floydw1234/vid_splitter" in stdout
    assert "main" in stdout
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in stdout
    assert "leaf_01HZX3ABCD9EF0123456789XYZ" in stdout


def test_project_create_json_output_includes_github_fields(capsys):
    api = StubGitHubProjectAPI()
    args = SimpleNamespace(
        config=None,
        name="vid_splitter",
        description=None,
        github_url="https://github.com/floydw1234/vid_splitter",
        base_ref="main",
        import_to_agenthub=True,
        execution_mode="docker",
        git_mode="swarm",
        project_path=None,
        accepted_frontier_id=None,
        existing_repo=True,
        output="json",
    )

    project_cmd._cmd_create(args, api)

    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == "proj-1"
    assert payload["github_url"] == "https://github.com/floydw1234/vid_splitter"
    assert payload["github_ref"] == "main"
    assert payload["github_resolved_sha"] == "a" * 40
    assert payload["accepted_frontier_id"] == "leaf_01HZX3ABCD9EF0123456789XYZ"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            dict(
                name="CLI Project",
                github_url=None,
                project_path=None,
                base_ref=None,
                execution_mode="docker",
            ),
            "Provide --github-url for GitHub-first onboarding or --project-path for legacy local onboarding.",
        ),
        (
            dict(
                name="CLI Project",
                github_url=None,
                project_path="/repo/demo",
                base_ref="main",
                execution_mode="docker",
            ),
            "--base-ref requires --github-url.",
        ),
        (
            dict(
                name="CLI Project",
                github_url="https://github.com/floydw1234/vid_splitter",
                project_path=None,
                base_ref=None,
                execution_mode="local",
            ),
            "--execution-mode local requires --project-path.",
        ),
    ],
)
def test_project_create_rejects_incompatible_inputs(args, message, capsys):
    api = StubAPI()
    namespace = SimpleNamespace(
        config=None,
        description=None,
        import_to_agenthub=False,
        git_mode="swarm",
        accepted_frontier_id=None,
        existing_repo=False,
        output="human",
        **args,
    )

    with pytest.raises(SystemExit):
        project_cmd._cmd_create(namespace, api)

    assert api.posts == []
    assert message in capsys.readouterr().err


def test_project_update_passes_explicit_frontier_id():
    api = StubAPI()
    args = SimpleNamespace(
        project_id="proj-1",
        name=None,
        description=None,
        github_url=None,
        execution_mode=None,
        git_mode=None,
        project_path=None,
        accepted_frontier_id="leaf_01HZX3ABCD9EF0123456789XYZ",
        output="json",
    )

    project_cmd._cmd_update(args, api)

    assert api.puts == [
        (
            "/api/projects/proj-1",
            {"accepted_frontier_id": "leaf_01HZX3ABCD9EF0123456789XYZ"},
        )
    ]


def test_project_import_agenthub_root_posts_explicit_path(capsys):
    api = StubAPI()
    args = SimpleNamespace(
        project_id="proj-1",
        path="/repo/demo",
        output="human",
    )

    project_cmd._cmd_import_agenthub_root(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/import-agenthub-root",
            {"path": "/repo/demo"},
        )
    ]
    assert "Imported AgentHub root for project proj-1" in capsys.readouterr().out


def test_project_migration_status_gets_expected_route(capsys):
    api = StubAPI()
    args = SimpleNamespace(project_id="proj-1", output="human")

    project_cmd._cmd_migration_status(args, api)

    assert api.gets == ["/api/projects/proj-1/migration/status"]
    assert "Migration status for project proj-1" in capsys.readouterr().out


def test_project_migration_set_frontier_posts_explicit_frontier_id():
    api = StubAPI()
    args = SimpleNamespace(
        project_id="proj-1",
        accepted_frontier_id="leaf_01HZX3REPAIR0123456789ABCDE",
        output="json",
    )

    project_cmd._cmd_migration_set_frontier(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/migration/set-frontier",
            {"accepted_frontier_id": "leaf_01HZX3REPAIR0123456789ABCDE"},
        )
    ]


def test_project_migration_backfill_ticket_bases_posts_dry_run_flag(capsys):
    api = StubAPI()
    args = SimpleNamespace(
        project_id="proj-1",
        dry_run=True,
        output="human",
    )

    project_cmd._cmd_migration_backfill_ticket_bases(args, api)

    assert api.posts == [
        (
            "/api/projects/proj-1/migration/backfill-ticket-bases",
            {"dry_run": True},
        )
    ]
    assert "Backfill ticket bases for project proj-1" in capsys.readouterr().out
