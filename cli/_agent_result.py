"""Machine-readable agent result envelope for `ta` status commands."""

from __future__ import annotations

import sys
from typing import Any, Literal

from cli._output import print_json

AGENT_RESULT_SCHEMA_VERSION = 1

AgentResultStatus = Literal["shipped", "failed", "needs_input", "running"]

_TERMINAL_FAILURE = frozenset(
    {
        "failed",
        "rejected",
        "compose_failed",
        "blocked",
        "superseded",
    }
)
_RUNNING = frozenset(
    {
        "pending",
        "running",
        "proposed",
        "validating",
        "attempt_ready",
        "queued",
        "composing",
        "shipping",
        "collecting",
    }
)
_SHIPPED = frozenset({"shipped"})


def build_result(
    *,
    status: AgentResultStatus,
    project_id: str | None = None,
    ticket_id: str | None = None,
    attempt_id: str | None = None,
    ship_run_id: str | None = None,
    candidate_id: str | None = None,
    shipped_frontier_before: str | None = None,
    shipped_frontier_after: str | None = None,
    validation_summary: dict[str, Any] | None = None,
    failure_reason: str | None = None,
    needs: list[dict[str, Any]] | None = None,
    next_commands: list[str] | None = None,
) -> dict[str, Any]:
    """Build a stable agent-result document (schema_version=1)."""
    return {
        "schema_version": AGENT_RESULT_SCHEMA_VERSION,
        "status": status,
        "project_id": project_id,
        "ticket_id": ticket_id,
        "attempt_id": attempt_id,
        "ship_run_id": ship_run_id,
        "candidate_id": candidate_id,
        "shipped_frontier_before": shipped_frontier_before,
        "shipped_frontier_after": shipped_frontier_after,
        "validation_summary": validation_summary or {},
        "failure_reason": failure_reason,
        "needs": list(needs or []),
        "next_commands": list(next_commands or []),
    }


def exit_code_for_result(result: dict[str, Any]) -> int:
    return 1 if result.get("status") == "failed" else 0


def emit_agent_result(result: dict[str, Any]) -> None:
    print_json(result)
    sys.exit(exit_code_for_result(result))


def need(
    action: str,
    message: str,
    *,
    command: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"action": action, "message": message}
    if command:
        item["command"] = command
    return item


