"""Tests for auto-ship LLM winner judge and fallback."""

from contextlib import contextmanager
from unittest.mock import patch

from models.db import db, Project, Ticket, TicketAttempt

_MOCK_LLM_SETTINGS = {"url": "http://llm.test/v1", "model": "test-model", "api_key": "sk-test"}


@contextmanager
def _configured_llm():
    with patch(
        "api.services.auto_ship_judge_service.get_frontend_llm_settings",
        return_value=dict(_MOCK_LLM_SETTINGS),
    ):
        yield


def _add_two_attempts(client, project, *, test1="failed", test2="passed"):
    pid = project["id"]
    frontier = project["accepted_frontier_id"]
    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = frontier
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Judge ticket",
            description="Implement feature X",
            acceptance_criteria="Tests pass and behavior matches spec",
            intent_status="active",
            base_leaf_id=frontier,
        )
        db.session.add(ticket)
        db.session.flush()
        a1 = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="a" * 40,
            base_hash=frontier,
            attempt_num=1,
            status="validated",
            test_status=test1,
            summary="attempt 1",
        )
        a2 = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="b" * 40,
            base_hash=frontier,
            attempt_num=2,
            status="validated",
            test_status=test2,
            summary="attempt 2",
        )
        db.session.add_all([a1, a2])
        db.session.commit()
        return str(pid), str(ticket.id), str(a1.id), str(a2.id)


def test_judge_picks_llm_winner_over_rule(client, project):
    from api.services.auto_ship_judge_service import pick_auto_winner_with_decision

    pid, ticket_id, a1_id, a2_id = _add_two_attempts(client, project)
    llm_json = (
        f'{{"winner_attempt_id": "{a2_id}", "rationale": "more complete", '
        f'"notes": {{"{a1_id}": "partial"}}}}'
    )
    with _configured_llm(), patch(
        "api.services.auto_ship_judge_service.complete_user_prompt",
        return_value=(llm_json, "test-model"),
    ):
        with client.application.app_context():
            proj = db.session.get(Project, pid)
            ticket = db.session.get(Ticket, ticket_id)
            winner, decision = pick_auto_winner_with_decision(proj, ticket)
    assert winner.agenthub_commit_hash == "b" * 40
    assert decision["judged_by"] == "llm"
    assert decision["model"] == "test-model"
    assert decision["rationale"] == "more complete"
    assert decision["notes"][a1_id] == "partial"


def test_invalid_judge_output_falls_back_to_rule(client, project):
    from api.services.auto_ship_judge_service import pick_auto_winner_with_decision

    pid, ticket_id, _a1_id, a2_id = _add_two_attempts(client, project)
    with _configured_llm(), patch(
        "api.services.auto_ship_judge_service.complete_user_prompt",
        return_value=('{"winner_attempt_id": "not-a-real-id"}', "test-model"),
    ):
        with client.application.app_context():
            proj = db.session.get(Project, pid)
            ticket = db.session.get(Ticket, ticket_id)
            winner, decision = pick_auto_winner_with_decision(proj, ticket)
    assert str(winner.id) == a2_id
    assert decision["judged_by"] == "fallback"
    assert decision["fallback_reason"]


def test_single_eligible_skips_llm(client, project):
    from api.services.auto_ship_judge_service import pick_auto_winner_with_decision

    pid = project["id"]
    frontier = project["accepted_frontier_id"]
    ticket_id = None
    only_id = None
    with client.application.app_context():
        proj = db.session.get(Project, pid)
        proj.shipped_frontier = frontier
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Single",
            intent_status="active",
            base_leaf_id=frontier,
        )
        db.session.add(ticket)
        db.session.flush()
        only = TicketAttempt(
            project_id=pid,
            ticket_id=ticket.id,
            agenthub_commit_hash="c" * 40,
            base_hash=frontier,
            attempt_num=1,
            status="validated",
            test_status="passed",
        )
        db.session.add(only)
        db.session.commit()
        ticket_id = str(ticket.id)
        only_id = str(only.id)

    with patch("api.services.auto_ship_judge_service.complete_user_prompt") as mocked:
        with client.application.app_context():
            proj = db.session.get(Project, pid)
            ticket = db.session.get(Ticket, ticket_id)
            winner, decision = pick_auto_winner_with_decision(proj, ticket)
    mocked.assert_not_called()
    assert str(winner.id) == only_id
    assert decision["judged_by"] == "single"


def test_llm_exception_falls_back(client, project):
    from api.services.auto_ship_judge_service import pick_auto_winner_with_decision

    pid, ticket_id, _a1_id, a2_id = _add_two_attempts(client, project)
    with _configured_llm(), patch(
        "api.services.auto_ship_judge_service.complete_user_prompt",
        side_effect=RuntimeError("timeout"),
    ):
        with client.application.app_context():
            proj = db.session.get(Project, pid)
            ticket = db.session.get(Ticket, ticket_id)
            winner, decision = pick_auto_winner_with_decision(proj, ticket)
    assert str(winner.id) == a2_id
    assert decision["judged_by"] == "fallback"
    assert "timeout" in decision["fallback_reason"]


def test_pick_auto_winner_prefers_passed_test_on_fallback(client, project):
    from api.services.auto_ship_judge_service import pick_auto_winner_with_decision

    pid, ticket_id, _a1_id, _a2_id = _add_two_attempts(client, project)
    with _configured_llm(), patch(
        "api.services.auto_ship_judge_service.complete_user_prompt",
        side_effect=ValueError("no model"),
    ):
        with client.application.app_context():
            proj = db.session.get(Project, pid)
            ticket = db.session.get(Ticket, ticket_id)
            winner, decision = pick_auto_winner_with_decision(proj, ticket)
    assert winner.agenthub_commit_hash == "b" * 40
    assert decision["judged_by"] == "fallback"


def test_judge_prompt_prioritizes_codebase_correctness(client, project):
    from api.services.auto_ship_judge_service import _build_judge_prompt
    from models.db import Ticket

    pid = project["id"]
    with client.application.app_context():
        ticket = Ticket(
            project_id=pid,
            column_id="done",
            title="Stale ticket",
            description="Add feature that already exists",
            intent_status="active",
        )
        prompt = _build_judge_prompt(
            ticket,
            [
                {
                    "attempt_id": "a",
                    "final_summary": "Work already on main; no change needed.",
                    "diff": "",
                }
            ],
        )
    assert "Correctness against the actual current codebase comes first" in prompt
    assert "final_summary" in prompt
