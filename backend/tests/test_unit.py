"""
Unit tests (plan 12.1).

Tests that require no HTTP or DB — pure service/model logic.

Covers:
  - wave computation
  - base selection (compute_base_hash)
  - TicketAttempt state transitions
  - compute_ticket_display_state
  - ship_run_to_json field presence
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ---------------------------------------------------------------------------
# 12.1a  Wave computation
# ---------------------------------------------------------------------------

def _make_ticket(tid, deps=None):
    t = MagicMock()
    t.id = tid
    t.depends_on_ticket_ids = deps or []
    return t


def test_compute_waves_no_deps():
    from api.services.merge_service import compute_waves
    tickets = [_make_ticket("a"), _make_ticket("b"), _make_ticket("c")]
    waves = compute_waves(tickets)
    assert waves["a"] == 0
    assert waves["b"] == 0
    assert waves["c"] == 0


def test_compute_waves_linear_chain():
    from api.services.merge_service import compute_waves
    tickets = [
        _make_ticket("a"),
        _make_ticket("b", deps=["a"]),
        _make_ticket("c", deps=["b"]),
    ]
    waves = compute_waves(tickets)
    assert waves["a"] == 0
    assert waves["b"] == 1
    assert waves["c"] == 2


def test_compute_waves_diamond():
    from api.services.merge_service import compute_waves
    # a → b, a → c, b+c → d
    tickets = [
        _make_ticket("a"),
        _make_ticket("b", deps=["a"]),
        _make_ticket("c", deps=["a"]),
        _make_ticket("d", deps=["b", "c"]),
    ]
    waves = compute_waves(tickets)
    assert waves["a"] == 0
    assert waves["b"] == 1
    assert waves["c"] == 1
    assert waves["d"] == 2


def test_compute_waves_handles_unknown_dep():
    """A dep that doesn't exist in the ticket set should be ignored (wave 0)."""
    from api.services.merge_service import compute_waves
    tickets = [_make_ticket("a", deps=["nonexistent"])]
    waves = compute_waves(tickets)
    assert waves["a"] == 0


def test_compute_waves_cycle_fallback():
    """Circular deps should fall back to wave 0 without infinite loop."""
    from api.services.merge_service import compute_waves
    tickets = [
        _make_ticket("a", deps=["b"]),
        _make_ticket("b", deps=["a"]),
    ]
    waves = compute_waves(tickets)
    assert waves["a"] == 0
    assert waves["b"] == 0


# ---------------------------------------------------------------------------
# 12.1b  TicketAttempt state transitions
# ---------------------------------------------------------------------------

def test_transition_proposed_to_accepted():
    from api.services.attempt_service import transition_attempt
    attempt = MagicMock()
    attempt.status = "proposed"
    attempt.id = "test-id"
    result = transition_attempt(attempt, "accepted")
    assert result.status == "accepted"


def test_transition_accepted_to_shipped_path():
    """Full path: accepted → composed → release_pr_open → shipped."""
    from api.services.attempt_service import transition_attempt
    attempt = MagicMock()
    attempt.id = "test-id"
    attempt.status = "accepted"
    transition_attempt(attempt, "composed")
    assert attempt.status == "composed"

    attempt.status = "composed"
    transition_attempt(attempt, "release_pr_open")
    assert attempt.status == "release_pr_open"

    attempt.status = "release_pr_open"
    transition_attempt(attempt, "shipped")
    assert attempt.status == "shipped"


def test_transition_invalid_raises():
    from api.services.attempt_service import transition_attempt
    attempt = MagicMock()
    attempt.status = "shipped"
    attempt.id = "test-id"
    with pytest.raises(ValueError, match="Cannot transition"):
        transition_attempt(attempt, "accepted")


def test_transition_terminal_states_have_no_outbound():
    from api.services.attempt_service import _TRANSITIONS
    for terminal in ("shipped", "rejected", "superseded", "failed"):
        assert _TRANSITIONS[terminal] == set(), f"{terminal} should have no outbound transitions"


# ---------------------------------------------------------------------------
# 12.1c  compute_ticket_display_state
# ---------------------------------------------------------------------------

def _make_display_ticket(column_id="backlog", intent_status="ready", deps=None):
    t = MagicMock()
    t.column_id = column_id
    t.intent_status = intent_status
    t.depends_on_ticket_ids = deps or []
    t.id = "ticket-1"
    return t


