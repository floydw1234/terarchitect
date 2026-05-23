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
                headers={"Authorization": f"Bearer {_agenthub_key()}"},
                timeout=5,
            )
        except Exception:
            pass  # intentionally silent — events are best-effort

    if background:
        t = threading.Thread(target=_do_post, daemon=True)
        t.start()
    else:
        _do_post()


def fetch_channel_posts(channel: str, limit: int = 50) -> list[dict]:
    """Fetch recent posts from an AgentHub channel. Returns [] on any error."""
    url = _agenthub_url()
    if not url:
        return []
    try:
        resp = requests.get(
            f"{url}/api/channels/{channel}/posts",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {_agenthub_key()}"},
            timeout=8,
        )
        if resp.ok:
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []
