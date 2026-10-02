"""Unit tests for cli._agent_result."""

import pytest

from cli._agent_result import (
    AGENT_RESULT_SCHEMA_VERSION,
    build_result,
    exit_code_for_result,
    result_from_attempt,
    result_from_operator_loop,
    result_from_ship_run,
    result_from_ticket_ledger,
)

STABLE_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "project_id",
        "ticket_id",
        "attempt_id",
        "ship_run_id",
        "candidate_id",
        "shipped_frontier_before",
        "shipped_frontier_after",
        "validation_summary",
        "failure_reason",
        "needs",
        "next_commands",
    }
)


def test_build_result_has_stable_keys_and_version():
    result = build_result(status="running", project_id="proj-1")
    assert set(result.keys()) == STABLE_KEYS
    assert result["schema_version"] == AGENT_RESULT_SCHEMA_VERSION
    assert result["status"] == "running"
    assert result["validation_summary"] == {}
    assert result["needs"] == []


def test_exit_code_failed_only():
    assert exit_code_for_result(build_result(status="shipped")) == 0
    assert exit_code_for_result(build_result(status="needs_input")) == 0
    assert exit_code_for_result(build_result(status="running")) == 0
    assert exit_code_for_result(build_result(status="failed")) == 1


def test_result_from_attempt_validated_needs_choose_winner():
    attempt = {
        "id": "attempt-1",
        "ticket_id": "ticket-1",
        "status": "validated",
        "validated": True,
        "test_status": "passed",
    }
    result = result_from_attempt(attempt, "proj", shipped_frontier="a" * 40)
    assert result["status"] == "needs_input"
    assert result["attempt_id"] == "attempt-1"
    assert result["needs"][0]["action"] == "choose_winner"
    assert "choose-winner" in result["needs"][0]["command"]


def test_result_from_attempt_failed():
    attempt = {
        "id": "attempt-1",
        "ticket_id": "ticket-1",
        "status": "failed",
        "validation_error": "tests broke",
    }
    result = result_from_attempt(attempt, "proj")
    assert result["status"] == "failed"
    assert result["failure_reason"] == "tests broke"


def test_result_from_ship_run_ready_to_ship():
    run = {
        "id": "run-1",
        "promotion_candidate_id": "cand-1",
        "status": "ready_to_ship",
        "base_main_hash": "a" * 40,
        "test_status": "passed",
    }
    result = result_from_ship_run(run, "proj", shipped_frontier_after="a" * 40)
    assert result["status"] == "needs_input"
    assert result["ship_run_id"] == "run-1"
    assert result["needs"][0]["action"] == "ship_run"


def test_result_from_ticket_ledger_shipped():
    ledger = {
        "project": {"id": "proj", "shipped_frontier": "d" * 40},
        "ticket": {"id": "ticket-1"},
        "accepted_attempt": {"id": "attempt-1", "status": "accepted"},
        "promotion_candidate": {"id": "cand-1"},
        "ship_run": {
            "id": "run-1",
            "status": "shipped",
            "base_main_hash": "b" * 40,
        },
        "evidence_summary": {"bundle_count": 1, "run_count": 1, "check_counts": {"passed": 1}},
        "attempts": [],
        "jobs": [],
        "next_commands": ["ta context proj --ticket ticket-1 --agent"],
    }
    result = result_from_ticket_ledger(ledger, "proj")
    assert result["status"] == "shipped"
    assert result["ticket_id"] == "ticket-1"
    assert result["validation_summary"]["bundle_count"] == 1


def test_result_from_operator_loop_shipped():
    receipt = {
        "project_id": "proj",
        "ticket_id": "ticket-1",
        "attempt_id": "attempt-1",
        "candidate_id": "cand-1",
        "ship_run_id": "run-1",
        "shipped_frontier_before": "a" * 40,
        "shipped_frontier_after": "c" * 40,
        "status": "shipped",
        "steps": {
            "evaluate_attempts": {
                "attempts": [{"attempt_id": "attempt-1", "validated": True, "status": "validated"}],
                "frontier_id": "a" * 40,
            },
            "ship_run": {"status": "shipped", "shipped_commit_hash": "c" * 40},
        },
        "next_commands": ["ta project show proj"],
    }
    result = result_from_operator_loop(receipt)
    assert result["status"] == "shipped"
    assert result["shipped_frontier_after"] == "c" * 40
    assert result["validation_summary"]["validated"] is True
