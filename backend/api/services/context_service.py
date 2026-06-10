"""Ticket agent/operator context projection service."""
import re

from models.db import AgentJob, ExecutionLog, Ticket, TicketAttempt

from .channel_service import parse_event_post, project_channel, ticket_channel, wave_channel
from .job_service import job_to_response
from .ledger_service import _candidate_for_ticket, _ship_run_for_candidate
from .merge_service import promotion_candidate_to_json, ship_run_to_json
from .project_service import project_to_json
from .ticket_service import ticket_to_json

_PATH_RE = re.compile(r"(/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+)")


def build_ticket_context(
    project,
    ticket: Ticket,
    *,
    agent: bool = False,
    fetch_posts=None,
) -> dict:
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project.id, ticket_id=ticket.id)
        .order_by(TicketAttempt.attempt_num.desc(), TicketAttempt.created_at.desc())
        .all()
    )
    jobs = (
        AgentJob.query
        .filter_by(project_id=project.id, ticket_id=ticket.id)
        .order_by(AgentJob.created_at.desc())
        .all()
    )
    latest_job = jobs[0] if jobs else None
    candidate = _candidate_for_ticket(project.id, ticket.id, attempts, attempts[0] if attempts else None)
    ship_run = _ship_run_for_candidate(project.id, candidate)

    channel_names = {
        "project": project_channel(str(project.id)),
        "ticket": ticket_channel(str(ticket.id)),
    }
    if attempts:
        channel_names["wave"] = wave_channel(project.name, attempts[0].wave_num)

    recent_events = []
    if fetch_posts is not None:
        recent_events = _collect_recent_events(channel_names, fetch_posts)

    paths = _path_hints(project, ticket)
    payload = {
        "project": project_to_json(project),
        "ticket": ticket_to_json(ticket),
        "attempts": [_attempt_to_json(attempt) for attempt in attempts],
        "jobs": [job_to_response(job) for job in jobs],
        "candidate": promotion_candidate_to_json(candidate, include_attempts=True) if candidate else None,
        "ship_run": ship_run_to_json(ship_run) if ship_run else None,
        "channels": channel_names,
        "recent_events": recent_events,
        "paths": paths,
        "next_commands": [
            f"ta status {project.id} --ticket {ticket.id}",
            f"ta ticket logs {project.id} {ticket.id} --raw",
        ],
    }
    if latest_job is not None:
        payload["latest_job"] = job_to_response(latest_job)
    if agent:
        from worker_context import build_worker_context

        payload["worker_context"] = build_worker_context(ticket)
    return payload


def _collect_recent_events(channel_names: dict[str, str], fetch_posts) -> list[dict]:
    events = []
    seen = set()
    for channel_type, channel_name in channel_names.items():
        for post in fetch_posts(channel_name, limit=20) or []:
            normalized = parse_event_post(post)
            key = (
                normalized.get("id"),
                normalized.get("created_at"),
                normalized.get("raw_content") or normalized.get("content"),
            )
            if key in seen:
                continue
            seen.add(key)
            normalized["_channel"] = channel_name
            normalized["_channel_type"] = channel_type
            events.append(normalized)
    events.sort(key=lambda event: event.get("created_at") or "")
    return events[-20:]


def _path_hints(project, ticket: Ticket) -> dict:
    logs = (
        ExecutionLog.query
        .filter_by(project_id=project.id, ticket_id=ticket.id)
        .order_by(ExecutionLog.created_at.desc())
        .all()
    )
    runner_workdir = None
    recovery_artifacts: list[str] = []
    for log in logs:
        text = "\n".join(filter(None, [log.summary, log.raw_output]))
        paths = _PATH_RE.findall(text)
        for path in paths:
            if "terarchitect_runner_" in path and runner_workdir is None:
                runner_workdir = path.split("/plan/", 1)[0].split("/logs/", 1)[0]
            if any(marker in path for marker in ("/plan/", "/.terarchitect/", "recovery", ".md", ".log")):
                if path not in recovery_artifacts:
                    recovery_artifacts.append(path)
    return {
        "project_path": getattr(project, "project_path", None),
        "runner_workdir_hint": runner_workdir,
        "recovery_artifact_hints": recovery_artifacts,
    }


def _attempt_to_json(attempt: TicketAttempt) -> dict:
    return {
        "id": str(attempt.id),
        "ticket_id": str(attempt.ticket_id),
        "status": attempt.status,
        "attempt_num": attempt.attempt_num,
        "agenthub_commit_hash": attempt.agenthub_commit_hash,
        "base_hash": attempt.base_hash,
        "summary": attempt.summary,
        "created_at": attempt.created_at.isoformat() if attempt.created_at else None,
        "updated_at": attempt.updated_at.isoformat() if attempt.updated_at else None,
    }
