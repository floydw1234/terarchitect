"""Project domain helpers."""
import re
from typing import Optional

from flask import current_app

from models.db import Project

_FRONTIER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{6,254}$")
_SOURCE_TYPES = {"github", "local_path", "agenthub_leaf"}


def normalize_frontier_id(value) -> str | None:
    if value is None:
        return None
    frontier_id = str(value).strip()
    return frontier_id or None


def get_project_frontier_id(project: Project) -> str | None:
    return normalize_frontier_id(getattr(project, "accepted_frontier_id", None))


def normalize_project_source_type(value) -> str | None:
    if value is None:
        return None
    source_type = str(value).strip().lower()
    return source_type or None


def infer_project_source_type(
    *,
    explicit_source_type,
    github_url,
    project_path,
    accepted_frontier_id,
) -> tuple[str | None, str | None]:
    source_type = normalize_project_source_type(explicit_source_type)
    github_url_value = (github_url or "").strip()
    project_path_value = (project_path or "").strip()
    frontier_id = normalize_frontier_id(accepted_frontier_id)

    if source_type is None:
        if github_url_value:
            source_type = "github"
        elif frontier_id is not None and not project_path_value:
            source_type = "agenthub_leaf"
        else:
            source_type = "local_path"

    if source_type not in _SOURCE_TYPES:
        return None, "source_type must be one of: github, local_path, agenthub_leaf"
    if source_type == "github" and not github_url_value:
        return None, "github_url is required when source_type=github"
    if source_type == "local_path" and not project_path_value and github_url_value:
        return None, "source_type=local_path is incompatible with github_url"
    if source_type == "agenthub_leaf" and project_path_value:
        return None, "source_type=agenthub_leaf is incompatible with project_path"
    return source_type, None


def normalize_github_ref(value) -> str | None:
    if value is None:
        return None
    github_ref = str(value).strip()
    return github_ref or None


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
        "source_type": getattr(project, "source_type", None) or "local_path",
        "github_url": project.github_url,
        "github_ref": getattr(project, "github_ref", None),
        "github_resolved_sha": getattr(project, "github_resolved_sha", None),
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
