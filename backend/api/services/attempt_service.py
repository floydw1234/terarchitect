"""TicketAttempt domain service: serialization, queries, state transitions, and creation."""
import os
from datetime import datetime, timezone
from typing import Optional

import requests as _requests
from flask import current_app

from models.db import db, TicketAttempt
from .project_service import (
    compare_base_to_accepted_frontier as _compare_base_to_accepted_frontier,
    get_project_frontier_id as _get_project_frontier_id,
)

# ---------------------------------------------------------------------------
# Valid status transitions
# ---------------------------------------------------------------------------
#
# The MVP docs speak in terms of proposed/accepted/rejected/failed/shipped,
# but the live code still supports a few compatibility states for older
# release-flow callbacks and tests.

_TRANSITIONS: dict[str, set[str]] = {
    "proposed":        {"validating", "validated", "rejected", "failed"},
    "validating":      {"validated", "failed"},
    "validated":       {"accepted", "rejected", "failed"},
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

LEGACY_WINNER_FALLBACK_STATUSES: frozenset[str] = frozenset(["accepted", "composed", "release_pr_open", "shipped"])
VALIDATED_STATUSES: frozenset[str] = frozenset(["validated", "accepted", "composed", "release_pr_open", "shipped"])
INTEGRATED_STATUSES: frozenset[str] = frozenset(["accepted", "composed", "release_pr_open", "shipped"])
# Exported compatibility set for older callers that still key off status strings.
SATISFIED_STATUSES: frozenset[str] = INTEGRATED_STATUSES


def _agenthub_auth_headers(api_key: str | None) -> dict[str, str]:
    key = (api_key or "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _iso_or_none(value) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else None


def attempt_is_validated(attempt: TicketAttempt | None) -> bool:
    if attempt is None:
        return False
    return bool(getattr(attempt, "validated_at", None)) or attempt.status in VALIDATED_STATUSES


def attempt_is_winner(attempt: TicketAttempt | None) -> bool:
    if attempt is None:
        return False
    winner_flag = getattr(attempt, "is_winner", None)
    if winner_flag is True:
        return True
    if winner_flag is False:
        return False
    return attempt.status in LEGACY_WINNER_FALLBACK_STATUSES


def attempt_is_integrated(attempt: TicketAttempt | None) -> bool:
    if attempt is None:
        return False
    if getattr(attempt, "integrated_at", None) or getattr(attempt, "integrated_frontier_id", None):
        return True
    return attempt.status in INTEGRATED_STATUSES


def attempt_satisfies_dependencies(attempt: TicketAttempt | None) -> bool:
    return attempt_is_winner(attempt) and attempt_is_integrated(attempt)


def attempt_is_integrated_winner(attempt: TicketAttempt | None) -> bool:
    return attempt_satisfies_dependencies(attempt)


def transition_attempt(attempt: TicketAttempt, new_status: str, reason: str = "") -> TicketAttempt:
    """Apply a status transition. Raises ValueError on invalid transition."""
    allowed = _TRANSITIONS.get(attempt.status, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition attempt {attempt.id} from '{attempt.status}' to '{new_status}'. "
            f"Allowed: {sorted(allowed) or 'none (terminal state)'}"
        )
    attempt.status = new_status
    now = datetime.now(timezone.utc)
    attempt.updated_at = datetime.now(timezone.utc)
    if new_status in VALIDATED_STATUSES and not getattr(attempt, "validated_at", None):
        attempt.validated_at = now
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
    """Validate a proposed attempt and transition it to validated or failed.

    Checks (plan 8.1):
      1. commit_hash is present
      2. base_hash is known (warning if missing, not blocking — may be first ticket)
      3. commit exists in AgentHub (if AGENTHUB_URL is configured)
      4. summary exists (warning only)

    If AgentHub is not configured, skips remote check and validates immediately.
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
                headers=_agenthub_auth_headers(key) or None,
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
                # AgentHub returned an unexpected error — validate anyway to not block work
                current_app.logger.warning(
                    "AgentHub validation check returned %s for %s — validating anyway",
                    resp.status_code, attempt.agenthub_commit_hash[:12],
                )
        except Exception as exc:
            current_app.logger.warning(
                "AgentHub validation unreachable (%s) — validating attempt %s",
                exc, attempt.id,
            )

    if not attempt.summary:
        current_app.logger.info(
            "validation_warning attempt=%s reason=no_summary", attempt.id
        )

    attempt.status = "validated"
    attempt.validated_at = datetime.now(timezone.utc)
    attempt.updated_at = datetime.now(timezone.utc)
    current_app.logger.info(
        "validation_passed attempt=%s ticket=%s commit=%s",
        attempt.id, attempt.ticket_id, (attempt.agenthub_commit_hash or "")[:12],
    )
    return attempt


def attempt_stale_status(attempt: TicketAttempt, project=None) -> tuple[Optional[bool], Optional[str]]:
    accepted_frontier_id = _get_project_frontier_id(project) if project else None
    return _compare_base_to_accepted_frontier(
        getattr(attempt, "base_hash", None),
        accepted_frontier_id,
        subject_name="attempt",
        base_field_name="attempt.base_hash",
    )


def attempt_to_json(
    attempt: TicketAttempt,
    *,
    include_test_output: bool = False,
    accepted_frontier_id: Optional[str] = None,
    shipped_frontier: Optional[str] = None,
) -> dict:
    """Serialize a TicketAttempt for API responses.

    Pass accepted_frontier_id to compare against the canonical DAG frontier.
    `shipped_frontier` remains as a compatibility alias for older callers.
    """
    commit = attempt.agenthub_commit_hash or ""
    frontier_id = accepted_frontier_id or shipped_frontier
    stale, stale_reason = _compare_base_to_accepted_frontier(
        getattr(attempt, "base_hash", None),
        frontier_id,
        subject_name="attempt",
        base_field_name="attempt.base_hash",
    )
    return {
        "id": str(attempt.id),
        "project_id": str(attempt.project_id),
        "ticket_id": str(attempt.ticket_id),
        "agenthub_commit_hash": commit,
        "short_commit_hash": commit[:12] if commit else None,
        "base_hash": attempt.base_hash,
        "base_leaf_id": attempt.base_hash,
        "parent_leaf_id": attempt.base_hash,
        "attempt_num": attempt.attempt_num,
        "agent_id": attempt.agent_id,
        "worker_job_id": attempt.agent_id,
        "status": attempt.status,
        "validated": attempt_is_validated(attempt),
        "validated_at": _iso_or_none(getattr(attempt, "validated_at", None)),
        "is_winner": attempt_is_winner(attempt),
        "winner_chosen_at": _iso_or_none(getattr(attempt, "winner_chosen_at", None)),
        "accepted": attempt_is_integrated(attempt),
        "integrated": attempt_is_integrated(attempt),
        "integrated_at": _iso_or_none(getattr(attempt, "integrated_at", None)),
        "integrated_frontier_id": getattr(attempt, "integrated_frontier_id", None),
        "dependency_satisfied": attempt_satisfies_dependencies(attempt),
        "summary": attempt.summary,
        "validation_error": attempt.validation_error,
        "test_status": attempt.test_status,
        "accepted_frontier_id": frontier_id,
        "stale": stale,
        "stale_reason": stale_reason,
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


def get_integrated_winner_attempt(ticket_id) -> Optional[TicketAttempt]:
    """Return the integrated winner attempt for a ticket, if any.

    Compatibility states like composed/release_pr_open/shipped still count as
    integrated winners for older fixtures and release-flow callbacks.
    """
    attempts = (
        TicketAttempt.query
        .filter_by(ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .all()
    )
    for attempt in attempts:
        if attempt_is_integrated_winner(attempt):
            return attempt
    return None


def get_accepted_attempt(ticket_id) -> Optional[TicketAttempt]:
    """Compatibility wrapper for callers using the older accepted naming."""
    return get_integrated_winner_attempt(ticket_id)


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
    agent_id: Optional[str] = None,
    summary: Optional[str] = None,
    initial_status: str = "proposed",
) -> TicketAttempt:
    """Create and persist a new TicketAttempt, auto-incrementing attempt_num."""
    if initial_status not in ALL_STATUSES:
        raise ValueError(f"Unknown initial status: {initial_status!r}")

    last = (
        TicketAttempt.query
        .filter_by(ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .first()
    )
    attempt_num = (last.attempt_num + 1) if last else 1

    attempt = TicketAttempt(
        project_id=project_id,
        ticket_id=ticket_id,
        agenthub_commit_hash=commit_hash,
        base_hash=base_hash,
        attempt_num=attempt_num,
        agent_id=agent_id,
        status=initial_status,
        summary=summary,
    )
    db.session.add(attempt)
    # Caller is responsible for db.session.commit()
    return attempt
