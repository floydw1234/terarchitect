from unittest.mock import patch

from agent.workspace_composer import composer


def test_prepare_runtime_repo_clones_ephemeral_repo_when_project_path_missing(tmp_path):
    runtime_dir = tmp_path / "runtime"
    repo_dir = runtime_dir / "repo"

    def fake_clone(github_url, repo_path):
        assert github_url == "https://github.com/example/demo"
        assert repo_path == str(repo_dir)
        repo_dir.mkdir(parents=True, exist_ok=True)

    with patch("agent.workspace_composer.composer._clone_repo", side_effect=fake_clone):
        repo_path, runtime = composer._prepare_runtime_repo(
            "",
            "https://github.com/example/demo",
            str(runtime_dir),
        )

    assert repo_path == str(repo_dir)
    assert runtime == {
        "requested_project_path": None,
        "repo_source": "github_ephemeral_clone",
        "cache_source": "github_url",
        "ephemeral_repo": True,
    }


def test_compose_workspace_merges_each_leaf_hash(tmp_path):
    project_path = tmp_path / "repo"
    worktree_path = tmp_path / "workspace-ws123456"
    project_path.mkdir()
    worktree_path.mkdir()
    merge_calls = []

    def fake_git(args, cwd, check=True, timeout=60):
        if args[:1] == ["merge"]:
            merge_calls.append(list(args))
        if args == ["rev-parse", "HEAD"]:
            return type("R", (), {"returncode": 0, "stdout": "c" * 40 + "\n", "stderr": ""})()
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def fake_run(args, **kwargs):
        if args[:3] == ["git", "cat-file", "-e"]:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return type("R", (), {"returncode": 0, "stdout": "origin/main\n", "stderr": ""})()
        raise AssertionError(f"Unexpected subprocess args: {args}")

    with patch("agent.workspace_composer.composer._ensure_commit", return_value=True), \
         patch("agent.workspace_composer.composer._git", side_effect=fake_git), \
         patch("agent.workspace_composer.composer.subprocess.run", side_effect=fake_run):
        _, composed_hash = composer._compose_workspace(
            ["a" * 40, "b" * 40],
            str(project_path),
            "ws123456",
            None,
            str(tmp_path),
        )

    assert composed_hash == "c" * 40
    assert merge_calls == [
        ["merge", "--no-ff", "--allow-unrelated-histories", "-m", "workspace: merge aaaaaaaaaaaa", "a" * 40],
        ["merge", "--no-ff", "--allow-unrelated-histories", "-m", "workspace: merge bbbbbbbbbbbb", "b" * 40],
    ]


def test_run_once_reports_ephemeral_runtime_when_composing_without_project_path(tmp_path):
    repo_dir = tmp_path / "repo"
    worktree_dir = tmp_path / "worktree"
    repo_dir.mkdir()
    worktree_dir.mkdir()
    posts = []

    payload = {
        "workspace": {"id": "ws-12345678", "base_root_hash": "b" * 40},
        "project": {
            "name": "Demo",
            "project_path": "",
            "github_url": "https://github.com/example/demo",
        },
        "leaf_hashes": ["a" * 40],
    }

    def fake_post(path, body=None):
        posts.append((path, body))
        return {}

    with patch.dict("os.environ", {
        "WORKSPACE_ID": "ws-12345678",
        "TERARCHITECT_API_URL": "http://backend",
    }, clear=False):
        with patch("agent.workspace_composer.composer._api_get", return_value=payload), \
             patch("agent.workspace_composer.composer._prepare_runtime_repo", return_value=(str(repo_dir), {
                 "requested_project_path": None,
                 "repo_source": "github_ephemeral_clone",
                 "cache_source": "github_url",
                 "ephemeral_repo": True,
             })), \
             patch("agent.workspace_composer.composer._compose_workspace", return_value=(str(worktree_dir), "c" * 40)), \
             patch("agent.workspace_composer.composer._run_tests", return_value=("passed", "")), \
             patch("agent.workspace_composer.composer._get_changed_files", return_value=["src/app.py"]), \
             patch("agent.workspace_composer.composer._cleanup_worktree"), \
             patch("agent.workspace_composer.composer._api_post", side_effect=fake_post):
            assert composer.run_once() is True

    composed_posts = [body for path, body in posts if path.endswith("/composed")]
    assert len(composed_posts) == 1
    assert composed_posts[0]["runtime"] == {
        "requested_project_path": None,
        "repo_source": "github_ephemeral_clone",
        "cache_source": "github_url",
        "ephemeral_repo": True,
        "project_path": str(repo_dir),
    }


def test_run_once_preserves_no_project_path_failure_when_no_github_url():
    posts = []

    payload = {
        "workspace": {"id": "ws-12345678", "base_root_hash": None},
        "project": {
            "name": "Demo",
            "project_path": "",
            "github_url": "",
        },
        "leaf_hashes": ["a" * 40],
    }

    def fake_post(path, body=None):
        posts.append((path, body))
        return {}

    with patch.dict("os.environ", {
        "WORKSPACE_ID": "ws-12345678",
        "TERARCHITECT_API_URL": "http://backend",
    }, clear=False), \
         patch("agent.workspace_composer.composer._api_get", return_value=payload), \
         patch("agent.workspace_composer.composer._api_post", side_effect=fake_post):
        assert composer.run_once() is True

    failed_posts = [body for path, body in posts if path.endswith("/fail")]
    assert len(failed_posts) == 1
    assert failed_posts[0]["failure_type"] == "no_project_path"
    assert failed_posts[0]["runtime"] == {
        "requested_project_path": None,
        "repo_source": "unavailable",
        "cache_source": "none",
        "ephemeral_repo": False,
    }
