"""Shared Ship Room candidate helpers for CLI commands."""

from __future__ import annotations

from cli._api import API


def attempt_is_integrated_like(attempt: dict) -> bool:
    if attempt.get("integrated") or attempt.get("accepted"):
        return True
    status = (attempt.get("status") or "").strip().lower()
    return status in {
        "accepted",
        "composed",
        "integrated",
        "release_pr_open",
        "shipped",
    }


def attempt_candidate_eligible(attempt: dict, *, shipped_frontier: str | None = None) -> bool:
    """True when an integrated attempt is aligned to the project's shipped frontier."""
    frontier = (shipped_frontier or attempt.get("shipped_frontier") or "").strip() or None
    base_hash = (attempt.get("base_hash") or "").strip() or None
    return bool(attempt_is_integrated_like(attempt) and frontier and base_hash == frontier)


def find_candidate_id_for_attempt(api: API, project_id: str, attempt_id: str) -> str | None:
    """Return a promotion candidate id that already includes this attempt, if any."""
    candidates = api.get(f"/api/projects/{project_id}/ship/candidates")
    normalized_attempt = str(attempt_id)
    for candidate in candidates:
        selected_ids = candidate.get("selected_attempt_ids") or []
        if any(str(selected) == normalized_attempt for selected in selected_ids):
            return str(candidate["id"])
    return None


def create_candidate_from_attempt(
    api: API,
    project_id: str,
    attempt_id: str,
) -> dict:
    """Create or idempotently reuse a promotion candidate for one attempt."""
    return api.post(
        f"/api/projects/{project_id}/ship/candidates",
        {"selected_attempt_ids": [str(attempt_id)]},
    )


def build_promotion_next_commands(
    project_id: str,
    attempt_id: str,
    *,
    ticket_id: str | None = None,
    candidate_id: str | None = None,
    candidate_eligible: bool = False,
) -> list[str]:
    """Operator next steps after accept or when inspecting a candidate-eligible attempt."""
    commands = [f"ta attempt show {project_id} {attempt_id}"]
    if not candidate_eligible:
        commands.append(f"ta ship candidates {project_id}")
        return commands

    create_cmd = f"ta ship create-candidate {project_id} --attempt {attempt_id}"
    if ticket_id:
        create_cmd += f" --ticket {ticket_id}"
    commands.append(create_cmd)

    if candidate_id:
        commands.extend([
            f"ta ship dry-compose {project_id} {candidate_id}",
            f"ta ship compose-candidate {project_id} {candidate_id}",
        ])
    else:
        commands.append(f"ta ship candidates {project_id}")

    return commands


def create_candidate_next_commands(project_id: str, candidate_id: str) -> list[str]:
    return [
        f"ta ship candidate {project_id} {candidate_id}",
        f"ta ship dry-compose {project_id} {candidate_id}",
        f"ta ship compose-candidate {project_id} {candidate_id}",
    ]
