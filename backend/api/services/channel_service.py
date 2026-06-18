"""AgentHub channel naming and event posting utilities.

Channel naming spec (from AGENTHUB-CONVERSION.md):
  ticket-{short_ticket_id}    — per-ticket execution ledger
  wave-{project_slug}-{n}     — per-wave release channel
  project-{short_project_id}  — project-level events

AgentHub enforces: ^[a-z0-9][a-z0-9_-]{0,30}$ (max 31 chars).
All names produced here are guaranteed to fit within that constraint.

post_event() is fire-and-forget: it never raises, returns None on any failure.
Configure via AGENTHUB_URL + AGENTHUB_API_KEY environment variables.
"""
import os
import re
import threading
import json
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Channel name construction
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int) -> str:
    """Lowercase, strip non-alnum/dash, collapse repeated dashes, truncate."""
    s = re.sub(r"[^a-z0-9-]", "-", text.lower())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len] or "x"


def ticket_channel(ticket_id: str) -> str:
    """ticket-{uuid_no_dashes[:24]}  — always exactly 31 chars."""
    short = str(ticket_id).replace("-", "")[:24]
    return f"ticket-{short}"


def wave_channel(project_name: str, wave_num: int) -> str:
    """wave-{slug}-{wave_num}  — max 31 chars.

    Budget: 'wave-' (5) + '-' (1) + wave_num digits (≤4) = 10 fixed.
    Slug gets up to 21 chars → total ≤ 31.
    """
    slug = _slugify(project_name, 21)
    return f"wave-{slug}-{wave_num}"


def project_channel(project_id: str) -> str:
    """project-{uuid_no_dashes[:23]}  — always exactly 31 chars."""
    short = str(project_id).replace("-", "")[:23]
    return f"project-{short}"


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def _agenthub_url() -> str:
    return (os.environ.get("AGENTHUB_URL") or "").rstrip("/")


def _agenthub_key() -> str:
    return (os.environ.get("AGENTHUB_API_KEY") or "").strip()


def _agenthub_auth_headers() -> dict[str, str]:
    key = _agenthub_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def event_content(event_type: str, message: str, metadata: dict[str, Any] | None = None) -> str:
    """Encode a structured Terarchitect event as channel post content."""
    payload = {
        "terarchitect_event": 1,
        "type": event_type,
        "message": message,
        "metadata": metadata or {},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def parse_event_post(post: dict[str, Any]) -> dict[str, Any]:
    """Normalize an AgentHub post into a timeline event.

    Structured posts are JSON produced by event_content(). Older text posts are
    preserved and given a best-effort event_type so existing ledgers stay readable.
    """
    content = post.get("content") or ""
    normalized = dict(post)
    normalized.setdefault("metadata", {})
    normalized["raw_content"] = content
    normalized["structured"] = False

    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        payload = None

    if isinstance(payload, dict) and payload.get("terarchitect_event") == 1:
        event_type = str(payload.get("type") or "event")
        message = str(payload.get("message") or event_type)
        metadata = payload.get("metadata")
        normalized["event_type"] = event_type
        normalized["message"] = message
        normalized["content"] = message
        normalized["metadata"] = metadata if isinstance(metadata, dict) else {}
        normalized["structured"] = True
        return normalized

    event_type = "event"
    if content.startswith("[feedback]"):
        event_type = "human_feedback"
    elif ":" in content:
        head = content.split(":", 1)[0].strip().lower().replace(" ", "_")
        if re.match(r"^[a-z][a-z0-9_]{1,63}$", head):
            event_type = head
    elif content.strip().startswith("done"):
        event_type = "attempt_published"

    normalized["event_type"] = event_type
    normalized["message"] = content
    return normalized


def post_event(channel: str, content: str, background: bool = True) -> None:
    """Post content to an AgentHub channel. Fire-and-forget; never raises.

    If background=True (default), the HTTP call is made in a daemon thread
    so it does not block the request handler.
    """
    url = _agenthub_url()
    if not url or not content:
        return

    def _do_post():
        try:
            requests.post(
                f"{url}/api/channels/{channel}/posts",
                json={"content": content},
                headers=_agenthub_auth_headers() or None,
                timeout=5,
            )
        except Exception:
            pass  # intentionally silent — events are best-effort

    if background:
        t = threading.Thread(target=_do_post, daemon=True)
        t.start()
    else:
        _do_post()


def post_structured_event(
    channel: str,
    event_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    background: bool = True,
) -> None:
    """Post a machine-readable Terarchitect event to an AgentHub channel."""
    post_event(channel, event_content(event_type, message, metadata), background=background)


def fetch_channel_posts(channel: str, limit: int = 50) -> list[dict]:
    """Fetch recent posts from an AgentHub channel. Returns [] on any error."""
    url = _agenthub_url()
    if not url:
        return []
    try:
        resp = requests.get(
            f"{url}/api/channels/{channel}/posts",
            params={"limit": limit},
            headers=_agenthub_auth_headers() or None,
            timeout=8,
        )
        if resp.ok:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []
