"""
Merge agent for terarchitect swarm mode.

After each wave of tickets completes, the coordinator dispatches this agent to:
  1. Claim the next queued merge run from the backend
  2. Fetch the DAG leaves / commit hashes for the wave's tickets
  3. Merge all commits into a single branch (wave-{N}-merge)
  4. Run the test suite (if MERGE_TEST_COMMAND is set)
  5. Push the merge branch / create a PR (if GitHub is configured)
  6. Report success or failure back to the backend
     — on failure, auto-creates a fix ticket via the backend /fail endpoint

Usage (standalone — coordinator mode):
    python -m agent.merger

Usage (one-shot — given an explicit run ID, e.g. from CI):
    MERGE_RUN_ID=<uuid> python -m agent.merger

Required env:
    TERARCHITECT_API_URL            — backend base URL
    TERARCHITECT_WORKER_API_KEY     — worker auth token

Optional env:
    MERGE_RUN_ID            — skip claim step, work on this specific run
    AGENTHUB_URL            — agenthub base URL (fallback for commit hashes)
    AGENTHUB_API_KEY        — agenthub auth key
    MERGE_TEST_COMMAND      — shell command to run tests (e.g. "pytest tests/ -x")
    MERGE_BRANCH_PREFIX     — prefix for merge branches (default: "wave")
    GIT_USER_NAME           — git commit identity
    GIT_USER_EMAIL          — git commit identity
    GH_TOKEN / GITHUB_TOKEN — GitHub token for PR creation (optional)
"""

import os
import subprocess
import sys
import tempfile
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _base_url() -> str:
    return _env("TERARCHITECT_API_URL", "http://localhost:5010").rstrip("/")


def _auth_headers() -> dict:
    key = _env("TERARCHITECT_WORKER_API_KEY")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _api_get(path: str):
    try:
        resp = requests.get(_base_url() + path, headers=_auth_headers(), timeout=15)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[merger] GET {path} failed: {e}", file=sys.stderr)
    return None


