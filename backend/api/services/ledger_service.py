"""Ticket ledger projection service."""
from collections import Counter

from models.db import AgentJob, EvidenceBundle, EvidenceRun, PromotionCandidate, ShipRun, Ticket, TicketAttempt

from .attempt_service import get_accepted_attempt
from .evidence_service import evidence_bundle_to_json, evidence_run_to_json
from .merge_service import promotion_candidate_to_json, ship_run_to_json
from .project_service import project_to_json
from .ticket_service import ticket_to_json


def build_ticket_ledger(project) -> dict:
    return {
        "project": project_to_json(project),
    }


def project_ticket_ledger(project, ticket: Ticket) -> dict:
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project.id, ticket_id=ticket.id)
        .order_by(TicketAttempt.attempt_num.asc(), TicketAttempt.created_at.asc())
        .all()
    )
    jobs = (
        AgentJob.query
        .filter_by(project_id=project.id, ticket_id=ticket.id)
        .order_by(AgentJob.created_at.asc())
        .all()
    )
    accepted_attempt = get_accepted_attempt(ticket.id)
    candidate = _candidate_for_ticket(project.id, ticket.id, attempts, accepted_attempt)
    ship_run = _ship_run_for_candidate(project.id, candidate)
    evidence_summary = _evidence_summary(project.id, attempts, ship_run)

    timeline = [
        _timeline_item(
            "ticket",
            ticket.id,
            label=f"Ticket {ticket.title}",
            status=ticket.column_id,
            created_at=ticket.created_at,
            summary=ticket.acceptance_criteria or ticket.description,
        )
    ]
    timeline.extend(
        _timeline_item(
            "job",
            job.id,
            label=f"Job {job.kind}",
            status=job.status,
            created_at=job.created_at,
        )
        for job in jobs
    )
    timeline.extend(
        _timeline_item(
            "attempt",
            attempt.id,
            label=f"Attempt #{attempt.attempt_num} published",
            status=attempt.status,
            created_at=attempt.created_at,
            commit_hash=attempt.agenthub_commit_hash,
            base_hash=attempt.base_hash,
            summary=attempt.summary,
        )
        for attempt in attempts
    )
    if accepted_attempt is not None:
        timeline.append(
            _timeline_item(
                "acceptance",
                accepted_attempt.id,
                label=f"Attempt #{accepted_attempt.attempt_num} accepted",
                status=accepted_attempt.status,
                created_at=accepted_attempt.updated_at or accepted_attempt.created_at,
                commit_hash=accepted_attempt.agenthub_commit_hash,
            )
        )
    if candidate is not None:
        timeline.append(
            _timeline_item(
                "promotion_candidate",
                candidate.id,
                label="Promotion candidate formed",
                status=candidate.status,
                created_at=candidate.created_at,
                commit_hash=candidate.composed_commit_hash,
            )
        )
    if ship_run is not None:
        timeline.append(
            _timeline_item(
                "ship_run",
                ship_run.id,
                label="ShipRun created",
                status=ship_run.status,
                created_at=ship_run.created_at,
                commit_hash=ship_run.composed_commit_hash,
            )
        )
        if ship_run.release_pr_url:
            timeline.append(
                _timeline_item(
                    "pull_request",
                    ship_run.id,
                    label=f"Release PR #{ship_run.release_pr_number or '?'}",
                    status=ship_run.status,
                    created_at=ship_run.updated_at or ship_run.created_at,
                    url=ship_run.release_pr_url,
                )
            )
    if getattr(project, "shipped_frontier", None):
        frontier_time = None
        if ship_run is not None:
            frontier_time = ship_run.shipped_at
        frontier_time = frontier_time or getattr(project, "shipped_frontier_updated_at", None)
        timeline.append(
            _timeline_item(
                "frontier",
                ticket.id,
                label="Project frontier advanced",
                status=ship_run.status if ship_run is not None else None,
                created_at=frontier_time,
                commit_hash=project.shipped_frontier,
            )
        )

    next_commands = [
        f"ta context {project.id} --ticket {ticket.id} --agent",
        f"ta ticket attempts {project.id} {ticket.id}",
    ]
    if ship_run is not None:
        next_commands.append(f"ta ship run {project.id} {ship_run.id}")
    elif candidate is not None:
        next_commands.append(f"ta ship candidate {project.id} {candidate.id}")

    return {
        "project": project_to_json(project),
        "ticket": ticket_to_json(ticket),
        "jobs": [_job_to_json(job) for job in jobs],
        "attempts": [_attempt_to_json(attempt) for attempt in attempts],
        "accepted_attempt": _attempt_to_json(accepted_attempt) if accepted_attempt else None,
        "promotion_candidate": promotion_candidate_to_json(candidate, include_attempts=True) if candidate else None,
        "ship_run": ship_run_to_json(ship_run) if ship_run else None,
        "evidence_summary": evidence_summary,
        "timeline": timeline,
        "next_commands": next_commands,
    }