def test_display_state_draft():
    from api.services.ticket_service import compute_ticket_display_state
    t = _make_display_ticket(intent_status="draft")
    assert compute_ticket_display_state(t) == "draft"


def test_display_state_archived():
    from api.services.ticket_service import compute_ticket_display_state
    t = _make_display_ticket(intent_status="archived")
    assert compute_ticket_display_state(t) == "archived"


def test_display_state_running():
    from api.services.ticket_service import compute_ticket_display_state
    t = _make_display_ticket(column_id="in_progress")
    assert compute_ticket_display_state(t, satisfied_dep_ids=set()) == "running"


def test_display_state_accepted():
    from api.services.ticket_service import compute_ticket_display_state
    accepted = MagicMock()
    accepted.status = "accepted"
    accepted.base_hash = "abc"
    t = _make_display_ticket()
    project = MagicMock()
    project.shipped_frontier = "abc"
    result = compute_ticket_display_state(t, accepted_attempt=accepted, project=project)
    assert result == "accepted"


def test_display_state_stale():
    from api.services.ticket_service import compute_ticket_display_state
    accepted = MagicMock()
    accepted.status = "accepted"
    accepted.base_hash = "old_base"
    t = _make_display_ticket()
    project = MagicMock()
    project.shipped_frontier = "new_frontier"
    result = compute_ticket_display_state(t, accepted_attempt=accepted, project=project)
    assert result == "stale"


def test_display_state_shipped():
    from api.services.ticket_service import compute_ticket_display_state
    accepted = MagicMock()
    accepted.status = "shipped"
    accepted.base_hash = None
    t = _make_display_ticket()
    result = compute_ticket_display_state(t, accepted_attempt=accepted)
    assert result == "shipped"


def test_display_state_attempt_ready():
    from api.services.ticket_service import compute_ticket_display_state
    latest = MagicMock()
    latest.status = "proposed"
    t = _make_display_ticket()
    result = compute_ticket_display_state(t, latest_attempt=latest)
    assert result == "attempt_ready"


def test_display_state_failed():
    from api.services.ticket_service import compute_ticket_display_state
    latest = MagicMock()
    latest.status = "failed"
    t = _make_display_ticket()
    result = compute_ticket_display_state(t, latest_attempt=latest)
    assert result == "failed"


# ---------------------------------------------------------------------------
# 12.1d  ship_run_to_json field presence
# ---------------------------------------------------------------------------

def test_ship_run_to_json_has_required_fields():
    from api.services.merge_service import ship_run_to_json
    run = MagicMock()
    run.id = "run-1"
    run.project_id = "proj-1"
    run.wave_num = 0
    run.status = "queued"
    run.error = None
    run.release_branch = None
    run.base_main_hash = None
    run.composed_commit_hash = None
    run.changed_files = []
    run.summary = None
    run.test_status = None
    run.test_output = None
    run.release_pr_url = None
    run.release_pr_number = None
    run.shipped_at = None
    run.shipped_commit_hash = None
    run.created_at = None
    run.updated_at = None

    result = ship_run_to_json(run)
    for field in ("id", "project_id", "wave_num", "status", "release_pr_url",
                  "release_pr_number", "shipped_commit_hash", "test_status"):
        assert field in result, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# 7.2  Structured AgentHub timeline events
# ---------------------------------------------------------------------------

def test_structured_event_content_roundtrip():
    from api.services.channel_service import event_content, parse_event_post

    content = event_content(
        "attempt_published",
        "Attempt #1 published at abc123",
        {"attempt_num": 1, "commit_hash": "abc123"},
    )
    parsed = parse_event_post({
        "id": 1,
        "content": content,
        "created_at": "2026-05-23T00:00:00Z",
    })

    assert parsed["structured"] is True
    assert parsed["event_type"] == "attempt_published"
    assert parsed["message"] == "Attempt #1 published at abc123"
    assert parsed["metadata"]["attempt_num"] == 1
    assert parsed["raw_content"] == content


def test_legacy_text_event_is_normalized():
    from api.services.channel_service import parse_event_post

    parsed = parse_event_post({"id": 2, "content": "release_pr_opened: PR #12"})

    assert parsed["structured"] is False
    assert parsed["event_type"] == "release_pr_opened"
    assert parsed["message"] == "release_pr_opened: PR #12"
