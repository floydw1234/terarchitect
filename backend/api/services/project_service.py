"""Project domain helpers."""
from flask import current_app

from models.db import Project


def project_to_json(project: Project):
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "github_url": project.github_url,
        "execution_mode": getattr(project, "execution_mode", None) or "docker",
        "git_mode": getattr(project, "git_mode", None) or "structured",
        "project_path": project.project_path,
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
