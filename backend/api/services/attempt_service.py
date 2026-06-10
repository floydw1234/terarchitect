"""TicketAttempt domain service: serialization, queries, state transitions, and creation."""
import os
from datetime import datetime, timezone
from typing import Optional

import requests as _requests
from flask import current_app

from models.db import db, TicketAttempt

# ---------------------------------------------------------------------------
# Valid status transitions
# ---------------------------------------------------------------------------
#
# The MVP docs speak in terms of proposed/accepted/rejected/failed/shipped,
# but the live code still supports a few compatibility states for older
# release-flow callbacks and tests.

_TRANSITIONS: dict[str, set[str]] = {
    "proposed":        {"validating", "accepted", "rejected", "failed"},
    "validating":      {"accepted", "failed"},
    "accepted":        {"composed", "superseded"},
    "composed":        {"release_pr_open"},
    "release_pr_open": {"shipped"},
    # Terminal states — no outbound transitions
    "shipped":         set(),
    "rejected":        set(),
    "superseded":      set(),
    "failed":          set(),
}

ALL_STATUSES: frozenset[str] = frozenset(_TRANSITIONS.keys())

# Attempt statuses that count as "satisfied" for dependency and wave completion checks.
# Exported so all services use the same definition.
SATISFIED_STATUSES: frozenset[str] = frozenset(["accepted", "composed", "release_pr_open", "shipped"])


def transition_attempt(attempt: TicketAttempt, new_status: str, reason: str = "") -> TicketAttempt:
    """Apply a status transition. Raises ValueError on invalid transition."""
    allowed = _TRANSITIONS.get(attempt.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition attempt {attempt.id} from '{attempt.status}' to '{new_status}'. "
            f"Allowed: {sorted(allowed) or 'none (terminal state)'}"
        )
    attempt.status = new_status
    attempt.updated_at = datetime.now(timezone.utc)
    if reason:
        current_app.logger.info(
            "Attempt %s transitioned to %s: %s", attempt.id, new_status, reason
        )
    # Caller is responsible for db.session.commit()
    return attempt


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def validate_attempt(attempt: TicketAttempt, agenthub_url: str = "") -> TicketAttempt:
    """Validate a proposed attempt and transition it to accepted or failed.

    Checks (plan 8.1):
      1. commit_hash is present
      2. base_hash is known (warning if missing, not blocking — may be first ticket)
      3. commit exists in AgentHub (if AGENTHUB_URL is configured)
      4. summary exists (warning only)

    If AgentHub is not configured, skips remote check and accepts immediately.
    This is the AgentHub-native MVP validation path; compatibility states are
    still allowed elsewhere in the model for older workflows.
    Caller must db.session.commit() after.
    """
    current_app.logger.info(
        "validation_start attempt=%s ticket=%s commit=%s base=%s",
        attempt.id, attempt.ticket_id,
        (attempt.agenthub_commit_hash or "")[:12] or "none",
        (attempt.base_hash or "")[:12] or "none",
    )

    url = (agenthub_url or os.environ.get("AGENTHUB_URL") or "").rstrip("/")
    key = os.environ.get("AGENTHUB_API_KEY") or ""

    if not attempt.agenthub_commit_hash:
        attempt.validation_error = "No commit hash — agent did not publish to AgentHub."
        attempt.status = "failed"
        attempt.updated_at = datetime.now(timezone.utc)
        current_app.logger.warning(
            "validation_failed attempt=%s reason=no_commit_hash", attempt.id
        )
        return attempt

    if not attempt.base_hash:
        current_app.logger.warning(
            "validation_warning attempt=%s reason=no_base_hash — "
            "staleness tracking will not work for this attempt",
            attempt.id,
        )

    if url:
        try:
            resp = _requests.get(
                f"{url}/api/git/commits/{attempt.agenthub_commit_hash}",
                headers={"Authorization": f"Bearer {key}"},
                timeout=8,
            )
            if resp.status_code == 404:
                attempt.validation_error = (
                    f"Commit {attempt.agenthub_commit_hash[:12]} not found in AgentHub."
                )
                attempt.status = "failed"
                attempt.updated_at = datetime.now(timezone.utc)
                current_app.logger.warning(
                    "validation_failed attempt=%s reason=commit_not_found commit=%s",
                    attempt.id, attempt.agenthub_commit_hash[:12],
                )
                return attempt
            if not resp.ok:
                # AgentHub returned an unexpected error — accept anyway to not block work
                current_app.logger.warning(
                    "AgentHub validation check returned %s for %s — accepting anyway",
                    resp.status_code, attempt.agenthub_commit_hash[:12],
                )
        except Exception as exc:
            current_app.logger.warning(
                "AgentHub validation unreachable (%s) — accepting attempt %s",
                exc, attempt.id,
            )

    if not attempt.summary:
        current_app.logger.info(
            "validation_warning attempt=%s reason=no_summary", attempt.id
        )

    attempt.status = "accepted"
    attempt.updated_at = datetime.now(timezone.utc)
    current_app.logger.info(
        "validation_passed attempt=%s ticket=%s commit=%s",
        attempt.id, attempt.ticket_id, (attempt.agenthub_commit_hash or "")[:12],
    )
    return attempt


