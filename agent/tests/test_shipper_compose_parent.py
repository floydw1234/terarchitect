import subprocess
from pathlib import Path

from agent.shipper import shipper


def _init_repo(path: Path) -> tuple[str, str]:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.com", "commit", "--allow-empty", "-m", "base"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    (path / "feat.txt").write_text("x\n")
    subprocess.run(["git", "add", "feat.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t.com", "commit", "-m", "attempt"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    attempt = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()
    return base, attempt


def test_compose_single_commit_merge_parent_is_shipped_frontier(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    base_hash, attempt_hash = _init_repo(repo)

    def fake_ensure(commit, project_path, tmp_dir):
        subprocess.run(["git", "fetch", ".", commit], cwd=project_path, check=False, capture_output=True)
        return True

    monkeypatch.setattr(shipper, "_ensure_commit", fake_ensure)
    monkeypatch.setattr(shipper, "_agenthub_receipt_exists", lambda _h: True)
    monkeypatch.setattr(shipper, "_push_repo_bundle_to_agenthub", lambda _p: None)

    subprocess.run(["git", "remote", "add", "origin", str(repo)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", base_hash], cwd=repo, check=True, capture_output=True)

    branch, base_main_hash = shipper._compose_release_branch(
        [attempt_hash],
        str(repo),
        "abcd1234",
        str(tmp_path),
        base_ref=base_hash,
    )
    assert base_main_hash == base_hash
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    parent = subprocess.check_output(["git", "rev-parse", f"{head}^"], cwd=repo, text=True).strip()
    assert parent == base_hash
