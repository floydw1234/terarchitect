from types import SimpleNamespace

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


def test_project_create_passes_explicit_frontier_id(capsys):
    api = StubAPI()
    args = SimpleNamespace(
        config=None,
        name="CLI Project",
        description=None,
        github_url=None,
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
                "execution_mode": "docker",
                "git_mode": "swarm",
                "accepted_frontier_id": "leaf_01HZX3ABCD9EF0123456789XYZ",
                "is_existing_repo": True,
            },
        )
    ]
    assert "Created project: proj-1" in capsys.readouterr().out


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
