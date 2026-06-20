"""
Unit tests (plan 12.1).

Tests that require no HTTP or DB — pure service/model logic.

Covers:
  - wave computation
  - promotion candidate graph analysis
  - base selection (compute_base_hash)
  - TicketAttempt state transitions, including legacy compatibility paths
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
# 12.1aa  Promotion candidate graph analysis
# ---------------------------------------------------------------------------

def _make_attempt(aid, ticket_id, commit_hash, base_hash, status="accepted", attempt_num=1):
    attempt = MagicMock()
    attempt.id = aid
    attempt.ticket_id = ticket_id
    attempt.agenthub_commit_hash = commit_hash
    attempt.base_hash = base_hash
    attempt.status = status
    attempt.attempt_num = attempt_num
    return attempt


def test_promotion_candidate_graph_auto_includes_dependency_closure():
    from api.services.merge_service import analyze_promotion_candidate_graph

    tickets = [
        _make_ticket("parent"),
        _make_ticket("child", deps=["parent"]),
    ]
    parent_attempt = _make_attempt("a-parent", "parent", "p" * 40, "f" * 40)
    child_attempt = _make_attempt("a-child", "child", "c" * 40, "p" * 40)

    result = analyze_promotion_candidate_graph(
        frontier="f" * 40,
        tickets=tickets,
        selected_attempts=[child_attempt],
        accepted_attempts_by_ticket_id={
            "parent": parent_attempt,
            "child": child_attempt,
        },
    )

    assert result["status"] == "valid"
    assert result["selected_attempt_ids"] == ["a-child", "a-parent"]
    assert result["selected_leaf_hashes"] == ["c" * 40]
    assert result["validation_summary"]["auto_included_dependency_attempt_ids"] == ["a-parent"]


def test_promotion_candidate_graph_blocks_ambiguous_multi_parent_ancestry():
    from api.services.merge_service import analyze_promotion_candidate_graph

    tickets = [
        _make_ticket("left"),
        _make_ticket("right"),
        _make_ticket("child", deps=["left", "right"]),
    ]
    left_attempt = _make_attempt("a-left", "left", "l" * 40, "f" * 40)
    right_attempt = _make_attempt("a-right", "right", "r" * 40, "f" * 40)
    child_attempt = _make_attempt("a-child", "child", "c" * 40, "l" * 40)

    result = analyze_promotion_candidate_graph(
        frontier="f" * 40,
        tickets=tickets,
        selected_attempts=[child_attempt],
        accepted_attempts_by_ticket_id={
            "left": left_attempt,
            "right": right_attempt,
            "child": child_attempt,
        },
    )

    assert result["status"] == "blocked"
    assert any("ambiguous multi-parent ancestry" in blocker for blocker in result["validation_summary"]["blockers"])


# ---------------------------------------------------------------------------
# 12.1b  TicketAttempt state transitions
# ---------------------------------------------------------------------------

def test_transition_proposed_to_validated_then_accepted():
    from api.services.attempt_service import transition_attempt
    attempt = MagicMock()
    attempt.status = "proposed"
    attempt.id = "test-id"

    result = transition_attempt(attempt, "validated")
    assert result.status == "validated"

    result = transition_attempt(attempt, "accepted")
    assert result.status == "accepted"


def test_transition_proposed_to_rejected():
    from api.services.attempt_service import transition_attempt
    attempt = MagicMock()
    attempt.status = "proposed"
    attempt.id = "test-id"
    result = transition_attempt(attempt, "rejected")
    assert result.status == "rejected"


def test_transition_accepted_to_superseded():
    from api.services.attempt_service import transition_attempt
    attempt = MagicMock()
    attempt.status = "accepted"
    attempt.id = "test-id"
    result = transition_attempt(attempt, "superseded")
    assert result.status == "superseded"


def test_transition_accepted_to_shipped_path():
    """Legacy-compatible release path: accepted → composed → release_pr_open → shipped."""
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
    project.accepted_frontier_id = "abc"
    result = compute_ticket_display_state(t, accepted_attempt=accepted, project=project)
    assert result == "accepted"


def test_display_state_stale():
    from api.services.ticket_service import compute_ticket_display_state
    accepted = MagicMock()
    accepted.status = "accepted"
    accepted.base_hash = "old_base"
    t = _make_display_ticket()
    project = MagicMock()
    project.accepted_frontier_id = "new_frontier"
    result = compute_ticket_display_state(t, accepted_attempt=accepted, project=project)
    assert result == "stale"


def test_ticket_stale_status_true_when_base_differs_from_accepted_frontier():
    from api.services.ticket_service import ticket_stale_status

    ticket = MagicMock()
    ticket.base_leaf_id = "leaf_old"
    project = MagicMock()
    project.accepted_frontier_id = "leaf_new"

    stale, reason = ticket_stale_status(ticket, project)

    assert stale is True
    assert "differs from project.accepted_frontier_id" in reason


def test_ticket_stale_status_false_when_base_matches_accepted_frontier():
    from api.services.ticket_service import ticket_stale_status

    ticket = MagicMock()
    ticket.base_leaf_id = "leaf_same"
    project = MagicMock()
    project.accepted_frontier_id = "leaf_same"

    stale, reason = ticket_stale_status(ticket, project)

    assert stale is False
    assert reason is None


def test_ticket_stale_status_reports_missing_values_clearly():
    from api.services.ticket_service import ticket_stale_status

    ticket = MagicMock()
    ticket.base_leaf_id = None
    project = MagicMock()
    project.accepted_frontier_id = None

    stale, reason = ticket_stale_status(ticket, project)

    assert stale is None
    assert reason == "Cannot determine ticket staleness: ticket.base_leaf_id is not set and project.accepted_frontier_id is not set."


def test_attempt_stale_status_true_when_base_differs_from_accepted_frontier():
    from api.services.attempt_service import attempt_stale_status

    attempt = MagicMock()
    attempt.base_hash = "leaf_old"
    project = MagicMock()
    project.accepted_frontier_id = "leaf_new"

    stale, reason = attempt_stale_status(attempt, project)

    assert stale is True
    assert "differs from project.accepted_frontier_id" in reason


def test_attempt_stale_status_false_when_base_matches_accepted_frontier():
    from api.services.attempt_service import attempt_stale_status

    attempt = MagicMock()
    attempt.base_hash = "leaf_same"
    project = MagicMock()
    project.accepted_frontier_id = "leaf_same"

    stale, reason = attempt_stale_status(attempt, project)

    assert stale is False
    assert reason is None


def test_attempt_stale_status_reports_missing_values_clearly():
    from api.services.attempt_service import attempt_stale_status

    attempt = MagicMock()
    attempt.base_hash = None
    project = MagicMock()
    project.accepted_frontier_id = None

    stale, reason = attempt_stale_status(attempt, project)

    assert stale is None
    assert reason == "Cannot determine attempt staleness: attempt.base_hash is not set and project.accepted_frontier_id is not set."


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
