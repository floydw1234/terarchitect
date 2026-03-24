"""
Git backend for terarchitect agent.

Reads TERARCHITECT_MODE from env:
  "structured" (default) — GitHub branches + PRs (existing behaviour)
  "swarm"                — agenthub DAG + message board (no PRs)

In swarm mode all agents share a single named branch (AGENTHUB_BRANCH, default "swarm").
Each agent resets that branch to the latest agenthub leaf before working, then commits
and pushes their changes back as a new DAG node — no direct commits to main/master.

Public API used by agent.py:
  is_swarm()                           → bool
  get_peer_context(ticket_id) → str    → injected into Director prompt before work starts
  prepare_work(project_path)           → fetches latest agenthub leaf, checks out swarm branch
  swarm_publish(project_path, commit_message, ticket_id, summary) → commit_hash | None
"""

import os
import subprocess
import tempfile
from typing import Optional

import requests


def is_swarm() -> bool:
    return (os.environ.get("TERARCHITECT_MODE") or "structured").strip().lower() == "swarm"


def _swarm_branch() -> str:
    """Name of the shared git branch all swarm agents work on (default: 'swarm')."""
    return (os.environ.get("AGENTHUB_BRANCH") or "swarm").strip() or "swarm"


def _ah_url() -> str:
    return (os.environ.get("AGENTHUB_URL") or "").rstrip("/")


def _ah_key() -> str:
    return os.environ.get("AGENTHUB_API_KEY") or ""


def _ah_headers() -> dict:
    return {"Authorization": f"Bearer {_ah_key()}"}


def _ah_get(path: str) -> Optional[any]:
    url = _ah_url()
    if not url:
        return None
    try:
        resp = requests.get(url + path, headers=_ah_headers(), timeout=15)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return None


def _ah_post(path: str, body: dict) -> Optional[dict]:
    url = _ah_url()
    if not url:
        return None
    try:
        resp = requests.post(url + path, json=body, headers=_ah_headers(), timeout=15)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Peer context — injected into Director prompt before work starts (swarm only)
# ---------------------------------------------------------------------------

def get_peer_context(ticket_id: str) -> str:
    """Return formatted agenthub context string. Empty string in structured mode or if unreachable."""
    if not is_swarm() or not _ah_url():
        return ""

    lines = ["## AgentHub — peer context\n"]

    leaves = _ah_get("/api/git/leaves") or []
    if leaves:
        lines.append("### Current frontier (leaves)")
        for c in leaves[:6]:
            h = (c.get("hash") or "")[:10]
            agent = c.get("agent_id") or "(seed)"
            msg = (c.get("message") or "")[:120]
            lines.append(f"  {h}  [{agent}]  {msg}")
        lines.append("")

    channel = f"ticket-{ticket_id}"
    posts = _ah_get(f"/api/channels/{channel}/posts?limit=10") or []
    if isinstance(posts, list) and posts:
        lines.append(f"### Recent board posts in #{channel}")
        for p in reversed(posts[:5]):
            agent = p.get("agent_id") or "?"
            content = (p.get("content") or "")[:200]
            lines.append(f"  [{agent}]: {content}")
        lines.append("")

    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# prepare_work — called before the worker starts (swarm only)
# ---------------------------------------------------------------------------

def prepare_work(project_path: str) -> None:
    """Fetch the latest agenthub leaf into the local repo and check it out.
    Non-fatal: if agenthub is unreachable the agent works from the cloned origin."""
    if not is_swarm():
        return

    leaves = _ah_get("/api/git/leaves") or []
    if not leaves:
        return

    latest_hash = (leaves[0].get("hash") or "").strip()
    if not latest_hash:
        return

    url = _ah_url()
    if not url:
        return

    try:
        resp = requests.get(
            f"{url}/api/git/fetch/{latest_hash}",
            headers=_ah_headers(),
            timeout=60,
            stream=True,
        )
        if not resp.ok:
            return

        with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
            bundle_path = f.name

        try:
            r = subprocess.run(
                ["git", "bundle", "unbundle", bundle_path],
                cwd=project_path, capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                # Reset (or create) the shared swarm branch at the leaf commit.
                # Using -B so it's idempotent: creates the branch if absent, moves it
                # to the leaf if the branch already exists from a previous agent run.
                branch = _swarm_branch()
                subprocess.run(
                    ["git", "checkout", "-B", branch, latest_hash],
                    cwd=project_path, capture_output=True, timeout=10,
                )
        finally:
            try:
                os.unlink(bundle_path)
            except OSError:
                pass
    except Exception:
        pass  # Non-fatal; agent continues from origin clone


# ---------------------------------------------------------------------------
# swarm_publish — called from _finalize instead of git push + gh pr create
# ---------------------------------------------------------------------------

def swarm_publish(
    project_path: str,
    commit_message: str,
    ticket_id: str,
    summary: str,
) -> Optional[str]:
    """Stage, commit, push to agenthub DAG, and post to ticket channel.
    Returns the commit hash on success, None on failure."""
    env = os.environ.copy()

    # Stage all changes
    subprocess.run(["git", "add", "-A"], cwd=project_path, capture_output=True, timeout=10, env=env)

    # Only commit if there are changes
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=project_path, capture_output=True, text=True, timeout=5, env=env,
    )
    if (status.stdout or "").strip():
        commit_r = subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=project_path, capture_output=True, text=True, timeout=15, env=env,
        )
        if commit_r.returncode != 0:
            return None

    # Get HEAD hash
    head_r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project_path, capture_output=True, text=True, timeout=5, env=env,
    )
    commit_hash = (head_r.stdout or "").strip()
    if not commit_hash:
        return None

    # Push to agenthub via ah CLI (reads AGENTHUB_URL + AGENTHUB_API_KEY from env)
    push_r = subprocess.run(
        ["ah", "push"],
        cwd=project_path, capture_output=True, text=True, timeout=120, env=env,
    )

    # Post completion notice to ticket channel (auto-created if it doesn't exist)
    channel = f"ticket-{ticket_id}"
    body = f"done: {summary[:400]}\ncommit: {commit_hash[:12]}" if summary else f"done\ncommit: {commit_hash[:12]}"
    _ah_post(f"/api/channels/{channel}/posts", {"content": body})

    return commit_hash if push_r.returncode == 0 else None
