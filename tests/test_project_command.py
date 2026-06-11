from types import SimpleNamespace

from cli.commands import project as project_cmd


class StubAPI:
    def __init__(self):
        self.posts = []
        self.puts = []

    def post(self, path, body):
        self.posts.append((path, body))
        return {"id": "proj-1", **body}

    def put(self, path, body):
        self.puts.append((path, body))
        return {"id": "proj-1", **body}


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