def _candidate_for_ticket(project_id, ticket_id, attempts: list[TicketAttempt], accepted_attempt: TicketAttempt | None):
    attempt_ids = {str(attempt.id) for attempt in attempts}
    if accepted_attempt is not None:
        attempt_ids.add(str(accepted_attempt.id))
    candidates = (
        PromotionCandidate.query
        .filter_by(project_id=project_id)
        .order_by(PromotionCandidate.created_at.asc())
        .all()
    )
    for candidate in candidates:
        selected_ids = {str(value) for value in (candidate.selected_attempt_ids or [])}
        if not selected_ids:
            continue
        if any(attempt_id in selected_ids for attempt_id in attempt_ids):
            return candidate
    return None


def _ship_run_for_candidate(project_id, candidate: PromotionCandidate | None):
    if candidate is None:
        return None
    return (
        ShipRun.query
        .filter_by(project_id=project_id, promotion_candidate_id=candidate.id)
        .order_by(ShipRun.created_at.asc())
        .first()
    )


def _evidence_summary(project_id, attempts: list[TicketAttempt], ship_run: ShipRun | None) -> dict:
    attempt_ids = {str(attempt.id) for attempt in attempts}
    ship_run_ids = {str(ship_run.id)} if ship_run is not None else set()
    bundles = (
        EvidenceBundle.query
        .filter_by(project_id=project_id)
        .order_by(EvidenceBundle.created_at.asc())
        .all()
    )
    relevant_bundles = [
        bundle for bundle in bundles
        if (bundle.target_type == "attempt" and str(bundle.target_id) in attempt_ids)
        or (bundle.target_type == "ship_run" and str(bundle.target_id) in ship_run_ids)
    ]
    bundle_ids = {bundle.id for bundle in relevant_bundles}
    runs = (
        EvidenceRun.query
        .filter_by(project_id=project_id)
        .order_by(EvidenceRun.created_at.asc())
        .all()
    )
    relevant_runs = [
        run for run in runs
        if (run.evidence_bundle_id in bundle_ids)
        or (run.target_type == "attempt" and str(run.target_id) in attempt_ids)
        or (run.target_type == "ship_run" and str(run.target_id) in ship_run_ids)
    ]
    check_counts = Counter()
    for bundle in relevant_bundles:
        for check in bundle.checks or []:
            check_counts[check.status] += 1
    return {
        "canonical_source": ["EvidenceBundle", "EvidenceRun", "EvidenceCheck"],
        "bundle_count": len(relevant_bundles),
        "run_count": len(relevant_runs),
        "check_counts": dict(check_counts),
        "bundles": [evidence_bundle_to_json(bundle, include_checks=True) for bundle in relevant_bundles],
        "runs": [evidence_run_to_json(run) for run in relevant_runs],
    }


def _timeline_item(kind: str, entity_id, *, label: str, status: str | None = None, created_at=None, **extra) -> dict:
    item = {
        "kind": kind,
        "id": str(entity_id),
        "label": label,
        "status": status,
        "created_at": created_at.isoformat() if created_at else None,
    }
    item.update({key: value for key, value in extra.items() if value is not None})
    return item


def _attempt_to_json(attempt: TicketAttempt | None) -> dict | None:
    if attempt is None:
        return None
    return {
        "id": str(attempt.id),
        "ticket_id": str(attempt.ticket_id),
        "status": attempt.status,
        "attempt_num": attempt.attempt_num,
        "agenthub_commit_hash": attempt.agenthub_commit_hash,
        "base_hash": attempt.base_hash,
        "summary": attempt.summary,
        "test_status": attempt.test_status,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }


def _job_to_json(job: AgentJob) -> dict:
    return {
        "id": str(job.id),
        "ticket_id": str(job.ticket_id),
        "status": job.status,
        "kind": job.kind,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