def _api_post(path: str, body: dict = None):
    try:
        resp = requests.post(
            _base_url() + path,
            json=body or {},
            headers=_auth_headers(),
            timeout=30,
        )
        if resp.ok:
            return resp.json()
        print(f"[merger] POST {path} → {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"[merger] POST {path} failed: {e}", file=sys.stderr)
    return None


def _git(args: list, cwd: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    name = _env("GIT_USER_NAME", "Terarchitect Merger")
    email = _env("GIT_USER_EMAIL", "merger@terarchitect.local")
    env.update({"GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
                 "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email})
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, env=env, check=check,
    )


# ---------------------------------------------------------------------------
# Agenthub fallback: fetch commit hashes from leaves if backend has none
# ---------------------------------------------------------------------------

def _ah_leaves() -> list[str]:
    ah_url = _env("AGENTHUB_URL").rstrip("/")
    ah_key = _env("AGENTHUB_API_KEY")
    if not ah_url:
        return []
    try:
        resp = requests.get(
            f"{ah_url}/api/git/leaves",
            headers={"Authorization": f"Bearer {ah_key}"},
            timeout=15,
        )
        if resp.ok:
            leaves = resp.json() or []
            return [leaf["hash"] for leaf in leaves if leaf.get("hash")]
    except Exception:
        pass
    return []


def _ah_fetch_bundle(commit_hash: str, dest_dir: str) -> Optional[str]:
    """Download a git bundle for `commit_hash` from agenthub.
    Returns path to the bundle file, or None on failure."""
    ah_url = _env("AGENTHUB_URL").rstrip("/")
    ah_key = _env("AGENTHUB_API_KEY")
    if not ah_url:
        return None
    try:
        resp = requests.get(
            f"{ah_url}/api/git/fetch/{commit_hash}",
            headers={"Authorization": f"Bearer {ah_key}"},
            timeout=60,
            stream=True,
        )
        if not resp.ok:
            return None
        path = os.path.join(dest_dir, f"{commit_hash[:12]}.bundle")
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return path
    except Exception as e:
        print(f"[merger] Bundle fetch {commit_hash[:12]}: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Core merge logic
# ---------------------------------------------------------------------------

class MergeConflictError(Exception):
    pass


class TestFailureError(Exception):
    pass


def _unbundle_commit(commit_hash: str, project_path: str, tmp_dir: str) -> bool:
    """Download and unbundle a commit from agenthub into the local repo.
    Returns True on success."""
    bundle_path = _ah_fetch_bundle(commit_hash, tmp_dir)
    if not bundle_path:
        return False
    r = subprocess.run(
        ["git", "bundle", "unbundle", bundle_path],
        cwd=project_path, capture_output=True, text=True, timeout=60,
    )
    return r.returncode == 0


def _swarm_branch() -> str:
    return _env("AGENTHUB_BRANCH", "swarm") or "swarm"


def _merge_commits(
    commit_hashes: list[str],
    project_path: str,
    wave_num: int,
    tmp_dir: str,
) -> str:
    """
    Merge all commit_hashes into the swarm branch, starting from origin/swarm
    (previous waves' merged state).  Returns the final merge commit hash.
    Raises MergeConflictError on conflict.
    """
    branch = _swarm_branch()

    # Ensure all commits are available locally (unbundle from agenthub if needed)
    for h in commit_hashes:
        r = subprocess.run(
            ["git", "cat-file", "-e", h],
            cwd=project_path, capture_output=True,
        )
        if r.returncode != 0:
            if not _unbundle_commit(h, project_path, tmp_dir):
                print(f"[merger] Warning: could not fetch {h[:12]} from agenthub", file=sys.stderr)

    # Start from origin/swarm (carries all previous wave merges).
    # Fall back to origin/main if the swarm branch doesn't exist yet (first wave).
    _git(["fetch", "origin", "main"], cwd=project_path, check=False)
    fetch_r = _git(["fetch", "origin", branch], cwd=project_path, check=False)
    if fetch_r.returncode == 0:
        _git(["checkout", "-B", branch, f"origin/{branch}"], cwd=project_path)
        print(f"[merger] Starting wave-{wave_num} merge from origin/{branch}")
    else:
        print(f"[merger] origin/{branch} not found — creating from origin/main")
        _git(["checkout", "-B", branch, "origin/main"], cwd=project_path)

    # Ensure swarm is up to date with main — if main has moved ahead (e.g. a hotfix was
    # merged directly), pull it in before applying wave commits so swarm never falls behind.
    ancestor_check = subprocess.run(
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        cwd=project_path, capture_output=True,
    )
    if ancestor_check.returncode != 0:
        print(f"[merger] origin/main is ahead of {branch} — merging main in first")
        r = _git(
            ["merge", "--no-ff", "-m", f"chore: sync {branch} with main before wave-{wave_num}", "origin/main"],
            cwd=project_path,
            check=False,
        )
        if r.returncode != 0:
            conflict_detail = r.stdout + r.stderr
            subprocess.run(["git", "merge", "--abort"], cwd=project_path, capture_output=True)
            raise MergeConflictError(
                f"Conflict merging main into {branch} before wave-{wave_num}:\n{conflict_detail[:2000]}"
            )
        print(f"[merger] Synced {branch} with main")

    # Merge all wave agent commits sequentially (not octopus — better conflict messages)
    for h in commit_hashes:
        r = _git(
            ["merge", "--no-ff", "-m", f"wave-{wave_num}: merge {h[:12]}", h],
            cwd=project_path,
            check=False,
        )
        if r.returncode != 0:
            conflict_detail = r.stdout + r.stderr
            subprocess.run(["git", "merge", "--abort"], cwd=project_path, capture_output=True)
            raise MergeConflictError(
                f"Conflict merging {h[:12]} into {branch}:\n{conflict_detail[:2000]}"
            )

    # Get final hash
    head = _git(["rev-parse", "HEAD"], cwd=project_path)
    return head.stdout.strip()


def _run_tests(project_path: str) -> None:
    """Run MERGE_TEST_COMMAND. Raises TestFailureError on failure."""
    test_cmd = _env("MERGE_TEST_COMMAND")
    if not test_cmd:
        return
    print(f"[merger] Running tests: {test_cmd}")
    r = subprocess.run(
        test_cmd, shell=True, cwd=project_path,
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        output = (r.stdout + r.stderr)[-3000:]
        raise TestFailureError(f"Tests failed (exit {r.returncode}):\n{output}")
    print("[merger] Tests passed.")


def _push_swarm(project_path: str, branch: str) -> bool:
    """Push the swarm branch to origin. Returns True on success.
    Uses --force-with-lease so concurrent pushes are caught rather than silently overwritten."""
    r = _git(["push", "-u", "origin", branch, "--force-with-lease"], cwd=project_path, check=False)
    if r.returncode != 0:
        print(f"[merger] Push to origin/{branch} failed: {r.stderr[:500]}", file=sys.stderr)
        return False
    print(f"[merger] Pushed origin/{branch} — VP can merge to main whenever ready.")
    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _claim_run() -> Optional[dict]:
    """Claim the next queued merge run. Returns full response dict or None."""
    resp = requests.post(
        _base_url() + "/api/worker/merge/next",
        json={},
        headers=_auth_headers(),
        timeout=15,
    )
    if resp.status_code == 204:
        return None  # Nothing to do
    if resp.ok:
        return resp.json()
    print(f"[merger] Claim failed: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
    return None


def run_once() -> bool:
    """Claim and execute one merge run. Returns True if work was done."""
    run_id_override = _env("MERGE_RUN_ID")

    if run_id_override:
        # Coordinator pre-claimed this run; fetch its data without re-claiming.
        try:
            resp = requests.get(
                _base_url() + f"/api/worker/merge/{run_id_override}",
                headers=_auth_headers(),
                timeout=15,
            )
            if not resp.ok:
                print(f"[merger] Could not fetch run {run_id_override}: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
                return False
            data = resp.json()
        except Exception as e:
            print(f"[merger] Fetch run {run_id_override} error: {e}", file=sys.stderr)
            return False
    else:
        data = _claim_run()
        if not data:
            return False

    run = data["run"]
    run_id = run["id"]
    wave_num = run["wave_num"]
    project = data["project"]
    commit_hashes = data.get("commit_hashes") or []
    project_path = (project.get("project_path") or "").strip()

    print(f"[merger] Claimed merge run {run_id} (wave {wave_num}, project {project['name']})")
    print(f"[merger] Commit hashes from backend: {commit_hashes}")

    # Fallback: use agenthub leaves if backend has no commit hashes
    if not commit_hashes:
        commit_hashes = _ah_leaves()
        print(f"[merger] Using agenthub leaves as fallback: {commit_hashes}")

    if not commit_hashes:
        _api_post(f"/api/worker/merge/{run_id}/fail", {
            "error": "No commit hashes found for wave tickets and agenthub returned no leaves.",
            "fix_ticket_title": f"[wave-{wave_num}] No commits to merge — investigate swarm agent output",
            "fix_ticket_description": "The merge agent found no commit hashes for wave "
                                      f"{wave_num}. Check that swarm agents ran `ah push` "
                                      "successfully and that commit_hash was reported to /complete.",
        })
        return True

    if not project_path or not os.path.isdir(project_path):
        _api_post(f"/api/worker/merge/{run_id}/fail", {
            "error": f"project_path not found or empty: {project_path!r}",
        })
        return True

    branch = _swarm_branch()

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            merge_hash = _merge_commits(commit_hashes, project_path, wave_num, tmp_dir)
            print(f"[merger] Merge complete: {merge_hash[:12]}")
        except MergeConflictError as e:
            print(f"[merger] Merge conflict: {e}", file=sys.stderr)
            _api_post(f"/api/worker/merge/{run_id}/fail", {
                "error": str(e),
                "fix_ticket_title": f"[wave-{wave_num}] Resolve merge conflicts",
                "fix_ticket_description": (
                    f"The merge agent encountered conflicts merging wave {wave_num} tickets.\n\n"
                    f"Details:\n{str(e)[:2000]}"
                ),
            })
            return True

        try:
            _run_tests(project_path)
        except TestFailureError as e:
            print(f"[merger] Test failure: {e}", file=sys.stderr)
            _api_post(f"/api/worker/merge/{run_id}/fail", {
                "error": str(e),
                "fix_ticket_title": f"[wave-{wave_num}] Fix test failures after merge",
                "fix_ticket_description": (
                    f"Tests failed after merging wave {wave_num}.\n\n"
                    f"Output:\n{str(e)[:2000]}"
                ),
            })
            return True

        _push_swarm(project_path, branch)
        _api_post(f"/api/worker/merge/{run_id}/done", {
            "commit_hash": merge_hash,
            "pr_url": "",
        })
        print(f"[merger] Wave {wave_num} merge run complete. hash={merge_hash[:12]} swarm branch updated.")
        return True


def main() -> None:
    if not _env("TERARCHITECT_WORKER_API_KEY"):
        print("[merger] Warning: TERARCHITECT_WORKER_API_KEY not set — auth may fail", file=sys.stderr)

    did_work = run_once()
    sys.exit(0 if did_work else 0)  # Always exit 0; coordinator decides whether to retry
