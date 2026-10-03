"""Server-side promotion spine (choose → accept → candidate → compose → ship)."""

from __future__ import annotations

import os
from typing import Any

from flask import current_app

from models.db import db, Project, ShipRun, Ticket, TicketAttempt
from .project_service import get_project_ship_target as _get_project_ship_target


class PromotionSpineError(Exception):
    def __init__(self, message: str, *, status_code: int = 409, detail: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _test_client():
    return current_app.test_client()


def _json_response(response) -> tuple[dict | list | None, int]:
    status = response.status_code
    try:
        payload = response.get_json()
    except Exception:
        payload = None
    return payload, status


def run_promotion_spine_for_attempt(
    project: Project,
    ticket: Ticket,
    attempt: TicketAttempt,
    *,
    sync_compose: bool = True,
    ship_after_compose: bool = True,
    merge_method: str = "merge",
) -> dict[str, Any]:
    """Run the operator ship loop via in-process API calls."""
    project_id = str(project.id)
    ticket_id = str(ticket.id)
    attempt_id = str(attempt.id)
    client = _test_client()

    choose_resp, choose_status = _json_response(
        client.post(
            f"/api/projects/{project_id}/tickets/{ticket_id}/attempts/{attempt_id}/choose-winner",
            json={},
        )
    )
    if choose_status >= 400:
        raise PromotionSpineError(
            (choose_resp or {}).get("error") or "choose-winner failed",
            status_code=choose_status,
        )

    accept_resp, accept_status = _json_response(
        client.post(
            f"/api/projects/{project_id}/tickets/{ticket_id}/attempts/{attempt_id}/accept",
            json={},
        )
    )
    if accept_status >= 400:
        raise PromotionSpineError(
            (accept_resp or {}).get("error") or "accept-winner failed",
            status_code=accept_status,
        )

    cand_resp, cand_status = _json_response(
        client.post(
            f"/api/projects/{project_id}/ship/candidates",
            json={"selected_attempt_ids": [attempt_id]},
        )
    )
    if cand_status >= 400:
        raise PromotionSpineError(
            (cand_resp or {}).get("error") or "create-candidate failed",
            status_code=cand_status,
        )
    candidate_id = str((cand_resp or {}).get("id") or "")
    if not candidate_id:
        raise PromotionSpineError("create-candidate returned no id", status_code=500)

    compose_resp, compose_status = _json_response(
        client.post(
            f"/api/projects/{project_id}/ship/candidates/{candidate_id}/compose",
            json={},
        )
    )
    if compose_status >= 400:
        raise PromotionSpineError(
            (compose_resp or {}).get("error") or "compose-candidate failed",
            status_code=compose_status,
        )
    run_id = str((compose_resp or {}).get("id") or "")
    if not run_id:
        raise PromotionSpineError("compose-candidate returned no ShipRun id", status_code=500)

    if sync_compose and (compose_resp or {}).get("status") in ("queued", "composing", "running"):
        _run_local_shipper_for_run(run_id)

    run_detail, run_status = _json_response(
        client.get(f"/api/projects/{project_id}/ship/runs/{run_id}")
    )
    if run_status >= 400:
        raise PromotionSpineError("could not load ShipRun after compose", status_code=run_status)

    ship_target = _get_project_ship_target(project)
    if ship_target == "github":
        return {
            "project_id": project_id,
            "ticket_id": ticket_id,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "ship_run_id": run_id,
            "status": (run_detail or {}).get("status"),
            "shipped": False,
            "release_pr_number": (run_detail or {}).get("release_pr_number"),
            "release_pr_url": (run_detail or {}).get("release_pr_url"),
            "composed_commit_hash": (run_detail or {}).get("composed_commit_hash"),
            "steps": {
                "choose_winner": choose_resp,
                "accept_winner": accept_resp,
                "create_candidate": cand_resp,
                "compose_candidate": run_detail,
            },
        }

    if not ship_after_compose:
        return {
            "project_id": project_id,
            "ticket_id": ticket_id,
            "attempt_id": attempt_id,
            "candidate_id": candidate_id,
            "ship_run_id": run_id,
            "status": (run_detail or {}).get("status"),
            "shipped": False,
            "steps": {
                "choose_winner": choose_resp,
                "accept_winner": accept_resp,
                "create_candidate": cand_resp,
                "compose_candidate": run_detail,
            },
        }

    if (run_detail or {}).get("status") != "ready_to_ship":
        raise PromotionSpineError(
            f"ShipRun {run_id} is {(run_detail or {}).get('status')!r}; expected ready_to_ship",
            status_code=409,
            detail=(run_detail or {}).get("error"),
        )

    ship_resp, ship_code = _json_response(
        client.post(
            f"/api/projects/{project_id}/ship/runs/{run_id}/ship",
            json={"merge_method": merge_method},
        )
    )
    if ship_code >= 400:
        raise PromotionSpineError(
            (ship_resp or {}).get("error") or "ship-run failed",
            status_code=ship_code,
            detail=(ship_resp or {}).get("detail"),
        )

    db.session.refresh(project)
    return {
        "project_id": project_id,
        "ticket_id": ticket_id,
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "ship_run_id": run_id,
        "status": (ship_resp or {}).get("status"),
        "shipped": (ship_resp or {}).get("status") == "shipped",
        "shipped_commit_hash": (ship_resp or {}).get("shipped_commit_hash"),
        "shipped_frontier": getattr(project, "shipped_frontier", None),
        "steps": {
            "choose_winner": choose_resp,
            "accept_winner": accept_resp,
            "create_candidate": cand_resp,
            "compose_candidate": run_detail,
            "ship_run": ship_resp,
        },
    }


def _record_ship_run_auto_ship_failure(run_id: str, message: str, *, compose_failed: bool = False) -> None:
    """Persist a loud auto-ship failure on the ShipRun when compose cannot proceed in-backend."""
    run = db.session.get(ShipRun, run_id)
    if run is None:
        return
    if run.status in ("shipped", "shipping"):
        return
    run.error = (message or "")[:4000] or None
    if compose_failed and run.status in ("queued", "composing", "running"):
        run.status = "compose_failed"
    elif run.status in ("queued", "composing", "running", "ready_to_ship"):
        run.status = "failed"
    db.session.commit()


def _run_local_shipper_for_run(run_id: str) -> None:
    from utils.auto_ship_runtime import format_auto_ship_runtime_error

    preflight = format_auto_ship_runtime_error()
    if preflight:
        _record_ship_run_auto_ship_failure(run_id, preflight, compose_failed=True)
        raise PromotionSpineError(preflight, status_code=503)

    api_url = (os.environ.get("TERARCHITECT_API_URL") or "http://127.0.0.1:5010").rstrip("/")
    try:
        from cli._shipper import run_local_shipper
    except ImportError as exc:
        detail = f"Local shipper is unavailable (cli._shipper import failed): {exc}"
        current_app.logger.error("auto_ship local shipper import failed run=%s: %s", run_id, exc)
        _record_ship_run_auto_ship_failure(run_id, detail, compose_failed=True)
        raise PromotionSpineError(detail, status_code=503)
    rc = run_local_shipper(api_url, run_id, capture_stdout=True)
    if rc != 0:
        detail = f"Local shipper exited {rc}"
        _record_ship_run_auto_ship_failure(run_id, detail, compose_failed=True)
        raise PromotionSpineError(detail, status_code=500)


def ship_ready_to_ship_run(project: Project, run_id: str, *, merge_method: str = "merge") -> dict[str, Any]:
    """POST ship for a run already in ready_to_ship (used by auto-ship finalize)."""
    project_id = str(project.id)
    client = _test_client()
    ship_resp, ship_code = _json_response(
        client.post(
            f"/api/projects/{project_id}/ship/runs/{run_id}/ship",
            json={"merge_method": merge_method},
        )
    )
    if ship_code >= 400:
        raise PromotionSpineError(
            (ship_resp or {}).get("error") or "ship-run failed",
            status_code=ship_code,
            detail=(ship_resp or {}).get("detail"),
        )
    db.session.refresh(project)
    return ship_resp or {}


def pick_auto_winner_attempt(project: Project, ticket: Ticket) -> TicketAttempt | None:
    from .auto_ship_judge_service import pick_auto_winner_with_decision

    attempt, _decision = pick_auto_winner_with_decision(project, ticket)
    return attempt


def ticket_execution_batch_settled(ticket_id: str) -> bool:
    """True when no agent jobs are still pending/running for this ticket."""
    from models.db import AgentJob

    jobs = AgentJob.query.filter_by(ticket_id=ticket_id).all()
    if not jobs:
        return True
    return not any(job.status in ("pending", "running") for job in jobs)
