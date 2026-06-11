"""Project domain helpers."""
import re
from typing import Optional

from flask import current_app

from models.db import Project

_FRONTIER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{6,254}$")


def normalize_frontier_id(value) -> str | None:
    if value is None:
        return None
    frontier_id = str(value).strip()
    return frontier_id or None


def get_project_frontier_id(project: Project) -> str | None:
    return normalize_frontier_id(getattr(project, "accepted_frontier_id", None))


def project_has_frontier(project: Project) -> bool:
    return get_project_frontier_id(project) is not None


def compare_base_to_accepted_frontier(
    base_value,
    accepted_frontier_id,
    *,
    subject_name: str,
    base_field_name: str,
) -> tuple[Optional[bool], Optional[str]]:
    normalized_base = normalize_frontier_id(base_value)
    normalized_frontier = normalize_frontier_id(accepted_frontier_id)
    missing = []
    if normalized_base is None:
        missing.append(f"{base_field_name} is not set")
    if normalized_frontier is None:
        missing.append("project.accepted_frontier_id is not set")
    if missing:
        return None, f"Cannot determine {subject_name} staleness: {' and '.join(missing)}."
    if normalized_base != normalized_frontier:
        return True, f"{base_field_name} differs from project.accepted_frontier_id."
    return False, None


def validate_project_frontier_candidate(project: Project | None, frontier_id) -> tuple[bool, str | None]:
    normalized = normalize_frontier_id(frontier_id)
    if normalized is None:
        return False, "accepted_frontier_id is required"
    if not _FRONTIER_ID_RE.fullmatch(normalized):
        return False, "accepted_frontier_id must look like an AgentHub frontier id or hash"
    git_mode = (getattr(project, "git_mode", None) or "swarm").strip().lower() if project else "swarm"
    if git_mode != "swarm":
        return False, "accepted_frontier_id is only valid for swarm projects"
    return True, None


def project_to_json(project: Project):
    frontier = getattr(project, "shipped_frontier", None) or None
    frontier_updated = getattr(project, "shipped_frontier_updated_at", None)
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "github_url": project.github_url,
        "execution_mode": getattr(project, "execution_mode", None) or "docker",
        "git_mode": getattr(project, "git_mode", None) or "swarm",
        "project_path": project.project_path,
        "accepted_frontier_id": get_project_frontier_id(project),
        "shipped_frontier": frontier,
        "shipped_frontier_updated_at": frontier_updated.isoformat() if frontier_updated else None,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def bootstrap_project_memory(project: Project) -> None:
    """Index one initial doc into project memory so retrieve has something to return. No-op if memory unavailable."""
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return
    doc = f"Project: {project.name or 'Untitled'}."
    if project.description:
        doc += f" {project.description}"
    else:
        doc += " No description."
    try:
        from utils.memory import index as memory_index_fn, get_hipporag_kwargs
        memory_index_fn(project.id, [doc], base_save_dir, **get_hipporag_kwargs())
        current_app.logger.info("Bootstrap project memory indexed for project %s", project.id)
    except Exception as e:
        current_app.logger.warning("Bootstrap project memory failed for %s: %s", project.id, e)
