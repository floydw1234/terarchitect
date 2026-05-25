"""
Shipper agent for terarchitect swarm mode.

Replaces the old merger. Instead of accumulating changes into a persistent swarm branch,
the shipper composes accepted AgentHub leaves directly onto a release branch based on
current main, runs tests, and opens one release PR for human review.

Flow:
  1. Coordinator pre-claims a ShipRun and passes SHIP_RUN_ID.
  2. Shipper fetches the run data (wave tickets + accepted attempt hashes).
  3. For each accepted attempt: downloads the AgentHub bundle, unbundles into local repo.
  4. Creates release branch: terarchitect/release/wave-{n}-{short_id}
  5. Merges each accepted hash onto the release branch with git merge --no-ff.
  6. Runs MERGE_TEST_COMMAND if configured.
  7. Opens a release PR from the release branch to main.
  8. Reports composed (ready_to_ship) or failed back to the backend.

Required env:
  TERARCHITECT_API_URL
  TERARCHITECT_WORKER_API_KEY

Optional env:
  SHIP_RUN_ID  — pre-claimed run (coordinator sets this)
  AGENTHUB_URL + AGENTHUB_API_KEY
  MERGE_TEST_COMMAND          — shell command to run tests (e.g. "pytest tests/ -x")
  GH_TOKEN / GITHUB_TOKEN     — for opening the release PR
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


def _slugify(text: str, max_len: int) -> str:
    s = re.sub(r"[^a-z0-9-]", "-", text.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] or "x"


def _wave_channel(project_name: str, wave_num: int) -> str:
    slug = _slugify(project_name, 21)
    return f"wave-{slug}-{wave_num}"


def _post_to_channel(channel: str, content: str) -> None:
    """Fire-and-forget post to AgentHub channel."""
    url = _ah_url()
    if not url:
        return
    try:
        requests.post(
            f"{url}/api/channels/{channel}/posts",
            json={"content": content},
            headers=_ah_headers(),
            timeout=5,
        )
    except Exception:
        pass


def _event_content(event_type: str, message: str, metadata: dict | None = None) -> str:
    return json.dumps(
        {
            "terarchitect_event": 1,
            "type": event_type,
            "message": message,
            "metadata": metadata or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _git(args: list, cwd: str, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    name = _env("GIT_USER_NAME", "Terarchitect Shipper")
    email = _env("GIT_USER_EMAIL", "shipper@terarchitect.local")
    env.update({
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email,
    })
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True,
        timeout=timeout, env=env, check=check,
    )


def _api_post(path: str, body: dict = None) -> Optional[dict]:
    try:
        resp = requests.post(
            _base_url() + path,
            json=body or {},
            headers=_auth_headers(),
            timeout=30,
        )
        if resp.ok:
            return resp.json()
        print(f"[shipper] POST {path} → {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"[shipper] POST {path} error: {e}", file=sys.stderr)
    return None


def _api_get(path: str) -> Optional[dict]:
    try:
        resp = requests.get(_base_url() + path, headers=_auth_headers(), timeout=15)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"[shipper] GET {path} error: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# AgentHub bundle fetch
# ---------------------------------------------------------------------------

def _fetch_bundle(commit_hash: str, dest_dir: str) -> Optional[str]:
    """Download a git bundle for commit_hash from AgentHub. Returns path or None."""
    url = _ah_url()
    if not url:
        return None
    try:
        resp = requests.get(
            f"{url}/api/git/fetch/{commit_hash}",
            headers=_ah_headers(),
            timeout=120,
            stream=True,
        )
        if not resp.ok:
            print(f"[shipper] Bundle fetch {commit_hash[:12]} → {resp.status_code}", file=sys.stderr)
            return None
        path = os.path.join(dest_dir, f"{commit_hash[:12]}.bundle")
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return path
    except Exception as e:
        print(f"[shipper] Bundle fetch {commit_hash[:12]} error: {e}", file=sys.stderr)
        return None


def _ensure_commit(commit_hash: str, project_path: str, tmp_dir: str) -> bool:
    """Ensure commit_hash is available in the local repo. Fetches from AgentHub if needed."""
    r = subprocess.run(["git", "cat-file", "-e", commit_hash], cwd=project_path, capture_output=True)
    if r.returncode == 0:
        return True
    bundle_path = _fetch_bundle(commit_hash, tmp_dir)
    if not bundle_path:
        return False
    r2 = subprocess.run(
        ["git", "bundle", "unbundle", bundle_path],
        cwd=project_path, capture_output=True, text=True, timeout=60,
    )
    return r2.returncode == 0


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

class ComposeError(Exception):
    pass


class TestFailureError(Exception):
    pass


def _compose_release_branch(
    commit_hashes: list[str],
    project_path: str,
    wave_num: int,
    run_short_id: str,
    tmp_dir: str,
) -> str:
    """Compose accepted leaves onto a release branch starting from current main.

    Returns the release branch name.
    Raises ComposeError on merge conflict.
    """
    branch = f"terarchitect/release/wave-{wave_num}-{run_short_id}"

    # Ensure all commits are available locally
    for h in commit_hashes:
        if not _ensure_commit(h, project_path, tmp_dir):
            print(f"[shipper] Warning: could not fetch {h[:12]} from AgentHub", file=sys.stderr)

    # Start release branch from current main
    _git(["fetch", "origin", "main"], cwd=project_path, check=False)
    _git(["fetch", "origin", "master"], cwd=project_path, check=False)

    # Determine default branch
    default_branch = "main"
    r = subprocess.run(["git", "rev-parse", "origin/main"], cwd=project_path, capture_output=True)
    if r.returncode != 0:
        default_branch = "master"

    _git(["checkout", "-B", branch, f"origin/{default_branch}"], cwd=project_path)
    print(f"[shipper] Release branch {branch!r} created from origin/{default_branch}")

    # Get base main hash (before merging)
    base_r = _git(["rev-parse", "HEAD"], cwd=project_path)
    base_main_hash = base_r.stdout.strip()

    # Merge each accepted commit
    for h in commit_hashes:
        r = _git(
            ["merge", "--no-ff", "--allow-unrelated-histories", "-m",
             f"wave-{wave_num}: merge {h[:12]}", h],
            cwd=project_path,
            check=False,
        )
        if r.returncode != 0:
            conflict_detail = (r.stdout + r.stderr)[:3000]
            subprocess.run(["git", "merge", "--abort"], cwd=project_path, capture_output=True)
            raise ComposeError(
                f"Merge conflict for {h[:12]} into release branch:\n{conflict_detail}"
            )
        print(f"[shipper] Merged {h[:12]}")

    return branch, base_main_hash


def _run_tests(project_path: str) -> tuple[str, str]:
    """Run MERGE_TEST_COMMAND. Returns (status, output). Status: passed | failed | skipped."""
    test_cmd = _env("MERGE_TEST_COMMAND")
    if not test_cmd:
        return "skipped", ""
    print(f"[shipper] Running tests: {test_cmd}")
    r = subprocess.run(
        test_cmd, shell=True, cwd=project_path,
        capture_output=True, text=True, timeout=600,
    )
    output = (r.stdout + r.stderr)[-4000:]
    if r.returncode != 0:
        print(f"[shipper] Tests failed (exit {r.returncode})", file=sys.stderr)
        return "failed", output
    print("[shipper] Tests passed.")
    return "passed", output


def _get_changed_files(project_path: str, default_branch: str) -> list[str]:
    """List files changed relative to origin/{default_branch}."""
    r = _git(
        ["diff", f"origin/{default_branch}...HEAD", "--name-only"],
        cwd=project_path, check=False,
    )
    if r.returncode != 0:
        return []
    return [f for f in r.stdout.splitlines() if f.strip()]


def _open_release_pr(
    project_path: str,
    slug: str,
    branch: str,
    wave_num: int,
    commit_hashes: list[str],
    changed_files: list[str],
    test_status: str,
    test_output: str,
) -> tuple[Optional[str], Optional[int]]:
    """Open or update a release PR. Returns (pr_url, pr_number)."""
    title = f"Release wave {wave_num}: {len(commit_hashes)} ticket(s)"
    files_section = "\n".join(f"- `{f}`" for f in changed_files[:30]) or "_(no files changed)_"
    if len(changed_files) > 30:
        files_section += f"\n... and {len(changed_files) - 30} more"
    test_section = f"**Tests:** {test_status}"
    if test_output:
        test_section += f"\n```\n{test_output[-1500:]}\n```"
    body = (
        f"## Wave {wave_num} — Release PR\n\n"
        f"Composed from {len(commit_hashes)} accepted AgentHub attempt(s).\n\n"
        f"### Changed files\n{files_section}\n\n"
        f"### Test results\n{test_section}\n\n"
        f"---\n*Created by Terarchitect shipper. Merge with the Ship button in the Ship Room.*"
    )
    if len(body) > 60000:
        body = body[:59997] + "..."

    gh_env = {**os.environ}
    try:
        r = subprocess.run(
            ["gh", "pr", "create",
             "--title", title,
             "--body", body,
             "--head", branch,
             "--base", "main",
             "-R", slug],
            capture_output=True, text=True, timeout=30, env=gh_env,
        )
        if r.returncode == 0 and r.stdout.strip():
            pr_url = r.stdout.strip()
            pr_number = None
            m = re.search(r"/pull/(\d+)", pr_url)
            if m:
                pr_number = int(m.group(1))
            print(f"[shipper] Release PR created: {pr_url}")
            return pr_url, pr_number
        # If PR already exists for this branch, fetch its number
        if "already exists" in (r.stderr or "").lower() or "already a pull request" in (r.stderr or "").lower():
            r2 = subprocess.run(
                ["gh", "pr", "view", branch, "--json", "url,number", "-R", slug],
                capture_output=True, text=True, timeout=15, env=gh_env,
            )
            if r2.returncode == 0:
                pr_data = json.loads(r2.stdout or "{}")
                return pr_data.get("url"), pr_data.get("number")
        print(f"[shipper] gh pr create failed: {r.stderr[:300]}", file=sys.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"[shipper] gh pr create error: {e}", file=sys.stderr)
    return None, None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_once() -> bool:
    """Claim and execute one ship run. Returns True if work was done."""
    run_id = _env("SHIP_RUN_ID")

    if run_id:
        data = _api_get(f"/api/worker/ship-run/{run_id}")
        if not data:
            print(f"[shipper] Could not fetch run {run_id}", file=sys.stderr)
            return False
    else:
        resp = requests.post(
            _base_url() + "/api/worker/ship-run/next",
            json={},
            headers=_auth_headers(),
            timeout=15,
        )
        if resp.status_code == 204:
            return False
        if not resp.ok:
            print(f"[shipper] Claim failed: {resp.status_code}", file=sys.stderr)
            return False
        data = resp.json()

    run = data["run"]
    run_id = run["id"]
    wave_num = run["wave_num"]
    project = data["project"]
    commit_hashes = data.get("commit_hashes") or []
    project_path = (project.get("project_path") or "").strip()
    slug = None
    github_url = (project.get("github_url") or "").strip()
    if github_url and "github.com" in github_url:
        from urllib.parse import urlparse
        p = urlparse(github_url.rstrip("/"))
        parts = p.path.strip("/").split("/")
        if len(parts) >= 2:
            slug = "/".join(parts[:2])

    wave_ch = _wave_channel(project['name'], wave_num)
    print(f"[shipper] Run {run_id} wave {wave_num} project {project['name']!r}")
    print(f"[shipper] Commit hashes: {commit_hashes}")

    if not commit_hashes:
        _api_post(f"/api/worker/ship-run/{run_id}/fail", {
            "error": "No accepted commit hashes found for wave tickets.",
            "compose_failed": True,
            "fix_ticket_title": f"[wave-{wave_num}] No commits to compose — check agent output",
            "fix_ticket_description":
                f"The shipper found no accepted AgentHub commits for wave {wave_num}. "
                "Ensure agents ran swarm_publish and their completions were recorded.",
        })
        return True

    if not project_path or not os.path.isdir(project_path):
        _api_post(f"/api/worker/ship-run/{run_id}/fail", {
            "error": f"project_path not found: {project_path!r}",
            "compose_failed": True,
        })
        return True

    if not slug:
        _api_post(f"/api/worker/ship-run/{run_id}/fail", {
            "error": "Project has no parseable GitHub URL — cannot open release PR.",
            "compose_failed": True,
        })
        return True

    run_short_id = run_id.replace("-", "")[:8]

    with tempfile.TemporaryDirectory() as tmp_dir:
        # --- Compose release branch ---
        _post_to_channel(
            wave_ch,
            _event_content(
                "release_composition_started",
                f"Release composition started for {len(commit_hashes)} attempt(s)",
                {"wave_num": wave_num, "ship_run_id": run_id, "attempt_count": len(commit_hashes)},
            ),
        )
        try:
            branch, base_main_hash = _compose_release_branch(
                commit_hashes, project_path, wave_num, run_short_id, tmp_dir
            )
        except ComposeError as e:
            print(f"[shipper] Compose conflict: {e}", file=sys.stderr)
            _post_to_channel(
                wave_ch,
                _event_content(
                    "release_composition_failed",
                    str(e)[:300],
                    {"wave_num": wave_num, "ship_run_id": run_id, "error": str(e)[:2000]},
                ),
            )
            _api_post(f"/api/worker/ship-run/{run_id}/fail", {
                "error": str(e),
                "compose_failed": True,
                "fix_ticket_title": f"[wave-{wave_num}] Resolve merge conflicts in release composition",
                "fix_ticket_description":
                    f"The shipper encountered conflicts composing wave {wave_num}.\n\nDetails:\n{str(e)[:2000]}",
            })
            return True

        # --- Run tests ---
        test_status, test_output = _run_tests(project_path)
        if test_status == "failed":
            _post_to_channel(
                wave_ch,
                _event_content(
                    "release_composition_failed",
                    f"Tests failed: {test_output[:300]}",
                    {"wave_num": wave_num, "ship_run_id": run_id, "test_status": test_status},
                ),
            )
            _api_post(f"/api/worker/ship-run/{run_id}/fail", {
                "error": f"Tests failed after composition:\n{test_output[:2000]}",
                "compose_failed": True,
                "fix_ticket_title": f"[wave-{wave_num}] Fix test failures after release composition",
                "fix_ticket_description":
                    f"Tests failed after composing wave {wave_num}.\n\nOutput:\n{test_output[:2000]}",
            })
            return True

        # --- Get composed commit hash and changed files ---
        head_r = _git(["rev-parse", "HEAD"], cwd=project_path, check=False)
        composed_commit_hash = head_r.stdout.strip() if head_r.returncode == 0 else None

        default_branch = "main"
        r_check = subprocess.run(["git", "rev-parse", "origin/main"], cwd=project_path, capture_output=True)
        if r_check.returncode != 0:
            default_branch = "master"
        changed_files = _get_changed_files(project_path, default_branch)

        # --- Push release branch ---
        push_r = _git(
            ["push", "-u", "origin", branch, "--force-with-lease"],
            cwd=project_path, check=False,
        )
        if push_r.returncode != 0:
            print(f"[shipper] Push failed: {push_r.stderr[:300]}", file=sys.stderr)
            _api_post(f"/api/worker/ship-run/{run_id}/fail", {
                "error": f"Failed to push release branch {branch!r}: {push_r.stderr[:1000]}",
                "compose_failed": True,
            })
            return True

        # --- Open release PR ---
        pr_url, pr_number = _open_release_pr(
            project_path, slug, branch, wave_num,
            commit_hashes, changed_files, test_status, test_output,
        )

        # --- Report composed ---
        _post_to_channel(
            wave_ch,
            _event_content(
                "release_pr_opened",
                f"PR #{pr_number} opened for {branch}; tests={test_status}; files={len(changed_files)}",
                {
                    "wave_num": wave_num,
                    "ship_run_id": run_id,
                    "release_pr_number": pr_number,
                    "release_pr_url": pr_url,
                    "release_branch": branch,
                    "test_status": test_status,
                    "changed_file_count": len(changed_files),
                    "composed_commit_hash": composed_commit_hash,
                },
            ),
        )
        _api_post(f"/api/worker/ship-run/{run_id}/composed", {
            "release_branch": branch,
            "release_pr_url": pr_url,
            "release_pr_number": pr_number,
            "composed_commit_hash": composed_commit_hash,
            "base_main_hash": base_main_hash,
            "test_status": test_status,
            "test_output": test_output[-4000:] if test_output else "",
            "changed_files": changed_files,
        })
        print(
            f"[shipper] Wave {wave_num} composed. branch={branch!r} "
            f"pr={pr_number} tests={test_status} files={len(changed_files)}"
        )
        return True


def main() -> None:
    if not _env("TERARCHITECT_WORKER_API_KEY"):
        print("[shipper] Warning: TERARCHITECT_WORKER_API_KEY not set", file=sys.stderr)
    run_once()
    sys.exit(0)