def attempt_to_json(
    attempt: TicketAttempt,
    *,
    include_test_output: bool = False,
    shipped_frontier: Optional[str] = None,
) -> dict:
    """Serialize a TicketAttempt for API responses.

    Pass shipped_frontier to include a staleness flag: True when the attempt's
    base_hash predates the current frontier (i.e. main has advanced since this
    attempt was created). This is used by the MVP path even though legacy
    states remain valid in storage.
    """
    commit = attempt.agenthub_commit_hash or ""
    stale: Optional[bool] = None
    if shipped_frontier and attempt.base_hash:
        stale = attempt.base_hash != shipped_frontier
    return {
        "id": str(attempt.id),
        "project_id": str(attempt.project_id),
        "ticket_id": str(attempt.ticket_id),
        "agenthub_commit_hash": commit,
        "short_commit_hash": commit[:12] if commit else None,
        "base_hash": attempt.base_hash,
        "wave_num": attempt.wave_num,
        "attempt_num": attempt.attempt_num,
        "agent_id": attempt.agent_id,
        "status": attempt.status,
        "summary": attempt.summary,
        "validation_error": attempt.validation_error,
        "test_status": attempt.test_status,
        "stale": stale,
        **({"test_output": attempt.test_output} if include_test_output else {}),
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_latest_attempt(ticket_id) -> Optional[TicketAttempt]:
    """Return the most recently created attempt for a ticket regardless of status."""
    return (
        TicketAttempt.query
        .filter_by(ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .first()
    )


def get_accepted_attempt(ticket_id) -> Optional[TicketAttempt]:
    """Return the accepted (or shipped) attempt for a ticket, if any.

    Compatibility states like composed/release_pr_open are still treated as
    satisfied because older release-flow code can emit them.
    """
    return (
        TicketAttempt.query
        .filter_by(ticket_id=ticket_id)
        .filter(TicketAttempt.status.in_(SATISFIED_STATUSES))
        .order_by(TicketAttempt.attempt_num.desc())
        .first()
    )


def list_wave_attempts(project_id, wave_num: int) -> list[TicketAttempt]:
    """Return all attempts for a given wave, newest attempt per ticket first."""
    return (
        TicketAttempt.query
        .filter_by(project_id=project_id, wave_num=wave_num)
        .order_by(TicketAttempt.ticket_id, TicketAttempt.attempt_num.desc())
        .all()
    )


def list_ready_attempts(project_id) -> list[TicketAttempt]:
    """Return accepted attempts that have not yet been composed into a release."""
    return (
        TicketAttempt.query
        .filter_by(project_id=project_id, status="accepted")
        .order_by(TicketAttempt.created_at.asc(), TicketAttempt.attempt_num.asc())
        .all()
    )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def create_attempt(
    *,
    project_id,
    ticket_id,
    commit_hash: Optional[str] = None,
    base_hash: Optional[str] = None,
    wave_num: Optional[int] = None,
    agent_id: Optional[str] = None,
    summary: Optional[str] = None,
    initial_status: str = "proposed",
) -> TicketAttempt:
    """Create and persist a new TicketAttempt, auto-incrementing attempt_num.

    `wave_num` is legacy-only compatibility metadata. Callers no longer need to
    supply it for acceptance, inspection, or later promotion selection paths.
    """
    if initial_status not in ALL_STATUSES:
        raise ValueError(f"Unknown initial status: {initial_status!r}")

    last = (
        TicketAttempt.query
        .filter_by(ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .first()
    )
    attempt_num = (last.attempt_num + 1) if last else 1
    compatibility_wave_num = 0 if wave_num is None else wave_num

    attempt = TicketAttempt(
        project_id=project_id,
        ticket_id=ticket_id,
        agenthub_commit_hash=commit_hash,
        base_hash=base_hash,
        wave_num=compatibility_wave_num,
        attempt_num=attempt_num,
        agent_id=agent_id,
        status=initial_status,
        summary=summary,
    )
    db.session.add(attempt)
    # Caller is responsible for db.session.commit()
    return attempt
