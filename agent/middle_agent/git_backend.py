"""
Git backend for terarchitect agent (swarm mode only).

Public API used by agent.py:
  is_swarm()                           → bool
  get_peer_context(ticket_id) → str    → injected into Director prompt before work starts
  prepare_work(project_path)           → fetches BASE_HASH bundle from AgentHub and checks it out
  swarm_publish(project_path, commit_message, ticket_id, summary) → commit_hash | None

Env vars consumed:
  TERARCHITECT_MODE      — always "swarm"
  BASE_HASH              — AgentHub commit hash to build on (set by coordinator)
  AGENTHUB_ROOT_HASH     — last shipped frontier (informational; used for logging)
  TICKET_ID              — used to name the local working branch
  PROJECT_ID             — used for logging
  AGENTHUB_URL           — AgentHub server URL
  AGENTHUB_API_KEY       — AgentHub auth key
"""

import os
import subprocess
import tempfile
import json
from typing import Any, Optional

import requests


def _ticket_channel(ticket_id: str) -> str:
    """Derive a valid agenthub channel name from a ticket ID.
    Agenthub enforces ≤ 31 chars, lowercase alphanumeric/dash/underscore.
    'ticket-' (7) + 24 hex chars (UUID without dashes, truncated) = 31.
    """
    short = str(ticket_id).replace("-", "")[:24]
    return f"ticket-{short}"


def is_swarm() -> bool:
    return (os.environ.get("TERARCHITECT_MODE") or "swarm").strip().lower() == "swarm"


def _ah_url() -> str:
    return (os.environ.get("AGENTHUB_URL") or "").rstrip("/")


def _ah_key() -> str:
    return os.environ.get("AGENTHUB_API_KEY") or ""


def _ah_headers() -> dict:
    return {"Authorization": f"Bearer {_ah_key()}"}


def _ah_get(path: str) -> Optional[Any]:
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


def post_ticket_event(ticket_id: str, event_type: str, message: str, metadata: dict | None = None) -> None:
    channel = _ticket_channel(ticket_id)
    body = _event_content(event_type, message, metadata)
    _ah_post(f"/api/channels/{channel}/posts", {"content": body})


def _format_subprocess_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if result.stdout:
        parts.append(result.stdout.strip())
    if result.stderr:
        parts.append(result.stderr.strip())
    return "\n".join(part for part in parts if part).strip()


def _is_missing_prerequisite_push_failure(result: subprocess.CompletedProcess[str]) -> bool:
    if result.returncode == 0:
        return False
    output = _format_subprocess_output(result).lower()
    return "repository lacks these prerequisite commits" in output


def _run_ah_push(project_path: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ah", "push"],
        cwd=project_path,
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )


def _retry_ah_push_with_full_bundle(project_path: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print(
        "[git_backend] AgentHub rejected incremental bundle due to missing prerequisite commits; "
        "retrying with a full bundle from an isolated clone",
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="agenthub-full-push-") as tmp_dir:
        clone_path = os.path.join(tmp_dir, "repo")
        clone_r = subprocess.run(
            ["git", "clone", project_path, clone_path],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        if clone_r.returncode != 0:
            print(
                f"[git_backend] Full-bundle retry clone failed: {_format_subprocess_output(clone_r)[:300]}",
                flush=True,
            )
            return clone_r

        remote_r = subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=clone_path,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        if remote_r.returncode != 0:
            print(
                f"[git_backend] Full-bundle retry could not remove origin from temp clone: "
                f"{_format_subprocess_output(remote_r)[:300]}",
                flush=True,
            )
            return remote_r

        retry_r = _run_ah_push(clone_path, env)
        if retry_r.returncode == 0:
            print("[git_backend] Full-bundle retry succeeded", flush=True)
        else:
            print(
                f"[git_backend] Full-bundle retry failed: {_format_subprocess_output(retry_r)[:300]}",
                flush=True,
            )
        return retry_r


# ---------------------------------------------------------------------------
# Peer context — injected into Director prompt before work starts (swarm only)
# ---------------------------------------------------------------------------

def get_peer_context(ticket_id: str) -> str:
    """Return formatted agenthub context string. Empty string if AgentHub unreachable."""
    if not _ah_url():
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

    channel = _ticket_channel(ticket_id)
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
    """Set up the working base before the agent starts.

    Reads BASE_HASH from env (set by coordinator from compute_base_hash).
    Fetches that commit bundle from AgentHub and checks it out as the
    local working branch. No origin/swarm branch needed.

    If BASE_HASH is not set, the agent works from the clone's default branch (main).
    Non-fatal throughout: on any failure the agent continues from the clone base.
    """
    if not is_swarm():
        return

    base_hash = (os.environ.get("BASE_HASH") or "").strip()
    ticket_id = (os.environ.get("TICKET_ID") or "").strip()
    project_id = (os.environ.get("PROJECT_ID") or "").strip()
    root_hash = (os.environ.get("AGENTHUB_ROOT_HASH") or "").strip()

    print(
        f"[git_backend] prepare_work project={project_id or '?'} ticket={ticket_id or '?'} "
        f"base={base_hash[:12] if base_hash else 'none'} "
        f"root={root_hash[:12] if root_hash else 'none'}",
        flush=True,
    )

    if not base_hash:
        print("[git_backend] No BASE_HASH set — working from clone base (main)", flush=True)
        return

    url = _ah_url()
    if not url:
        print("[git_backend] No AGENTHUB_URL set — cannot fetch base bundle", flush=True)
        return

    try:
        resp = requests.get(
            f"{url}/api/git/fetch/{base_hash}",
            headers=_ah_headers(),
            timeout=60,
            stream=True,
        )
        if not resp.ok:
            print(f"[git_backend] AgentHub fetch {base_hash[:12]} → {resp.status_code}", flush=True)
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
            if r.returncode != 0:
                print(f"[git_backend] git bundle unbundle failed: {r.stderr[:300]}", flush=True)
                return

            branch = f"ticket-{ticket_id}" if ticket_id else "swarm-work"
            checkout_r = subprocess.run(
                ["git", "checkout", "-B", branch, base_hash],
                cwd=project_path, capture_output=True, text=True, timeout=10,
            )
            if checkout_r.returncode == 0:
                print(f"[git_backend] Checked out base {base_hash[:12]} as branch {branch}", flush=True)
            else:
                print(f"[git_backend] Checkout failed: {checkout_r.stderr[:200]}", flush=True)
        finally:
            try:
                os.unlink(bundle_path)
            except OSError:
                pass
    except Exception as exc:
        print(f"[git_backend] prepare_work error (non-fatal): {exc}", flush=True)


# ---------------------------------------------------------------------------
# swarm_publish - publishes worker output as an AgentHub attempt.
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
    push_r = _run_ah_push(project_path, env)
    if _is_missing_prerequisite_push_failure(push_r):
        push_r = _retry_ah_push_with_full_bundle(project_path, env)
    elif push_r.returncode != 0:
        print(f"[git_backend] ah push failed: {_format_subprocess_output(push_r)[:300]}", flush=True)

    # Post completion notice to ticket channel (auto-created if it doesn't exist)
    post_ticket_event(ticket_id, "attempt_published", f"done: {summary[:400]}" if summary else "done", {"ticket_id": ticket_id, "commit_hash": commit_hash, "commit_short": commit_hash[:12]})

    return commit_hash if push_r.returncode == 0 else None
