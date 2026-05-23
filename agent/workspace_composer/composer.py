"""
Workspace composer for Terarchitect Phase 9 — Composite Workspace.

Composes a candidate codebase state from selected AgentHub leaves into a
temporary git worktree. Does NOT push to origin or open a PR. The composed
state exists only locally for inspection, testing, and optional blessing.

Flow:
  1. Coordinator pre-claims a CompositeWorkspace and passes WORKSPACE_ID.
  2. Composer fetches workspace data (selected leaf hashes, project path).
  3. Creates a temporary git worktree from the project's shipped_frontier.
  4. Fetches each selected leaf bundle from AgentHub.
  5. Merges leaves in dependency order (wave_num, then attempt_num).
  6. Runs WORKSPACE_TEST_COMMAND if configured.
  7. Reports composed (preview_ready) or failed (conflicted / test_failed).

Required env:
  TERARCHITECT_API_URL
  TERARCHITECT_WORKER_API_KEY

Optional env:
  WORKSPACE_ID          — pre-claimed workspace
  AGENTHUB_URL / AGENTHUB_API_KEY
  WORKSPACE_TEST_COMMAND — shell command to run tests
  GIT_USER_NAME / GIT_USER_EMAIL
"""

import json
import os
import re
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


def _ah_url() -> str:
    return _env("AGENTHUB_URL").rstrip("/")


def _ah_headers() -> dict:
    return {"Authorization": f"Bearer {_env('AGENTHUB_API_KEY')}"}


def _git(args: list, cwd: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    name = _env("GIT_USER_NAME", "Terarchitect Workspace Composer")
    email = _env("GIT_USER_EMAIL", "workspace@terarchitect.local")
    env.update({
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
    })
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, env=env, check=check,
    )


def _api_get(path: str) -> Optional[dict]:
    try:
        resp = requests.get(_base_url() + path, headers=_auth_headers(), timeout=15)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[composer] GET {path} error: {e}", file=sys.stderr)
    return None


def _api_post(path: str, body: dict = None) -> Optional[dict]:
    try:
        resp = requests.post(
            _base_url() + path, json=body or {}, headers=_auth_headers(), timeout=30,
        )
        if resp.ok:
            return resp.json()
        print(f"[composer] POST {path} → {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[composer] POST {path} error: {e}", file=sys.stderr)
    return None


def _fetch_bundle(commit_hash: str, dest_dir: str) -> Optional[str]:
    url = _ah_url()
    if not url:
        return None
    try:
        resp = requests.get(
            f"{url}/api/git/fetch/{commit_hash}",
            headers=_ah_headers(), timeout=120, stream=True,
        )
        if not resp.ok:
            return None
        path = os.path.join(dest_dir, f"{commit_hash[:12]}.bundle")
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return path
    except Exception as e:
        print(f"[composer] Bundle fetch {commit_hash[:12]} error: {e}", file=sys.stderr)
        return None


def _ensure_commit(commit_hash: str, worktree: str, tmp_dir: str) -> bool:
    r = subprocess.run(["git", "cat-file", "-e", commit_hash], cwd=worktree, capture_output=True)
    if r.returncode == 0:
        return True
    bundle = _fetch_bundle(commit_hash, tmp_dir)
    if not bundle:
        return False
    r2 = subprocess.run(["git", "bundle", "unbundle", bundle], cwd=worktree, capture_output=True, timeout=60)
    return r2.returncode == 0


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

class ComposeError(Exception):
    pass


class TestFailureError(Exception):
    pass


def _compose_workspace(
    leaf_hashes: list[str],
    project_path: str,
    workspace_id: str,
    base_root_hash: Optional[str],
    tmp_dir: str,
) -> tuple[str, str]:
    """Compose leaves into a temporary git worktree.

    Returns (worktree_path, composed_commit_hash).
    Raises ComposeError on merge conflict.
    """
    worktree_path = os.path.join(tmp_dir, f"workspace-{workspace_id[:8]}")

    # Ensure all commits are available in the main repo
    for h in leaf_hashes:
        if not _ensure_commit(h, project_path, tmp_dir):
            print(f"[composer] Warning: could not fetch {h[:12]} from AgentHub", file=sys.stderr)

    # Determine starting point
    _git(["fetch", "origin", "main"], cwd=project_path, check=False)
    _git(["fetch", "origin", "master"], cwd=project_path, check=False)

    start_ref = base_root_hash or "HEAD"
    # Verify start_ref exists
    check = subprocess.run(["git", "cat-file", "-e", start_ref], cwd=project_path, capture_output=True)
    if check.returncode != 0:
        # Fall back to origin/main
        start_ref = "origin/main"
        for branch in ("main", "master"):
            r = subprocess.run(["git", "rev-parse", f"origin/{branch}"], cwd=project_path, capture_output=True)
            if r.returncode == 0:
                start_ref = f"origin/{branch}"
                break

    # Create worktree at start_ref
    _git(["worktree", "add", "--detach", worktree_path, start_ref], cwd=project_path)
    print(f"[composer] Worktree created at {worktree_path} from {start_ref[:12] if len(start_ref) > 12 else start_ref}")

    # Merge each leaf
    for h in leaf_hashes:
        r = _git(
            ["merge", "--no-ff", "--allow-unrelated-histories", "-m",
             f"workspace: merge {h[:12]}"],
            cwd=worktree_path, check=False,
        )
        if r.returncode != 0:
            detail = (r.stdout + r.stderr)[:2000]
            subprocess.run(["git", "merge", "--abort"], cwd=worktree_path, capture_output=True)
            raise ComposeError(f"Merge conflict for {h[:12]}:\n{detail}")
        print(f"[composer] Merged {h[:12]}")

    head_r = _git(["rev-parse", "HEAD"], cwd=worktree_path)
    composed_hash = head_r.stdout.strip()
    return worktree_path, composed_hash


def _run_tests(worktree: str) -> tuple[str, str]:
    test_cmd = _env("WORKSPACE_TEST_COMMAND") or _env("MERGE_TEST_COMMAND")
    if not test_cmd:
        return "skipped", ""
    print(f"[composer] Running tests: {test_cmd}")
    r = subprocess.run(test_cmd, shell=True, cwd=worktree, capture_output=True, text=True, timeout=600)
    output = (r.stdout + r.stderr)[-4000:]
    if r.returncode != 0:
        return "failed", output
    print("[composer] Tests passed.")
    return "passed", output


def _get_changed_files(worktree: str, base_ref: str) -> list[str]:
    r = _git(["diff", f"{base_ref}...HEAD", "--name-only"], cwd=worktree, check=False)
    if r.returncode != 0:
        return []
    return [f for f in r.stdout.splitlines() if f.strip()]


def _cleanup_worktree(project_path: str, worktree_path: str) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=project_path, capture_output=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_once() -> bool:
    workspace_id = _env("WORKSPACE_ID")

    if workspace_id:
        data = _api_get(f"/api/worker/workspaces/{workspace_id}")
        if not data:
            print(f"[composer] Could not fetch workspace {workspace_id}", file=sys.stderr)
            return False
    else:
        resp = requests.post(
            _base_url() + "/api/worker/workspaces/next",
            json={}, headers=_auth_headers(), timeout=15,
        )
        if resp.status_code == 204:
            return False
        if not resp.ok:
            print(f"[composer] Claim failed: {resp.status_code}", file=sys.stderr)
            return False
        data = resp.json()

    ws = data["workspace"]
    workspace_id = ws["id"]
    project = data["project"]
    leaf_hashes = data.get("leaf_hashes") or []
    project_path = (project.get("project_path") or "").strip()
    base_root_hash = ws.get("base_root_hash")

    print(f"[composer] Workspace {workspace_id} project {project['name']!r} leaves={len(leaf_hashes)}")

    if not leaf_hashes:
        _api_post(f"/api/worker/workspaces/{workspace_id}/fail", {
            "error": "No leaf hashes to compose.",
            "failure_type": "no_leaves",
        })
        return True

    if not project_path or not os.path.isdir(project_path):
        _api_post(f"/api/worker/workspaces/{workspace_id}/fail", {
            "error": f"project_path not found: {project_path!r}",
            "failure_type": "no_project_path",
        })
        return True

    with tempfile.TemporaryDirectory() as tmp_dir:
        worktree_path = None
        try:
            # Compose
            worktree_path, composed_hash = _compose_workspace(
                leaf_hashes, project_path, workspace_id, base_root_hash, tmp_dir
            )

            # Tests
            test_status, test_output = _run_tests(worktree_path)
            if test_status == "failed":
                _api_post(f"/api/worker/workspaces/{workspace_id}/fail", {
                    "error": f"Tests failed:\n{test_output[:1000]}",
                    "failure_type": "test_failed",
                    "test_status": "failed",
                    "test_output": test_output[-4000:],
                    "composed_commit_hash": composed_hash,
                })
                return True

            # Changed files
            start_ref = base_root_hash or "HEAD"
            changed_files = _get_changed_files(worktree_path, start_ref)

            _api_post(f"/api/worker/workspaces/{workspace_id}/composed", {
                "composed_commit_hash": composed_hash,
                "test_status": test_status,
                "test_output": test_output[-4000:] if test_output else "",
                "changed_files": changed_files,
            })
            print(f"[composer] Workspace {workspace_id} composed. hash={composed_hash[:12]} "
                  f"tests={test_status} files={len(changed_files)}")

        except ComposeError as e:
            print(f"[composer] Conflict: {e}", file=sys.stderr)
            _api_post(f"/api/worker/workspaces/{workspace_id}/fail", {
                "error": str(e),
                "failure_type": "conflicted",
            })
        finally:
            if worktree_path and os.path.isdir(worktree_path):
                _cleanup_worktree(project_path, worktree_path)

    return True


def main() -> None:
    if not _env("TERARCHITECT_WORKER_API_KEY"):
        print("[composer] Warning: TERARCHITECT_WORKER_API_KEY not set", file=sys.stderr)
    run_once()
    sys.exit(0)