def validation_summary_from_evidence(evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not evidence:
        return {}
    summary: dict[str, Any] = {
        "bundle_count": evidence.get("bundle_count", 0),
        "run_count": evidence.get("run_count", 0),
        "check_counts": dict(evidence.get("check_counts") or {}),
    }
    if evidence.get("status") is not None:
        summary["status"] = evidence.get("status")
    if evidence.get("target_type") is not None:
        summary["target_type"] = evidence.get("target_type")
    return summary


def validation_summary_for_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    validated = bool(attempt.get("validated"))
    if "validated" not in attempt:
        status = (attempt.get("status") or "").strip().lower()
        validated = status == "validated" or status in {"accepted", "composed", "release_pr_open", "shipped"}
    summary: dict[str, Any] = {
        "validated": validated,
        "attempt_status": attempt.get("status"),
        "test_status": attempt.get("test_status"),
    }
    if attempt.get("validation_error"):
        summary["validation_error"] = attempt.get("validation_error")
    return summary


def validation_summary_for_ship_run(run: dict[str, Any]) -> dict[str, Any]:
    evidence = run.get("evidence_summary")
    if isinstance(evidence, dict) and evidence:
        return validation_summary_from_evidence(evidence)
    summary: dict[str, Any] = {
        "ship_run_status": run.get("status"),
        "test_status": run.get("test_status"),
    }
    if run.get("validation_errors"):
        summary["validation_errors"] = list(run.get("validation_errors") or [])
    return summary


def classify_workflow_status(raw_status: str | None) -> AgentResultStatus:
    value = (raw_status or "").strip().lower()
    if not value:
        return "needs_input"
    if value in _SHIPPED:
        return "shipped"
    if value in _TERMINAL_FAILURE:
        return "failed"
    if value in _RUNNING:
        return "running"
    if value in {"validated", "accepted", "ready_to_ship", "valid", "composed", "release_pr_open"}:
        return "needs_input"
    return "needs_input"


def result_from_ticket_ledger(ledger: dict[str, Any], project_id: str) -> dict[str, Any]:
    project = ledger.get("project") or {}
    ticket = ledger.get("ticket") or {}
    ticket_id = str(ticket.get("id") or "")
    accepted = ledger.get("accepted_attempt") or {}
    candidate = ledger.get("promotion_candidate") or {}
    ship_run = ledger.get("ship_run") or {}
    attempts = list(ledger.get("attempts") or [])
    jobs = list(ledger.get("jobs") or [])
    evidence = ledger.get("evidence_summary") or {}

    attempt_id = str(accepted.get("id") or "") or None
    if not attempt_id and attempts:
        attempt_id = str(attempts[-1].get("id") or "") or None

    ship_run_id = str(ship_run.get("id") or "") or None
    candidate_id = str(candidate.get("id") or "") or None

    shipped_after = (project.get("shipped_frontier") or "").strip() or None
    shipped_before = None
    if ship_run.get("base_main_hash"):
        shipped_before = str(ship_run.get("base_main_hash"))

    failure_reason = None
    needs: list[dict[str, Any]] = []
    next_commands = list(ledger.get("next_commands") or [])

    primary_status = None
    if ship_run:
        primary_status = ship_run.get("status")
    elif accepted:
        primary_status = accepted.get("status")
    elif attempts:
        primary_status = attempts[-1].get("status")
    elif jobs:
        primary_status = jobs[-1].get("status")

    status = classify_workflow_status(primary_status)

    if ship_run and (ship_run.get("status") or "").lower() in _TERMINAL_FAILURE:
        failure_reason = ship_run.get("error") or f"ShipRun status is {ship_run.get('status')}"
    elif accepted and (accepted.get("status") or "").lower() in {"failed", "rejected"}:
        failure_reason = accepted.get("validation_error") or f"Attempt status is {accepted.get('status')}"

    latest_attempt = accepted or (attempts[-1] if attempts else {})
    if status == "needs_input":
        run_status = (ship_run.get("status") or "").lower() if ship_run else ""
        if run_status == "ready_to_ship" and ship_run_id:
            needs.append(
                need(
                    "ship_run",
                    "ShipRun is ready to ship.",
                    command=f"ta ship ship-run {project_id} {ship_run_id}",
                )
            )
        elif candidate_id and not ship_run_id:
            needs.append(
                need(
                    "compose_candidate",
                    "Promotion candidate exists; compose it into a ShipRun.",
                    command=f"ta ship compose-candidate {project_id} {candidate_id} --sync",
                )
            )
        elif latest_attempt:
            attempt_status = (latest_attempt.get("status") or "").lower()
            if attempt_status == "validated" and attempt_id and ticket_id:
                needs.append(
                    need(
                        "choose_winner",
                        "Validated attempt is ready for winner selection.",
                        command=f"ta ticket choose-winner {project_id} {ticket_id} {attempt_id}",
                    )
                )
            elif latest_attempt.get("is_winner") and attempt_status == "validated" and ticket_id and attempt_id:
                needs.append(
                    need(
                        "accept_winner",
                        "Winner chosen; accept the attempt to integrate.",
                        command=f"ta ticket accept-winner {project_id} {ticket_id} {attempt_id}",
                    )
                )
        active_job = next(
            (job for job in jobs if (job.get("status") or "").lower() in {"pending", "running"}),
            None,
        )
        if active_job:
            status = "running"
            needs = [
                need(
                    "wait_for_job",
                    f"Agent job is {active_job.get('status')}.",
                )
            ]

    validation_summary = validation_summary_from_evidence(evidence)
    if latest_attempt:
        validation_summary = {
            **validation_summary,
            **{k: v for k, v in validation_summary_for_attempt(latest_attempt).items() if v is not None},
        }

    return build_result(
        status=status,
        project_id=project_id,
        ticket_id=ticket_id or None,
        attempt_id=attempt_id,
        ship_run_id=ship_run_id,
        candidate_id=candidate_id,
        shipped_frontier_before=shipped_before,
        shipped_frontier_after=shipped_after,
        validation_summary=validation_summary,
        failure_reason=failure_reason,
        needs=needs,
        next_commands=next_commands,
    )


def result_from_attempt(
    attempt: dict[str, Any],
    project_id: str,
    *,
    shipped_frontier: str | None = None,
    next_commands: list[str] | None = None,
) -> dict[str, Any]:
    ticket_id = str(attempt.get("ticket_id") or "") or None
    attempt_id = str(attempt.get("id") or attempt.get("attempt_id") or "") or None
    raw_status = attempt.get("status")
    status = classify_workflow_status(raw_status)
    failure_reason = None
    needs: list[dict[str, Any]] = []

    attempt_status = (raw_status or "").strip().lower()
    if attempt_status in {"failed", "rejected"}:
        status = "failed"
        failure_reason = attempt.get("validation_error") or f"Attempt status is {raw_status}"
    elif attempt_status == "shipped":
        status = "shipped"
    elif attempt_status in {"validating", "proposed"}:
        status = "running"
    elif attempt_status == "validated" and ticket_id and attempt_id:
        status = "needs_input"
        if attempt.get("is_winner"):
            needs.append(
                need(
                    "accept_winner",
                    "Winner chosen; accept the attempt.",
                    command=f"ta ticket accept-winner {project_id} {ticket_id} {attempt_id}",
                )
            )
        else:
            needs.append(
                need(
                    "choose_winner",
                    "Validated attempt awaits winner selection.",
                    command=f"ta ticket choose-winner {project_id} {ticket_id} {attempt_id}",
                )
            )
    elif attempt_status in {"accepted", "composed", "release_pr_open"}:
        status = "needs_input"
        needs.append(
            need(
                "promote",
                "Accepted attempt can enter Ship Room.",
                command=f"ta ship create-candidate {project_id} --attempt {attempt_id}",
            )
        )

    base_hash = (attempt.get("base_hash") or "").strip() or None
    frontier_after = shipped_frontier
    frontier_before = base_hash if frontier_after and base_hash and base_hash != frontier_after else base_hash

    return build_result(
        status=status,
        project_id=project_id,
        ticket_id=ticket_id,
        attempt_id=attempt_id,
        shipped_frontier_before=frontier_before,
        shipped_frontier_after=frontier_after,
        validation_summary=validation_summary_for_attempt(attempt),
        failure_reason=failure_reason,
        needs=needs,
        next_commands=next_commands or [],
    )


def result_from_ship_run(
    run: dict[str, Any],
    project_id: str,
    *,
    shipped_frontier_before: str | None = None,
    shipped_frontier_after: str | None = None,
) -> dict[str, Any]:
    ship_run_id = str(run.get("id") or "") or None
    candidate_id = str(run.get("promotion_candidate_id") or "") or None
    raw_status = run.get("status")
    status = classify_workflow_status(raw_status)
    failure_reason = None
    needs: list[dict[str, Any]] = []

    run_status = (raw_status or "").strip().lower()
    if run_status in {"failed", "compose_failed"}:
        status = "failed"
        failure_reason = run.get("error") or f"ShipRun status is {raw_status}"
    elif run_status == "ready_to_ship" and ship_run_id:
        status = "needs_input"
        needs.append(
            need(
                "ship_run",
                "ShipRun is ready to ship.",
                command=f"ta ship ship-run {project_id} {ship_run_id}",
            )
        )
    elif run_status in {"queued", "composing"}:
        status = "running"
        needs.append(
            need(
                "compose",
                "ShipRun composition is in progress or queued.",
                command=f"ta ship compose-run {project_id} {ship_run_id}",
            )
        )

    frontier_after = shipped_frontier_after or run.get("shipped_commit_hash")
    frontier_before = shipped_frontier_before or run.get("base_main_hash")

    next_commands = [
        f"ta ship run {project_id} {ship_run_id}" if ship_run_id else "",
        f"ta ship candidates {project_id}",
    ]
    next_commands = [cmd for cmd in next_commands if cmd]

    return build_result(
        status=status,
        project_id=project_id,
        ship_run_id=ship_run_id,
        candidate_id=candidate_id,
        shipped_frontier_before=frontier_before,
        shipped_frontier_after=frontier_after,
        validation_summary=validation_summary_for_ship_run(run),
        failure_reason=failure_reason,
        needs=needs,
        next_commands=next_commands,
    )


def result_from_operator_loop(receipt: dict[str, Any]) -> dict[str, Any]:
    project_id = str(receipt.get("project_id") or "")
    ticket_id = str(receipt.get("ticket_id") or "") or None
    attempt_id = str(receipt.get("attempt_id") or "") or None
    ship_run_id = str(receipt.get("ship_run_id") or "") or None
    candidate_id = str(receipt.get("candidate_id") or "") or None

    ship_step = (receipt.get("steps") or {}).get("ship_run") or {}
    raw_status = receipt.get("status") or ship_step.get("status")
    status = classify_workflow_status(raw_status)
    if (raw_status or "").lower() != "shipped" and ship_step.get("shipped_commit_hash"):
        status = "shipped"

    evaluate = (receipt.get("steps") or {}).get("evaluate_attempts") or {}
    attempts = list(evaluate.get("attempts") or [])
    validation_summary: dict[str, Any] = {}
    if attempts:
        match = next(
            (item for item in attempts if item.get("attempt_id") == attempt_id),
            attempts[0],
        )
        validation_summary = {
            "validated": bool(match.get("validated")),
            "attempt_status": match.get("status"),
            "recommendation": match.get("recommendation"),
        }
    validation_summary["shipped_frontier_at_evaluate"] = evaluate.get("shipped_frontier") or evaluate.get(
        "frontier_id"
    )

    return build_result(
        status=status,
        project_id=project_id or None,
        ticket_id=ticket_id,
        attempt_id=attempt_id,
        ship_run_id=ship_run_id,
        candidate_id=candidate_id,
        shipped_frontier_before=receipt.get("shipped_frontier_before"),
        shipped_frontier_after=receipt.get("shipped_frontier_after"),
        validation_summary=validation_summary,
        failure_reason=None,
        needs=[],
        next_commands=list(receipt.get("next_commands") or []),
    )
