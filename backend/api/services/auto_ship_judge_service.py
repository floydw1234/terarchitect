"""LLM judge for auto-ship winner selection with deterministic fallback."""

from __future__ import annotations

import json
from typing import Any

from flask import current_app

from models.db import EvidenceBundle, Project, Ticket, TicketAttempt

try:
    from utils.app_settings import get_frontend_llm_settings
    from utils.frontend_llm_client import complete_user_prompt, strip_json_fences
except (ModuleNotFoundError, ImportError):
    from backend.utils.app_settings import get_frontend_llm_settings
    from backend.utils.frontend_llm_client import complete_user_prompt, strip_json_fences

from .attempt_inspection_service import inspect_diff
from .attempt_service import attempt_is_validated as _attempt_is_validated

AUTO_SHIP_DIFF_MAX_BYTES = 20_000
REVIEWER_NOTES_MAX_CHARS = 8_000


def list_eligible_auto_ship_attempts(project: Project, ticket: Ticket) -> list[TicketAttempt]:
    from .attempt_service import attempt_stale_status as _attempt_stale_status
    from models.db import TicketAttempt as AttemptModel

    attempts = (
        AttemptModel.query.filter_by(project_id=project.id, ticket_id=ticket.id)
        .order_by(AttemptModel.attempt_num.asc())
        .all()
    )
    eligible: list[TicketAttempt] = []
    for attempt in attempts:
        if not _attempt_is_validated(attempt):
            continue
        stale, _reason = _attempt_stale_status(
            attempt,
            project=project,
            ticket=ticket,
        )
        if stale is not False:
            continue
        eligible.append(attempt)
    return eligible


def _rank_attempts_rule(eligible: list[TicketAttempt]) -> list[TicketAttempt]:
    def _rank(item: TicketAttempt) -> tuple[int, int]:
        test_status = (item.test_status or "").strip().lower()
        passed_rank = 0 if test_status == "passed" else 1
        return (passed_rank, item.attempt_num or 999)

    return sorted(eligible, key=_rank)


def pick_auto_winner_by_rule(eligible: list[TicketAttempt]) -> TicketAttempt:
    ranked = _rank_attempts_rule(eligible)
    return ranked[0]


def _validation_summary(attempt: TicketAttempt) -> dict[str, Any]:
    return {
        "status": attempt.status,
        "validated_at": attempt.validated_at.isoformat() if attempt.validated_at else None,
        "test_status": attempt.test_status,
        "test_output": (attempt.test_output or "")[:4000] or None,
        "validation_error": attempt.validation_error,
        "summary": attempt.summary,
    }


def _reviewer_notes(project_id, attempt_id) -> str | None:
    bundles = (
        EvidenceBundle.query.filter_by(
            project_id=project_id,
            target_type="attempt",
            target_id=attempt_id,
        )
        .order_by(EvidenceBundle.created_at.desc())
        .all()
    )
    parts: list[str] = []
    for bundle in bundles:
        for check in bundle.checks or []:
            if check.check_type != "llm_review":
                continue
            meta = check.check_metadata if isinstance(check.check_metadata, dict) else {}
            reviewer = meta.get("reviewer") or check.tool_name or "reviewer"
            findings = meta.get("findings")
            chunk = f"[{reviewer}] status={check.status}"
            if bundle.summary:
                chunk += f" summary={bundle.summary}"
            if findings:
                chunk += f" findings={json.dumps(findings, ensure_ascii=False)[:2000]}"
            elif check.output:
                chunk += f" output={(check.output or '')[:2000]}"
            parts.append(chunk)
    if not parts:
        return None
    text = "\n".join(parts)
    if len(text) > REVIEWER_NOTES_MAX_CHARS:
        return text[:REVIEWER_NOTES_MAX_CHARS] + "…"
    return text


def _attempt_payload(project: Project, attempt: TicketAttempt) -> dict[str, Any]:
    diff_info = inspect_diff(project, attempt, max_bytes=AUTO_SHIP_DIFF_MAX_BYTES)
    return {
        "attempt_id": str(attempt.id),
        "attempt_num": attempt.attempt_num,
        "agent_id": attempt.agent_id,
        "commit_hash": attempt.agenthub_commit_hash,
        "validation": _validation_summary(attempt),
        "reviewer_notes": _reviewer_notes(project.id, attempt.id),
        "diff": diff_info.get("diff") or "",
        "diff_truncated": bool(diff_info.get("truncated")),
        "diff_unavailable_reason": diff_info.get("unavailable_reason"),
    }


