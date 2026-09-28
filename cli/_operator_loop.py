"""Decomposed operator ship loop for spark headless dogfood (explicit CLI subcommand spine)."""

from __future__ import annotations

from cli._api import API, APIError
from cli._ship_candidate import create_candidate_from_attempt, create_candidate_next_commands
from cli.commands import ticket as ticket_cmd


def _ensure_evaluated_attempt(payload: dict, attempt_id: str) -> dict:
    attempts = payload.get("attempts") or []
    if not attempts:
        raise APIError(
            404,
            f"No attempts matched ticket evaluation for attempt {attempt_id}.",
            next_commands=[
                f"ta ticket attempts {payload.get('project_id')} {payload.get('ticket_id')}",
            ],
        )
    match = next((item for item in attempts if item.get("attempt_id") == attempt_id), None)
    if match is None:
        raise APIError(
            404,
            f"Attempt {attempt_id} was not included in evaluate-attempts output.",
            next_commands=[
                f"ta ticket evaluate-attempts {payload.get('project_id')} {payload.get('ticket_id')} --attempt {attempt_id}",
            ],
        )
    if not match.get("validated") and (match.get("status") or "").lower() != "validated":
        raise APIError(
            409,
            f"Attempt {attempt_id} is not validated and cannot enter the operator ship loop.",
            next_commands=match.get("review_commands") or [],
        )
    if match.get("stale"):
        raise APIError(
            409,
            f"Attempt {attempt_id} is stale and cannot be chosen as the winner.",
            detail=match.get("stale_reason"),
            next_commands=[
                f"ta ticket evaluate-attempts {payload.get('project_id')} {payload.get('ticket_id')} --attempt {attempt_id}",
            ],
        )
    return match


def run_operator_ship_loop(
    api: API,
    *,
    project_id: str,
    ticket_id: str,
    attempt_id: str,
    merge_method: str = "merge",
    sync_compose: bool = True,
    expect_frontier: str | None = None,
    include_diff: bool = False,
    include_files: bool = False,
    max_diff_bytes: int = 65536,
) -> dict:
    """Run evaluate → choose → accept → create-candidate → compose (--sync) → ship-run via API."""
    project = ticket_cmd._get_project(api, project_id, output="json")
    shipped_frontier_before = ticket_cmd._get_shipped_frontier(project)
    if expect_frontier and shipped_frontier_before and expect_frontier != shipped_frontier_before:
        raise APIError(
            409,
            f"Expected frontier {expect_frontier}, but project.shipped_frontier is now {shipped_frontier_before or 'unset'}.",
            next_commands=[
                f"ta ticket evaluate-attempts {project_id} {ticket_id} --attempt {attempt_id}",
            ],
        )

    evaluate = ticket_cmd.build_evaluate_attempts_payload(
        api,
        project_id=project_id,
        ticket_id=ticket_id,
        attempt_ids=[attempt_id],
        latest=None,
        include_diff=include_diff,
        include_files=include_files,
        max_diff_bytes=max_diff_bytes,
    )
    evaluate["shipped_frontier"] = evaluate.get("frontier_id")
    _ensure_evaluated_attempt(evaluate, attempt_id)

    choose_body = {}
    choose = api.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/attempts/{attempt_id}/choose-winner",
        choose_body,
    )

    accept = api.post(
        f"/api/projects/{project_id}/tickets/{ticket_id}/attempts/{attempt_id}/accept",
        {},
    )

    candidate = create_candidate_from_attempt(api, project_id, attempt_id)
    candidate_id = str(candidate.get("id") or "")
    if not candidate_id:
        raise APIError(500, "create-candidate returned no candidate id.")

    compose_run = api.post(
        f"/api/projects/{project_id}/ship/candidates/{candidate_id}/compose",
        {},
    )
    run_id = str(compose_run.get("id") or "")
    if not run_id:
        raise APIError(500, "compose-candidate returned no ShipRun id.")
    if sync_compose:
        from cli.commands.ship import _run_status_needs_local_compose, _sync_compose_ship_run

        if _run_status_needs_local_compose(compose_run.get("status")):
            compose_run = _sync_compose_ship_run(api, project_id, run_id, output="json")
    if (compose_run.get("status") or "") != "ready_to_ship":
        raise APIError(
            409,
            f"ShipRun {run_id} is {compose_run.get('status')!r}; expected ready_to_ship before ship-run.",
            detail=(compose_run.get("error") or "")[:500] or None,
            next_commands=[
                f"ta ship run {project_id} {run_id}",
                f"ta ship compose-run {project_id} {run_id}",
                f"ta ship doctor {project_id}",
            ],
        )

    ship_run = api.post(
        f"/api/projects/{project_id}/ship/runs/{run_id}/ship",
        {"merge_method": merge_method},
    )

    project_after = ticket_cmd._get_project(api, project_id, output="json")
    shipped_frontier_after = (
        ticket_cmd._get_shipped_frontier(project_after)
        or ship_run.get("shipped_commit_hash")
    )

    next_commands = [
        f"ta project show {project_id}",
        f"ta ticket attempts {project_id} {ticket_id}",
        f"ta ship run {project_id} {run_id}",
    ]

    return {
        "project_id": project_id,
        "ticket_id": ticket_id,
        "attempt_id": attempt_id,
        "candidate_id": candidate_id,
        "ship_run_id": run_id,
        "shipped_frontier_before": shipped_frontier_before,
        "shipped_frontier_after": shipped_frontier_after,
        "shipped_frontier": shipped_frontier_after,
        "status": ship_run.get("status"),
        "shipped_commit_hash": ship_run.get("shipped_commit_hash"),
        "sequence": [
            "evaluate-attempts",
            "choose-winner",
            "accept-winner",
            "create-candidate",
            "compose-candidate",
            "ship-run",
        ],
        "steps": {
            "evaluate_attempts": evaluate,
            "choose_winner": choose,
            "accept_winner": accept,
            "create_candidate": {
                **candidate,
                "candidate_id": candidate_id,
                "next_commands": create_candidate_next_commands(project_id, candidate_id),
            },
            "compose_candidate": compose_run,
            "ship_run": ship_run,
        },
        "next_commands": next_commands,
    }
