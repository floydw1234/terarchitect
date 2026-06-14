from unittest.mock import patch

from agent.shipper import shipper


def test_prepare_runtime_repo_clones_ephemeral_repo_when_project_path_missing(tmp_path):
    runtime_dir = tmp_path / "runtime"
    repo_dir = runtime_dir / "repo"

    def fake_clone(github_url, repo_path):
        assert github_url == "https://github.com/example/demo"
        assert repo_path == str(repo_dir)
        repo_dir.mkdir(parents=True, exist_ok=True)

    with patch("agent.shipper.shipper._clone_repo", side_effect=fake_clone):
        repo_path, runtime = shipper._prepare_runtime_repo(
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


def test_run_once_reports_ephemeral_runtime_when_shipping_without_project_path(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    posts = []

    run_payload = {
        "run": {"id": "run-12345678", "wave_num": 3, "promotion_candidate_id": "cand-1"},
        "candidate": {"id": "cand-1"},
        "project": {
            "name": "Demo",
            "project_path": "",
            "github_url": "https://github.com/example/demo",
        },
        "commit_hashes": ["a" * 40],
        "membership": {},
    }

    def fake_git(args, cwd, check=True, timeout=60):
        if args == ["rev-parse", "HEAD"]:
            return type("R", (), {"returncode": 0, "stdout": "b" * 40 + "\n", "stderr": ""})()
        if args == ["diff", "origin/main...HEAD", "--name-only"]:
            return type("R", (), {"returncode": 0, "stdout": "src/app.py\n", "stderr": ""})()
        if args == ["push", "-u", "origin", "terarchitect/release/wave-3-run12345", "--force-with-lease"]:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(f"Unexpected git args: {args}")

    def fake_subprocess_run(args, **kwargs):
        if args == ["git", "rev-parse", "origin/main"]:
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        raise AssertionError(f"Unexpected subprocess args: {args}")

    def fake_post(path, body=None):
        posts.append((path, body))
        return {}

    with patch.dict("os.environ", {
        "SHIP_RUN_ID": "run-12345678",
        "TERARCHITECT_API_URL": "http://backend",
    }, clear=False):
        with patch("agent.shipper.shipper._api_get", return_value=run_payload), \
             patch("agent.shipper.shipper._prepare_runtime_repo", return_value=(str(repo_dir), {
                 "requested_project_path": None,
                 "repo_source": "github_ephemeral_clone",
                 "cache_source": "github_url",
                 "ephemeral_repo": True,
             })), \
             patch("agent.shipper.shipper._compose_release_branch", return_value=("terarchitect/release/wave-3-run12345", "c" * 40)), \
             patch("agent.shipper.shipper._run_tests", return_value=("passed", "")), \
             patch("agent.shipper.shipper._git", side_effect=fake_git), \
             patch("agent.shipper.shipper.subprocess.run", side_effect=fake_subprocess_run), \
             patch("agent.shipper.shipper._get_changed_files", return_value=["src/app.py"]), \
             patch("agent.shipper.shipper._open_release_pr", return_value=("https://github.com/example/demo/pull/1", 1)), \
             patch("agent.shipper.shipper._post_to_channel"), \
             patch("agent.shipper.shipper._api_post", side_effect=fake_post):
            assert shipper.run_once() is True

    composed_posts = [body for path, body in posts if path.endswith("/composed")]
    assert len(composed_posts) == 1
    assert composed_posts[0]["runtime"] == {
        "requested_project_path": None,
        "repo_source": "github_ephemeral_clone",
        "cache_source": "github_url",
        "ephemeral_repo": True,
        "project_path": str(repo_dir),
    }