def _build_judge_prompt(ticket: Ticket, attempts_payload: list[dict[str, Any]]) -> str:
    ticket_block = {
        "title": ticket.title,
        "description": ticket.description,
        "acceptance_criteria": ticket.acceptance_criteria,
    }
    return (
        "You are selecting the best validated implementation attempt for automatic shipping.\n"
        "Pick the attempt that best fulfills the ticket, preferring correct and complete work, "
        "then the smallest and cleanest change.\n\n"
        f"Ticket:\n{json.dumps(ticket_block, ensure_ascii=False, indent=2)}\n\n"
        f"Eligible attempts:\n{json.dumps(attempts_payload, ensure_ascii=False, indent=2)}\n\n"
        "Respond with strict JSON only (no markdown), shape:\n"
        '{"winner_attempt_id": "<uuid>", "rationale": "short", '
        '"notes": {"<attempt_id>": "optional per-attempt note"}}\n'
        "winner_attempt_id must be one of the attempt_id values above."
    )


def _parse_judge_response(
    content: str,
    eligible_ids: set[str],
) -> tuple[str, str, dict[str, str] | None]:
    parsed = json.loads(strip_json_fences(content))
    if not isinstance(parsed, dict):
        raise ValueError("judge response must be a JSON object")
    winner_id = str(parsed.get("winner_attempt_id") or "").strip()
    if not winner_id or winner_id not in eligible_ids:
        raise ValueError("winner_attempt_id is missing or not eligible")
    rationale = str(parsed.get("rationale") or "").strip() or "No rationale provided."
    notes_raw = parsed.get("notes")
    notes: dict[str, str] | None = None
    if isinstance(notes_raw, dict):
        notes = {str(k): str(v) for k, v in notes_raw.items()}
    return winner_id, rationale, notes


def _decision_record(
    *,
    winner_attempt_id: str,
    rationale: str,
    judged_by: str,
    eligible: list[TicketAttempt],
    model: str | None = None,
    fallback_reason: str | None = None,
    notes: dict[str, str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "winner_attempt_id": winner_attempt_id,
        "rationale": rationale,
        "judged_by": judged_by,
        "eligible_attempt_ids": [str(a.id) for a in eligible],
    }
    if model:
        out["model"] = model
    if fallback_reason:
        out["fallback_reason"] = fallback_reason
    if notes:
        out["notes"] = notes
    return out


def _fallback_decision(
    eligible: list[TicketAttempt],
    reason: str,
    *,
    rationale: str | None = None,
) -> tuple[TicketAttempt, dict[str, Any]]:
    winner = pick_auto_winner_by_rule(eligible)
    llm = get_frontend_llm_settings()
    model = (llm.get("model") or "").strip() or None
    decision = _decision_record(
        winner_attempt_id=str(winner.id),
        rationale=rationale or "Selected by fallback rule (tests passed first, then lowest attempt_num).",
        judged_by="fallback",
        eligible=eligible,
        model=model,
        fallback_reason=reason,
    )
    current_app.logger.warning("auto_ship judge fallback: %s", reason)
    return winner, decision


def judge_auto_winner_attempt(
    project: Project,
    ticket: Ticket,
    eligible: list[TicketAttempt],
) -> tuple[TicketAttempt | None, dict[str, Any] | None]:
    if not eligible:
        return None, None

    if len(eligible) == 1:
        only = eligible[0]
        decision = _decision_record(
            winner_attempt_id=str(only.id),
            rationale="Only one eligible validated attempt.",
            judged_by="single",
            eligible=eligible,
        )
        return only, decision

    llm = get_frontend_llm_settings()
    if not (llm.get("model") or "").strip():
        return _fallback_decision(eligible, "no LLM model configured")

    eligible_ids = {str(a.id) for a in eligible}
    attempts_payload = [_attempt_payload(project, a) for a in eligible]
    prompt = _build_judge_prompt(ticket, attempts_payload)

    try:
        content, model_name = complete_user_prompt(prompt)
        winner_id, rationale, notes = _parse_judge_response(content, eligible_ids)
    except Exception as exc:
        return _fallback_decision(eligible, str(exc))

    winner = next((a for a in eligible if str(a.id) == winner_id), None)
    if winner is None:
        return _fallback_decision(eligible, "winner_attempt_id did not match an eligible attempt")

    decision = _decision_record(
        winner_attempt_id=winner_id,
        rationale=rationale,
        judged_by="llm",
        eligible=eligible,
        model=model_name,
        notes=notes,
    )
    return winner, decision


def pick_auto_winner_with_decision(
    project: Project,
    ticket: Ticket,
) -> tuple[TicketAttempt | None, dict[str, Any] | None]:
    eligible = list_eligible_auto_ship_attempts(project, ticket)
    return judge_auto_winner_attempt(project, ticket, eligible)
