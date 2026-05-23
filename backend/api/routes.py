"""
API Routes for Terarchitect
"""
import json
import os
import subprocess
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, ExecutionLog, AgentJob, ShipRun, TicketAttempt, CompositeWorkspace
from utils.embedding_client import embed_single
from utils.rag import upsert_embedding, delete_embeddings_for_source
from utils.app_settings import (
    get_value,
    check_execution_readiness,
)

from .services.graph_service import (
    generate_architecture_graph as _generate_architecture_graph,
    update_graph_embeddings as _update_graph_embeddings,
)
from .services.job_service import (
    claim_swarm_job as _claim_swarm_job,
    job_to_response as _job_to_response,
)
from .services.merge_service import (
    compute_waves as _compute_waves,
    maybe_trigger_wave_merge as _maybe_trigger_wave_merge,
    ship_run_to_json as _ship_run_to_json,
)
from .services.notes_service import (
    join_note_link_ids as _join_note_link_ids,
    note_to_json as _note_to_json,
    split_note_link_ids as _split_note_link_ids,
)
from .services.github_service import (
    env_for_gh_user as _env_for_gh_user,
    repo_slug_from_github_url as _repo_slug_from_github_url,
)
from .services.project_service import (
    bootstrap_project_memory as _bootstrap_project_memory,
    project_to_json as _project_to_json,
)
from .services.ticket_service import (
    dispatch_unblocked_queued as _dispatch_unblocked_queued,
    enqueue_ticket_job as _enqueue_ticket_job,
    ticket_to_json as _ticket_to_json,
)
from .services.attempt_service import (
    SATISFIED_STATUSES as _SATISFIED_STATUSES,
    attempt_to_json as _attempt_to_json,
    create_attempt as _create_attempt,
    get_accepted_attempt as _get_accepted_attempt,
    get_latest_attempt as _get_latest_attempt,
    transition_attempt as _transition_attempt,
    validate_attempt as _validate_attempt,
)
from .services.channel_service import (
    ticket_channel as _ticket_channel,
    wave_channel as _wave_channel,
    post_event as _post_event,
    fetch_channel_posts as _fetch_channel_posts,
)
from .services.workspace_service import (
    workspace_to_json as _workspace_to_json,
    analyze_compatibility as _analyze_compatibility,
)

api_bp = Blueprint("api", __name__)

# Worker-facing route prefixes are already protected by _require_worker_auth (Bearer TERARCHITECT_WORKER_API_KEY).
# All other routes are protected by _require_ui_auth (Bearer TERARCHITECT_UI_API_KEY) when that key is set.
_WORKER_ROUTE_PREFIXES = ("/worker/", "/rag/")


@api_bp.before_request
def _ui_auth_check():
    """Gate all non-worker UI routes when TERARCHITECT_UI_API_KEY is set."""
    path = request.path  # e.g. /api/projects/...
    # Strip the blueprint prefix (/api) for comparison
    suffix = path[4:] if path.startswith("/api") else path
    if any(suffix.startswith(p) for p in _WORKER_ROUTE_PREFIXES):
        return  # Worker routes handle their own auth
    # Also pass through requests carrying a valid worker token.
    # Some worker-facing endpoints live under /projects/... (complete, logs, cancel-requested,
    # worker-context) and are not covered by the prefix check above.
    worker_token = (get_value("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if worker_token:
        auth_header = request.headers.get("Authorization") or ""
        if auth_header.startswith("Bearer ") and auth_header[7:].strip() == worker_token:
            return  # Valid worker token — skip UI auth
    err, status = _require_ui_auth()
    if err is not None:
        return err, status

# Worker-facing API: auth via Bearer token. Set TERARCHITECT_WORKER_API_KEY in the backend env to require auth; if unset, no auth (dev).
def _require_worker_auth():
    """Return (None, None) if authorized, else (response, status_code) to return."""
    token = (get_value("TERARCHITECT_WORKER_API_KEY") or "").strip()
    if not token:
        return None, None  # No key configured: allow (dev)
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Missing or invalid Authorization header (expected Bearer <token>)"}), 401
    if auth[7:].strip() != token:
        return jsonify({"error": "Invalid worker API token"}), 401
    return None, None


# UI-facing API: optional auth via Bearer token. Set TERARCHITECT_UI_API_KEY (env) to require auth; if unset, no auth (local dev).
def _require_ui_auth():
    """Return (None, None) if authorized, else (response, status_code) to return.
    Keyed off TERARCHITECT_UI_API_KEY env var only (not DB settings, to avoid a bootstrap chicken-and-egg problem).
    When the key is not set the check is skipped, preserving the local-dev experience."""
    token = (os.environ.get("TERARCHITECT_UI_API_KEY") or "").strip()
    if not token:
        return None, None
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return jsonify({"error": "Missing or invalid Authorization header"}), 401
    if auth[7:].strip() != token:
        return jsonify({"error": "Invalid UI API token"}), 401
    return None, None


# Cancel requested by ticket_id is now stored in agent_jobs.cancel_requested column (DB-backed, process-safe).


def _read_local_git_tip(path: str) -> str | None:
    """Return the current HEAD commit hash for the main/master branch at path. Returns None on any error."""
    if not path or not os.path.isdir(path):
        return None
    for branch in ("main", "master"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", branch],
                cwd=path, capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                h = r.stdout.strip()
                if h and len(h) >= 7:
                    return h
        except Exception:
            pass
    return None


def _fetch_github_default_branch_tip(github_url: str) -> str | None:
    """Fetch the default branch (main/master) tip SHA from GitHub via gh CLI.
    Returns None if gh is unavailable, not authenticated, or URL is not parseable."""
    slug = _repo_slug_from_github_url(github_url)
    if not slug:
        return None
    for branch in ("main", "master"):
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{slug}/git/refs/heads/{branch}"],
                capture_output=True, text=True, timeout=10, env=_env_for_gh_user(),
            )
            if r.returncode == 0:
                data = json.loads(r.stdout or "{}")
                sha = (data.get("object") or {}).get("sha") or None
                if sha:
                    return sha
        except Exception:
            pass
    return None


def _apply_root_refresh(project, new_hash: str, source: str = "wave_merge") -> None:
    """Update shipped_frontier and re-dispatch any newly unblocked queued tickets."""
    from datetime import datetime, timezone
    project.shipped_frontier = new_hash
    project.shipped_frontier_updated_at = datetime.now(timezone.utc)
    db.session.commit()
    current_app.logger.info(
        "Root refresh project=%s frontier=%s source=%s", project.id, new_hash[:12], source
    )
    try:
        _dispatch_unblocked_queued(project.id)
    except Exception as exc:
        current_app.logger.warning("Root refresh dispatch failed: %s", exc)


@api_bp.route("/projects", methods=["GET", "POST"])
def projects():
    """List all projects or create a new one."""
    if request.method == "GET":
        projects = Project.query.all()
        return jsonify([_project_to_json(p) for p in projects])

    if request.method == "POST":
        data = request.json or {}
        if not data.get("name"):
            return jsonify({"error": "name is required"}), 400
        project_path_val = (data.get("project_path") or "").strip() or None
        project = Project(
            name=data.get("name"),
            description=data.get("description"),
            github_url=data.get("github_url"),
            execution_mode="local" if (data.get("execution_mode") or "").strip().lower() == "local" else "docker",
            git_mode="swarm",
            project_path=project_path_val,
        )
        # Seed shipped_frontier: try local git first, then GitHub URL
        frontier_tip = None
        if project_path_val:
            frontier_tip = _read_local_git_tip(project_path_val)
        if not frontier_tip and data.get("github_url"):
            frontier_tip = _fetch_github_default_branch_tip(data["github_url"])
        if frontier_tip:
            from datetime import datetime, timezone
            project.shipped_frontier = frontier_tip
            project.shipped_frontier_updated_at = datetime.now(timezone.utc)
        db.session.add(project)
        db.session.flush()  # assigns project.id from the DB before it's used below
        graph = Graph(project_id=project.id)
        default_columns = [
            {"id": "backlog", "title": "Backlog", "order": 0},
            {"id": "queued", "title": "Queued", "order": 1},
            {"id": "in_progress", "title": "In Progress", "order": 2},
            {"id": "done", "title": "Done", "order": 3},
        ]
        kanban_board = KanbanBoard(project_id=project.id, columns=default_columns)
        db.session.add(graph)
        db.session.add(kanban_board)
        db.session.commit()

        # Create default "Project setup" ticket(s) from config only for new projects (not existing repos)
        is_existing_repo = data.get("is_existing_repo") is True
        if not is_existing_repo:
            _config_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
            _default_tickets_path = os.path.join(_config_dir, "default_tickets.json")
            if os.path.isfile(_default_tickets_path):
                try:
                    with open(_default_tickets_path, encoding="utf-8") as f:
                        default_tickets = json.load(f)
                    if isinstance(default_tickets, list):
                        for t in default_tickets:
                            ticket = Ticket(
                                project_id=project.id,
                                column_id="backlog",
                                title=t.get("title", "Untitled"),
                                description=t.get("description"),
                                associated_node_ids=t.get("associated_node_ids", []),
                                associated_edge_ids=t.get("associated_edge_ids", []),
                                priority=t.get("priority", "medium"),
                                status=t.get("status", "todo"),
                            )
                            db.session.add(ticket)
                        db.session.commit()
                except (json.JSONDecodeError, OSError) as e:
                    current_app.logger.warning("Could not create default tickets: %s", e)

        # Bootstrap project memory so agent retrieve has at least one doc (avoids "No facts available")
        _bootstrap_project_memory(project)

        response = {
            **_project_to_json(project),
            "created_at": project.created_at.isoformat(),
        }
        # Plan 4.2: warn if frontier could not be seeded — agents will start from clone base
        git_mode_val = getattr(project, "git_mode", "swarm") or "swarm"
        if git_mode_val == "swarm" and not project.shipped_frontier:
            response["frontier_warning"] = (
                "Could not determine the repository's default branch tip. "
                "Set the frontier manually via POST /api/projects/{id}/frontier "
                "before running agents, or it will be set automatically after the first ship."
            )
        return jsonify(response), 201


@api_bp.route("/projects/<uuid:project_id>", methods=["GET", "PUT", "DELETE"])
def project_detail(project_id):
    """Get, update, or delete a project."""
    project = Project.query.get_or_404(project_id)

    if request.method == "GET":
        return jsonify(_project_to_json(project))

    if request.method == "PUT":
        data = request.json
        project.name = data.get("name", project.name)
        project.description = data.get("description", project.description)
        project.github_url = data.get("github_url", project.github_url)
        if "execution_mode" in data:
            project.execution_mode = "local" if (data.get("execution_mode") or "").strip().lower() == "local" else "docker"
        if "git_mode" in data:
            project.git_mode = "swarm"
        if "project_path" in data:
            project.project_path = data.get("project_path") or None
        db.session.commit()
        return jsonify(_project_to_json(project))

    if request.method == "DELETE":
        data = request.json or {}
        confirm_name = (data.get("confirm_name") or "").strip()
        if confirm_name != project.name:
            return jsonify({
                "error": "Name does not match. Send confirm_name equal to the project name to confirm deletion.",
            }), 400
        base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
        if base_save_dir:
            try:
                from utils.memory import remove_project_memory
                remove_project_memory(project.id, base_save_dir)
            except Exception as e:
                current_app.logger.warning("Failed to remove project memory for %s: %s", project.id, e)
        # Delete RAG embeddings via raw SQL so the ORM never SELECTs the embedding column (pgvector
        # OID 16397 is unknown to SQLAlchemy's ARRAY(Float)); then delete project.
        db.session.execute(text("DELETE FROM rag_embeddings WHERE project_id = :pid"), {"pid": project.id})
        db.session.delete(project)
        db.session.commit()
        return jsonify({"message": "Project deleted"})


@api_bp.route("/projects/<uuid:project_id>/frontier", methods=["POST"])
def project_frontier(project_id):
    """Set the shipped_frontier for a project.
    Body: { "hash": "<commit>", "source": "manual" }
    Also accepts source "local_git" to auto-read from project_path.
    """
    project = Project.query.get_or_404(project_id)
    data = request.json or {}
    source = (data.get("source") or "manual").strip()
    new_hash = (data.get("hash") or "").strip()

    if source == "local_git":
        # Try local path first, then fall back to GitHub URL
        path = project.project_path or ""
        new_hash = _read_local_git_tip(path) or ""
        if not new_hash and project.github_url:
            new_hash = _fetch_github_default_branch_tip(project.github_url) or ""
        if not new_hash:
            return jsonify({"error": "Could not determine tip: no readable project_path and no accessible GitHub URL"}), 422

    if not new_hash:
        return jsonify({"error": "hash is required"}), 400

    _apply_root_refresh(project, new_hash, source=source)
    return jsonify(_project_to_json(project))


# ---------------------------------------------------------------------------
# Composite Workspace — Phase 9
# Gated behind ENABLE_COMPOSITE_WORKSPACE=1 (default off — lab-grade feature).
# Remove this gate when Phase 14 (Verification Engine) is complete.
# ---------------------------------------------------------------------------

def _workspace_enabled() -> bool:
    return os.environ.get("ENABLE_COMPOSITE_WORKSPACE", "0").strip() in ("1", "true", "yes")


def _require_workspace_enabled():
    """Return (None, None) if workspace feature is enabled, else an error response."""
    if not _workspace_enabled():
        return jsonify({
            "error": "Composite Workspace is not enabled.",
            "hint": "Set ENABLE_COMPOSITE_WORKSPACE=1 to enable this lab-grade feature.",
        }), 503
    return None, None

@api_bp.route("/projects/<uuid:project_id>/workspaces", methods=["GET", "POST"])
def workspaces(project_id):
    """List workspaces or create a new draft."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    Project.query.get_or_404(project_id)

    if request.method == "GET":
        wss = (
            CompositeWorkspace.query
            .filter_by(project_id=project_id)
            .filter(CompositeWorkspace.status != "discarded")
            .order_by(CompositeWorkspace.created_at.desc())
            .all()
        )
        return jsonify([_workspace_to_json(w) for w in wss])

    data = request.json or {}
    attempt_ids = data.get("attempt_ids") or []
    if not attempt_ids:
        return jsonify({"error": "attempt_ids is required"}), 400

    project = Project.query.get(project_id)
    frontier = getattr(project, "shipped_frontier", None) or None

    # Collect leaf hashes from selected attempts
    leaf_hashes = []
    for aid in attempt_ids:
        attempt = TicketAttempt.query.get(aid)
        if attempt and attempt.agenthub_commit_hash:
            leaf_hashes.append(attempt.agenthub_commit_hash)

    ws = CompositeWorkspace(
        project_id=project_id,
        base_root_hash=frontier,
        selected_attempt_ids=[str(a) for a in attempt_ids],
        selected_leaf_hashes=leaf_hashes,
        status="draft",
        created_by=(data.get("created_by") or "").strip() or None,
    )
    db.session.add(ws)
    db.session.commit()
    return jsonify(_workspace_to_json(ws)), 201


@api_bp.route("/projects/<uuid:project_id>/workspaces/analyze", methods=["POST"])
def workspace_analyze(project_id):
    """Compatibility analysis for a proposed set of attempts before composing."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    Project.query.get_or_404(project_id)
    data = request.json or {}
    attempt_ids = data.get("attempt_ids") or []
    if not attempt_ids:
        return jsonify({"error": "attempt_ids is required"}), 400
    report = _analyze_compatibility(str(project_id), [str(a) for a in attempt_ids])
    return jsonify(report)


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>", methods=["GET"])
def workspace_detail(project_id, workspace_id):
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    include_output = request.args.get("include_test_output", "false").lower() == "true"
    return jsonify(_workspace_to_json(ws, include_test_output=include_output))


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/compose", methods=["POST"])
def workspace_compose(project_id, workspace_id):
    """Queue the workspace for async composition."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("draft", "conflicted", "test_failed"):
        return jsonify({"error": f"Cannot compose from status '{ws.status}'"}), 409
    if not ws.selected_leaf_hashes:
        return jsonify({"error": "No leaves selected — add attempts first"}), 409
    ws.status = "composing"
    db.session.commit()
    current_app.logger.info("Workspace %s queued for composition", workspace_id)
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/bless", methods=["POST"])
def workspace_bless(project_id, workspace_id):
    """Bless this workspace as the preferred candidate state.
    Marks it blessed and updates project.blessed_workspace_id.
    Does NOT imply production or shipping."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("preview_ready", "blessed"):
        return jsonify({"error": f"Cannot bless a workspace in status '{ws.status}' — compose it first"}), 409
    ws.status = "blessed"
    project = Project.query.get(project_id)
    if project:
        project.blessed_workspace_id = str(workspace_id)
    db.session.commit()
    current_app.logger.info("Workspace %s blessed for project %s", workspace_id, project_id)
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/snapshot", methods=["POST"])
def workspace_snapshot(project_id, workspace_id):
    """Create a Snapshot candidate from this workspace (stub — Phase 14 implements Snapshots)."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("preview_ready", "blessed"):
        return jsonify({"error": f"Cannot create snapshot from status '{ws.status}'"}), 409
    ws.status = "snapshot_candidate"
    db.session.commit()
    return jsonify({
        **_workspace_to_json(ws),
        "snapshot_note": "Snapshot creation is a stub — Phase 14 will add the Verification Engine.",
    })


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/promote", methods=["POST"])
def workspace_promote(project_id, workspace_id):
    """Promote this workspace to a ShipRun (compatibility export path).
    Creates a ShipRun so the coordinator picks it up and runs the shipper."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    if ws.status not in ("preview_ready", "blessed", "snapshot_candidate"):
        return jsonify({"error": f"Cannot promote a workspace in status '{ws.status}'"}), 409

    # Determine wave_num from the selected attempts (use the max wave)
    wave_num = 0
    for aid in (ws.selected_attempt_ids or []):
        attempt = TicketAttempt.query.get(aid)
        if attempt:
            wave_num = max(wave_num, attempt.wave_num)

    # Create a ShipRun — coordinator will dispatch the shipper
    run = ShipRun(
        project_id=str(project_id),
        wave_num=wave_num,
        status="queued",
        summary=f"Promoted from Composite Workspace {str(workspace_id)[:8]}",
    )
    db.session.add(run)
    # Keep the workspace at its current status — the ShipRun is the promotion artifact.
    # The workspace remains inspectable (blessed/snapshot_candidate) after promotion.
    db.session.commit()
    current_app.logger.info(
        "Workspace %s promoted to ship run %s (wave %d)", workspace_id, run.id, wave_num
    )
    return jsonify({
        "workspace": _workspace_to_json(ws),
        "ship_run": _ship_run_to_json(run),
        "label": "Promoted for Export",
    })


@api_bp.route("/projects/<uuid:project_id>/workspaces/<uuid:workspace_id>/discard", methods=["POST"])
def workspace_discard(project_id, workspace_id):
    """Discard this workspace. Discarded workspaces are hidden from the list."""
    err, status = _require_workspace_enabled()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(
        project_id=project_id, id=workspace_id
    ).first_or_404()
    ws.status = "discarded"
    # Clear blessed if this was the blessed workspace
    project = Project.query.get(project_id)
    if project and str(project.blessed_workspace_id) == str(workspace_id):
        project.blessed_workspace_id = None
    db.session.commit()
    return jsonify(_workspace_to_json(ws))


# ---------------------------------------------------------------------------
# Worker-facing workspace endpoints (coordinator + composer)
# ---------------------------------------------------------------------------

@api_bp.route("/worker/workspaces/next", methods=["POST"])
def worker_workspace_next():
    """Coordinator claims the next workspace queued for composition."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status

    ws = (
        CompositeWorkspace.query
        .filter_by(status="composing")
        .order_by(CompositeWorkspace.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not ws:
        return "", 204

    project = Project.query.get(ws.project_id)
    return jsonify({
        "workspace": _workspace_to_json(ws),
        "project": {
            "id": str(project.id) if project else None,
            "name": project.name if project else "",
            "project_path": project.project_path if project else None,
            "github_url": project.github_url if project else None,
        },
        "leaf_hashes": ws.selected_leaf_hashes or [],
    }), 200


@api_bp.route("/worker/workspaces/<uuid:workspace_id>", methods=["GET"])
def worker_workspace_get(workspace_id):
    """Fetch workspace data for a pre-claimed workspace."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(id=workspace_id).first_or_404()
    project = Project.query.get(ws.project_id)
    return jsonify({
        "workspace": _workspace_to_json(ws),
        "project": {
            "id": str(project.id) if project else None,
            "name": project.name if project else "",
            "project_path": project.project_path if project else None,
        },
        "leaf_hashes": ws.selected_leaf_hashes or [],
    })


@api_bp.route("/worker/workspaces/<uuid:workspace_id>/composed", methods=["POST"])
def worker_workspace_composed(workspace_id):
    """Composer reports successful composition."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(id=workspace_id).first_or_404()
    data = request.json or {}
    ws.status = "preview_ready"
    ws.composed_commit_hash = (data.get("composed_commit_hash") or "").strip() or None
    ws.test_status = (data.get("test_status") or "").strip() or None
    ws.test_output = (data.get("test_output") or "")[:8000] or None
    ws.changed_files = data.get("changed_files") or []
    db.session.commit()
    current_app.logger.info("Workspace %s composed: hash=%s tests=%s files=%d",
                            workspace_id, ws.composed_commit_hash, ws.test_status,
                            len(ws.changed_files or []))
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/worker/workspaces/<uuid:workspace_id>/fail", methods=["POST"])
def worker_workspace_fail(workspace_id):
    """Composer reports failure."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ws = CompositeWorkspace.query.filter_by(id=workspace_id).first_or_404()
    data = request.json or {}
    failure_type = (data.get("failure_type") or "").strip()
    ws.status = "test_failed" if failure_type == "test_failed" else "conflicted"
    ws.conflict_summary = (data.get("error") or "")[:4000]
    if data.get("test_status"):
        ws.test_status = data["test_status"]
    if data.get("test_output"):
        ws.test_output = data["test_output"][:8000]
    if data.get("composed_commit_hash"):
        ws.composed_commit_hash = data["composed_commit_hash"]
    db.session.commit()
    return jsonify(_workspace_to_json(ws))


@api_bp.route("/worker/workspaces/reset-stale", methods=["POST"])
def worker_workspace_reset_stale():
    """Reset workspace composer runs stuck in 'composing' after a coordinator restart."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    try:
        max_age = int(data.get("max_age_seconds", 1800))
    except (TypeError, ValueError):
        max_age = 1800
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    stale = CompositeWorkspace.query.filter(
        CompositeWorkspace.status == "composing",
        CompositeWorkspace.updated_at < cutoff,
    ).all()
    count = len(stale)
    for ws in stale:
        ws.status = "draft"
        ws.conflict_summary = f"Reset by coordinator after {max_age}s stale timeout."
    db.session.commit()
    return jsonify({"reset": count, "max_age_seconds": max_age})


@api_bp.route("/projects/<uuid:project_id>/debug", methods=["GET"])
def project_debug(project_id):
    """Debug/observability endpoint. Returns frontier, accepted attempts by wave,
    open ship runs, and stale attempt count. Useful for diagnosing stuck states."""
    project = Project.query.get_or_404(project_id)
    frontier = getattr(project, "shipped_frontier", None) or None

    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)

    accepted = (
        TicketAttempt.query
        .filter_by(project_id=project_id)
        .filter(TicketAttempt.status.in_(_SATISFIED_STATUSES))
        .order_by(TicketAttempt.wave_num, TicketAttempt.attempt_num)
        .all()
    )

    by_wave: dict = {}
    stale_count = 0
    for a in accepted:
        w = a.wave_num
        by_wave.setdefault(w, []).append({
            "id": str(a.id),
            "ticket_id": str(a.ticket_id),
            "attempt_num": a.attempt_num,
            "status": a.status,
            "short_hash": (a.agenthub_commit_hash or "")[:12] or None,
            "base_hash": (a.base_hash or "")[:12] or None,
            "stale": (a.base_hash != frontier) if (frontier and a.base_hash) else None,
        })
        if frontier and a.base_hash and a.base_hash != frontier:
            stale_count += 1

    open_runs = ShipRun.query.filter_by(project_id=project_id).filter(
        ShipRun.status.in_(["queued", "running", "ready_to_ship", "shipping"])
    ).all()

    return jsonify({
        "project_id": str(project_id),
        "shipped_frontier": frontier,
        "shipped_frontier_updated_at": (
            project.shipped_frontier_updated_at.isoformat()
            if project.shipped_frontier_updated_at else None
        ),
        "accepted_attempts_by_wave": by_wave,
        "stale_attempt_count": stale_count,
        "open_ship_runs": [_ship_run_to_json(r) for r in open_runs],
        "ticket_count": len(tickets),
    })


@api_bp.route("/projects/<uuid:project_id>/graph", methods=["GET", "PUT"])
def graph(project_id):
    """Get or update the project's graph."""
    graph = Graph.query.filter_by(project_id=project_id).first_or_404()

    if request.method == "GET":
        return jsonify({
            "id": str(graph.id),
            "project_id": str(graph.project_id),
            "nodes": graph.nodes if graph.nodes is not None else [],
            "edges": graph.edges if graph.edges is not None else [],
            "version": graph.version,
        })

    if request.method == "PUT":
        data = request.json
        if "nodes" in data:
            graph.nodes = data["nodes"] if data["nodes"] is not None else []
        if "edges" in data:
            graph.edges = data["edges"] if data["edges"] is not None else []
        graph.version = graph.version + 1
        db.session.commit()

        # RAG: replace node/edge embeddings for this project
        _update_graph_embeddings(project_id, graph)

        return jsonify({"version": graph.version})


@api_bp.route("/projects/<uuid:project_id>/graph/generate", methods=["POST"])
def graph_generate(project_id):
    """Clone the project's GitHub repo and use the LLM to generate an architecture graph.
    Only works when the graph is empty (no nodes). Returns generated nodes and edges,
    and writes them directly to the graph."""
    return _generate_architecture_graph(project_id)


@api_bp.route("/projects/<uuid:project_id>/kanban", methods=["GET", "PUT"])
def kanban(project_id):
    """Get or update the project's kanban board."""
    kanban = KanbanBoard.query.filter_by(project_id=project_id).first_or_404()

    if request.method == "GET":
        return jsonify({
            "id": str(kanban.id),
            "project_id": str(kanban.project_id),
            "columns": kanban.columns,
        })

    if request.method == "PUT":
        data = request.json
        if "columns" in data:
            kanban.columns = data["columns"] if data["columns"] is not None else []
        db.session.commit()
        return jsonify({"columns": kanban.columns})


@api_bp.route("/projects/<uuid:project_id>/tickets", methods=["GET", "POST"])
def tickets(project_id):
    """List tickets or create a new one."""
    if request.method == "GET":
        tickets = Ticket.query.filter_by(project_id=project_id).all()
        return jsonify([_ticket_to_json(t) for t in tickets])

    if request.method == "POST":
        data = request.json or {}
        if not data.get("title") or not data.get("column_id"):
            return jsonify({"error": "title and column_id are required"}), 400
        ticket = Ticket(
            project_id=project_id,
            column_id=data["column_id"],
            title=data["title"],
            description=data.get("description"),
            associated_node_ids=data.get("associated_node_ids", []),
            associated_edge_ids=data.get("associated_edge_ids", []),
            priority=data.get("priority", "medium"),
            status=data.get("status", "todo"),
            depends_on_ticket_ids=data.get("depends_on_ticket_ids", []),
            intent_status=data.get("intent_status", "ready"),
            rationale=data.get("rationale"),
            acceptance_criteria=data.get("acceptance_criteria"),
            constraints=data.get("constraints"),
            value_score=data.get("value_score"),
            risk_level=data.get("risk_level"),
            created_source=data.get("created_source", "manual"),
        )
        db.session.add(ticket)
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        return jsonify(_ticket_to_json(ticket)), 201


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>", methods=["GET", "PATCH", "DELETE"])
def ticket_detail(project_id, ticket_id):
    """Get, update, or delete a single ticket."""
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()

    if request.method == "GET":
        return jsonify(_ticket_to_json(ticket))

    if request.method == "PATCH":
        data = request.json
        moved_to_in_progress = (
            data.get("column_id") == "in_progress" and ticket.column_id != "in_progress"
        )
        if moved_to_in_progress:
            project = Project.query.get(project_id)
            if not project:
                return jsonify({"error": "Project not found"}), 404
            execution_mode = getattr(project, "execution_mode", None) or "docker"
            if execution_mode == "local":
                if not (project.project_path or "").strip():
                    return jsonify({
                        "error": "Project has execution mode Local; set Project path in project settings before moving a ticket to In Progress.",
                    }), 400
            else:
                if not (project.github_url or "").strip():
                    return jsonify({
                        "error": "Project must have a GitHub URL set before moving a ticket to In Progress.",
                    }), 400
            graph = Graph.query.filter_by(project_id=project_id).first()
            if not graph or not graph.nodes or len(graph.nodes) == 0:
                return jsonify({
                    "error": "Add at least one node to the graph before moving a ticket to In Progress.",
                }), 400
            ready, missing = check_execution_readiness()
            if not ready:
                missing_str = ", ".join(f"{label} ({key})" for key, label in missing)
                return jsonify({
                    "error": f"Cannot run: set these in .env and restart the backend: {missing_str}.",
                }), 400
            dep_ids = ticket.depends_on_ticket_ids or []
            if dep_ids:
                # A dependency is satisfied when it has an accepted attempt
                blocking = [
                    Ticket.query.get(d) for d in dep_ids
                    if not TicketAttempt.query.filter_by(ticket_id=d).filter(
                        TicketAttempt.status.in_(_SATISFIED_STATUSES)
                    ).first()
                ]
                blocking = [b for b in blocking if b]
                if blocking:
                    titles = ", ".join(f'"{b.title}"' for b in blocking[:3])
                    suffix = f" (+{len(blocking) - 3} more)" if len(blocking) > 3 else ""
                    return jsonify({
                        "error": f"Blocked by tickets with no accepted attempt: {titles}{suffix}.",
                    }), 400
        if "column_id" in data:
            new_col = data["column_id"]
            _SYSTEM_COLUMNS = {"backlog", "queued", "in_progress", "done"}
            kanban = KanbanBoard.query.filter_by(project_id=project_id).first()
            valid_cols = {c["id"] for c in (kanban.columns or [])} if kanban else set()
            if valid_cols and new_col not in valid_cols and new_col not in _SYSTEM_COLUMNS:
                return jsonify({"error": f"Invalid column_id '{new_col}'"}), 400
            ticket.column_id = new_col
        if "title" in data:
            ticket.title = data["title"]
        if "description" in data:
            ticket.description = data["description"]
        if "priority" in data:
            ticket.priority = data["priority"]
        if "status" in data:
            ticket.status = data["status"]
        if "associated_node_ids" in data:
            ticket.associated_node_ids = data["associated_node_ids"]
        if "associated_edge_ids" in data:
            ticket.associated_edge_ids = data["associated_edge_ids"]
        if "depends_on_ticket_ids" in data:
            ticket.depends_on_ticket_ids = data["depends_on_ticket_ids"]
        if "intent_status" in data:
            ticket.intent_status = data["intent_status"]
        if "rationale" in data:
            ticket.rationale = data["rationale"]
        if "acceptance_criteria" in data:
            ticket.acceptance_criteria = data["acceptance_criteria"]
        if "constraints" in data:
            ticket.constraints = data["constraints"]
        if "value_score" in data:
            ticket.value_score = data["value_score"]
        if "risk_level" in data:
            ticket.risk_level = data["risk_level"]
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        if moved_to_in_progress:
            ticket.intent_status = "active"
            db.session.commit()
            _enqueue_ticket_job(ticket.id)
        if data.get("column_id") == "queued":
            # Manual re-queue — fire retry_requested if the ticket had previous attempts
            has_attempts = TicketAttempt.query.filter_by(ticket_id=ticket.id).first()
            if has_attempts:
                _post_event(
                    _ticket_channel(str(ticket_id)),
                    "retry_requested: ticket manually re-queued",
                )
        return jsonify(_ticket_to_json(ticket))

    if request.method == "DELETE":
        # Clean up embeddings and any queued/running agent jobs that reference this ticket before delete.
        for c in ticket.comments:
            delete_embeddings_for_source(project_id, "ticket_comment", c.id)
        delete_embeddings_for_source(project_id, "ticket", ticket.id)
        AgentJob.query.filter_by(ticket_id=ticket.id).delete()
        db.session.delete(ticket)
        db.session.commit()
        return jsonify({"message": "Ticket deleted"})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/logs", methods=["GET"])
def ticket_logs(project_id, ticket_id):
    """Get execution logs for a ticket (for debugging)."""
    logs = ExecutionLog.query.filter_by(
        project_id=project_id,
        ticket_id=ticket_id,
    ).order_by(ExecutionLog.created_at.asc()).all()
    return jsonify([{
        "id": str(log.id),
        "step": log.step,
        "summary": log.summary,
        "raw_output": log.raw_output,
        "success": log.success,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    } for log in logs])


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/worker-context", methods=["GET"])
def worker_context(project_id, ticket_id):
    """Phase 1: Worker-facing context. Same shape as build_worker_context(ticket); no project_path. Auth: Bearer token."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    project = Project.query.get_or_404(project_id)
    try:
        from worker_context import build_worker_context
        context = build_worker_context(ticket)
    except Exception as e:
        current_app.logger.exception("worker_context: build_worker_context failed: %s", e)
        return jsonify({"error": "Failed to load context", "detail": str(e)}), 500
    context.pop("project_path", None)
    context["repo_url"] = project.github_url or ""
    context["project_id"] = str(project_id)
    return jsonify(context)


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/logs", methods=["POST"])
def ticket_logs_append(project_id, ticket_id):
    """Phase 1: Append an execution log entry (worker-facing). Body: session_id, step, summary, raw_output (optional), success (optional, default True). Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    step = (data.get("step") or "").strip() or "step"
    summary = (data.get("summary") or "").strip() or ""
    raw_output = data.get("raw_output")
    success = data.get("success", True)
    if not isinstance(success, bool):
        success = bool(success)
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    log_entry = ExecutionLog(
        project_id=project_id,
        ticket_id=ticket_id,
        session_id=session_id,
        step=step[:100],
        summary=summary,
        raw_output=raw_output,
        success=success,
    )
    db.session.add(log_entry)
    db.session.commit()
    return jsonify({"id": str(log_entry.id), "message": "Logged"})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/complete", methods=["POST"])
def ticket_complete(project_id, ticket_id):
    """Mark ticket complete (worker-facing). Body: commit_hash, summary, agent_id. Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    if ticket.column_id != "in_progress":
        return jsonify({"error": "Ticket is not in_progress; cannot mark complete"}), 409
    data = request.json or {}
    summary = (data.get("summary") or "").strip() or ""
    commit_hash = (data.get("commit_hash") or "").strip() or None
    base_hash = (data.get("base_hash") or "").strip() or None
    agent_id_val = (data.get("agent_id") or "").strip() or None

    ticket.status = "completed"
    ticket.column_id = "done"
    ticket.intent_status = "active"

    # Record AgentHub attempt for all completions
    project = Project.query.get(project_id)
    is_swarm = project and (getattr(project, "git_mode", None) or "swarm") == "swarm"
    attempt = None
    if is_swarm:
        tickets_all = Ticket.query.filter_by(project_id=project_id).all()
        wave_num = _compute_waves(tickets_all).get(str(ticket_id), 0)
        attempt = _create_attempt(
            project_id=project_id,
            ticket_id=ticket.id,
            commit_hash=commit_hash,
            base_hash=base_hash,
            wave_num=wave_num,
            agent_id=agent_id_val,
            summary=summary or None,
            initial_status="proposed",
        )
        # Validate immediately: check commit exists in AgentHub
        _validate_attempt(attempt)
        # Post validation result to ticket channel
        if attempt.status == "accepted":
            _post_event(
                _ticket_channel(str(ticket_id)),
                f"attempt_published: attempt #{attempt.attempt_num}"
                + (f" commit {commit_hash[:12]}" if commit_hash else ""),
            )
        else:
            _post_event(
                _ticket_channel(str(ticket_id)),
                f"validation_failed: {attempt.validation_error or 'unknown error'}",
            )

    db.session.commit()

    if is_swarm:
        try:
            _dispatch_unblocked_queued(project_id)
        except Exception as exc:
            current_app.logger.warning("Dispatch queued failed: %s", exc)
        # Only trigger wave merge if the attempt was accepted; failed validation
        # means no commit hash to merge, so queuing a ship run would fail immediately.
        if attempt is not None and attempt.status == "accepted":
            try:
                _maybe_trigger_wave_merge(project_id, ticket_id)
            except Exception as exc:
                current_app.logger.warning("Wave merge trigger failed: %s", exc)

    return jsonify({"message": "Complete", "ticket_id": str(ticket.id)})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/attempts", methods=["GET"])
def ticket_attempts_list(project_id, ticket_id):
    """List all attempts for a ticket, newest first."""
    Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    attempts = (
        TicketAttempt.query
        .filter_by(project_id=project_id, ticket_id=ticket_id)
        .order_by(TicketAttempt.attempt_num.desc())
        .all()
    )
    include_output = request.args.get("include_test_output", "false").lower() == "true"
    project = Project.query.get(project_id)
    frontier = getattr(project, "shipped_frontier", None) or None
    return jsonify([
        _attempt_to_json(a, include_test_output=include_output, shipped_frontier=frontier)
        for a in attempts
    ])


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/attempts/<uuid:attempt_id>/accept", methods=["POST"])
def ticket_attempt_accept(project_id, ticket_id, attempt_id):
    """Accept a ticket attempt. Supersedes any previously accepted attempt for the same ticket."""
    attempt = TicketAttempt.query.filter_by(
        project_id=project_id, ticket_id=ticket_id, id=attempt_id
    ).first_or_404()
    try:
        # Supersede any existing accepted attempt for this ticket
        prev_accepted = _get_accepted_attempt(ticket_id)
        if prev_accepted and str(prev_accepted.id) != str(attempt_id):
            try:
                _transition_attempt(prev_accepted, "superseded", reason="superseded by newer acceptance")
            except ValueError:
                pass  # already in a terminal state
        _transition_attempt(attempt, "accepted")
        db.session.commit()
        _post_event(
            _ticket_channel(str(ticket_id)),
            f"attempt_accepted: attempt #{attempt.attempt_num}"
            + (f" commit {attempt.agenthub_commit_hash[:12]}" if attempt.agenthub_commit_hash else ""),
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 409
    return jsonify(_attempt_to_json(attempt))


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/attempts/<uuid:attempt_id>/reject", methods=["POST"])
def ticket_attempt_reject(project_id, ticket_id, attempt_id):
    """Reject a ticket attempt. Optionally post feedback to AgentHub channel."""
    attempt = TicketAttempt.query.filter_by(
        project_id=project_id, ticket_id=ticket_id, id=attempt_id
    ).first_or_404()
    data = request.json or {}
    reason = (data.get("reason") or "").strip()
    try:
        _transition_attempt(attempt, "rejected", reason=reason or "rejected via API")
        db.session.commit()
        _post_event(
            _ticket_channel(str(ticket_id)),
            f"attempt_rejected: attempt #{attempt.attempt_num}"
            + (f" — {reason}" if reason else ""),
        )
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 409
    return jsonify(_attempt_to_json(attempt))


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/cancel-requested", methods=["GET"])
def ticket_cancel_requested(project_id, ticket_id):
    """Phase 1: Poll by agent to see if cancellation was requested. Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    job = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).order_by(AgentJob.created_at.desc()).first()
    requested = bool(job and job.cancel_requested)
    return jsonify({"cancel_requested": requested})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review", methods=["GET"])
def ticket_review(project_id, ticket_id):
    """Removed: per-ticket PR review is gone. Returns 410 Gone."""
    return jsonify({"error": "Per-ticket PR review has been removed. Use the Ship Room."}), 410


@api_bp.route("/projects/<uuid:project_id>/review", methods=["GET"])
def project_review_list(project_id):
    """Removed: per-ticket PR review list is gone. Returns 410 Gone."""
    return jsonify({"error": "Per-ticket PR review has been removed. Use the Ship Room."}), 410


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review/comment", methods=["POST"])
def ticket_review_comment(project_id, ticket_id):
    return jsonify({"error": "Per-ticket PR review has been removed."}), 410


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review/approve", methods=["POST"])
def ticket_review_approve(project_id, ticket_id):
    return jsonify({"error": "Per-ticket PR review has been removed."}), 410


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review/merge", methods=["POST"])
def ticket_review_merge(project_id, ticket_id):
    return jsonify({"error": "Per-ticket PR review has been removed."}), 410




@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/cancel", methods=["POST"])
def cancel_ticket_execution_api(project_id, ticket_id):
    """Request cancellation. Sets cancel_requested on the running/pending job; agent polls and exits."""
    Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    job = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).order_by(AgentJob.created_at.desc()).first()
    if job:
        job.cancel_requested = True
        db.session.commit()
    return jsonify({"message": "Cancellation requested"}), 200


# ---------- Phase 1: Queue (worker-facing). Auth: Bearer (TERARCHITECT_WORKER_API_KEY). ----------

@api_bp.route("/worker/projects", methods=["GET"])
def worker_projects():
    """Return all project IDs (and names) for coordinator discovery. Requires worker API auth."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    projects = Project.query.order_by(Project.created_at.asc()).all()
    return jsonify({
        "projects": [{"id": str(p.id), "name": p.name or "Untitled"} for p in projects],
    }), 200


# ---------------------------------------------------------------------------
# Wave computation helpers (swarm mode)
# ---------------------------------------------------------------------------

@api_bp.route("/worker/jobs/start", methods=["POST"])
def worker_jobs_start():
    """Claim one pending job. Body: optional {"project_id": "<uuid>"}.
    For swarm-mode projects, skips tickets whose graph nodes/edges conflict with
    any currently running ticket, serialising overlapping work automatically.
    Returns 200 + job or 204 (nothing available right now)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    project_id = data.get("project_id")

    if project_id:
        try:
            project_id = UUID(project_id) if isinstance(project_id, str) else project_id
        except (ValueError, TypeError):
            return jsonify({"error": "project_id must be a valid UUID"}), 400
        project = Project.query.get(project_id)
        if project is None:
            return jsonify({"error": "Project not found"}), 404

        if getattr(project, "git_mode", None) == "swarm":
            job = _claim_swarm_job(project_id)
        else:
            job = (
                AgentJob.query.filter_by(project_id=project_id, status="pending")
                .order_by(AgentJob.created_at.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
    else:
        job = (
            AgentJob.query.filter_by(status="pending")
            .order_by(AgentJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

    if not job:
        return "", 204
    job.status = "running"
    db.session.commit()
    return jsonify(_job_to_response(job)), 200


@api_bp.route("/worker/jobs/<uuid:job_id>/complete", methods=["POST"])
def worker_jobs_complete(job_id):
    """Phase 1: Mark job completed when container exits."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    job = AgentJob.query.filter_by(id=job_id).first_or_404()
    if job.status != "running":
        return jsonify({"error": "Job not running", "status": job.status}), 409
    job.status = "completed"
    db.session.commit()
    return jsonify({"message": "Job completed", "job_id": str(job_id)})


@api_bp.route("/worker/jobs/<uuid:job_id>/fail", methods=["POST"])
def worker_jobs_fail(job_id):
    """Phase 1: Mark job failed when container exits. Moves ticket back to backlog and increments failed_count."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    job = AgentJob.query.filter_by(id=job_id).first_or_404()
    if job.status != "running":
        return jsonify({"error": "Job not running", "status": job.status}), 409
    job.status = "failed"
    if job.ticket_id:
        ticket = Ticket.query.filter_by(id=job.ticket_id).first()
        if ticket:
            ticket.column_id = "queued"
            ticket.failed_count = (ticket.failed_count or 0) + 1
    db.session.commit()
    if job.ticket_id:
        _post_event(
            _ticket_channel(str(job.ticket_id)),
            f"retry_requested: job {str(job_id)[:8]} failed — ticket re-queued for retry",
        )
    return jsonify({"message": "Job failed", "job_id": str(job_id)})


@api_bp.route("/worker/jobs/reset-stale", methods=["POST"])
def worker_jobs_reset_stale():
    """Phase 1: Reset jobs stuck in 'running' state (e.g. after coordinator restart). Auth: Bearer.
    Body: optional {"max_age_seconds": N} — only reset running jobs older than N seconds (default 3600)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    try:
        max_age = int(data.get("max_age_seconds", 3600))
    except (TypeError, ValueError):
        max_age = 3600
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    stale = AgentJob.query.filter(
        AgentJob.status == "running",
        AgentJob.updated_at < cutoff,
    ).all()
    count = len(stale)
    for job in stale:
        job.status = "failed"
        if job.ticket_id:
            ticket = Ticket.query.filter_by(id=job.ticket_id).first()
            if ticket:
                ticket.column_id = "queued"
                ticket.failed_count = (ticket.failed_count or 0) + 1
    db.session.commit()
    current_app.logger.info("Reset %d stale running jobs (older than %ds)", count, max_age)
    return jsonify({"reset": count, "max_age_seconds": max_age})


@api_bp.route("/readiness", methods=["GET"])
def migration_readiness():
    """Checkpoint verification for the AgentHub-native conversion (plan 13.2).
    Returns pass/fail for each migration checkpoint A-G."""
    from sqlalchemy import inspect as _sa_inspect

    inspector = _sa_inspect(db.engine)
    tables = set(inspector.get_table_names())

    def _has_column(table, col):
        if table not in tables:
            return False
        return any(c["name"] == col for c in inspector.get_columns(table))

    checkpoints = {
        "A_data_model": {
            "description": "Core tables and intent fields exist",
            "checks": {
                "ticket_attempts": "ticket_attempts" in tables,
                "ship_runs": "ship_runs" in tables,
                "composite_workspaces": "composite_workspaces" in tables,
                "tickets.shipped_frontier_on_projects": _has_column("projects", "shipped_frontier"),
                "tickets.intent_status": _has_column("tickets", "intent_status"),
                "tickets.acceptance_criteria": _has_column("tickets", "acceptance_criteria"),
                "prs_table_dropped": "prs" not in tables,
                "pr_review_comments_dropped": "pr_review_comments" not in tables,
            },
        },
        "B_write_path": {
            "description": "Swarm ticket completion creates attempts, not PRs",
            "checks": {
                "ticket_attempts_exists": "ticket_attempts" in tables,
                "prs_dropped": "prs" not in tables,
                "agent_jobs_no_review_kind": not _has_column("agent_jobs", "pr_number"),
            },
        },
        "C_read_path": {
            "description": "Ship Room, display_state, and CLI ship commands available",
            "checks": {
                "ship_runs_exists": "ship_runs" in tables,
                "display_state_computable": _has_column("tickets", "intent_status"),
                "frontier_readable": _has_column("projects", "shipped_frontier"),
            },
        },
        "D_composition": {
            "description": "Shipper can compose release branches",
            "checks": {
                "ship_runs_has_release_branch": _has_column("ship_runs", "release_branch"),
                "ship_runs_has_test_status": _has_column("ship_runs", "test_status"),
                "ship_runs_has_composed_commit": _has_column("ship_runs", "composed_commit_hash"),
            },
        },
        "E_ship": {
            "description": "Ship advances frontier and marks attempts shipped",
            "checks": {
                "projects_has_frontier": _has_column("projects", "shipped_frontier"),
                "ticket_attempts_shipped_status": "ticket_attempts" in tables,
                "ship_runs_has_shipped_at": _has_column("ship_runs", "shipped_at"),
            },
        },
        "F_composite_workspace": {
            "description": "Composite Workspace: compose, bless, promote",
            "checks": {
                "composite_workspaces_exists": "composite_workspaces" in tables,
                "composite_workspaces_has_status": _has_column("composite_workspaces", "status"),
                "projects_has_blessed_workspace": _has_column("projects", "blessed_workspace_id"),
                "feature_flag_exists": True,  # ENABLE_COMPOSITE_WORKSPACE env var documented
            },
        },
        "G_cleanup": {
            "description": "PR-per-ticket path fully removed",
            "checks": {
                "prs_table_dropped": "prs" not in tables,
                "pr_review_comments_dropped": "pr_review_comments" not in tables,
                "merge_runs_renamed_to_ship_runs": "merge_runs" not in tables and "ship_runs" in tables,
            },
        },
    }

    result = {}
    all_pass = True
    for key, cp in checkpoints.items():
        checks = cp["checks"]
        passed = all(checks.values())
        all_pass = all_pass and passed
        result[key] = {
            "description": cp["description"],
            "passed": passed,
            "checks": checks,
        }

    return jsonify({
        "all_checkpoints_passed": all_pass,
        "checkpoints": result,
    })


@api_bp.route("/ready", methods=["GET"])
def execution_ready():
    """Readiness check: env vars for running agents + active feature flags.
    Returns { ready, missing, features }."""
    ready, missing = check_execution_readiness()
    return jsonify({
        "ready": ready,
        "missing": [{"key": k, "label": l} for k, l in missing],
        "features": {
            # Feature flags — set to 1/true to enable, 0/false to disable.
            # Remove each flag when the corresponding phase is stable.
            "composite_workspace": _workspace_enabled(),
        },
    })


@api_bp.route("/projects/<uuid:project_id>/notes", methods=["GET", "POST"])
def notes(project_id):
    """List notes or create a new one."""
    if request.method == "GET":
        notes = Note.query.filter_by(project_id=project_id).all()
        return jsonify([_note_to_json(n) for n in notes])

    if request.method == "POST":
        data = request.json or {}
        note = Note(
            project_id=project_id,
            node_id=_join_note_link_ids(data.get("node_ids")),
            edge_id=_join_note_link_ids(data.get("edge_ids")),
            title=data.get("title"),
            content=data.get("content"),
        )
        db.session.add(note)
        db.session.commit()
        content = ((data.get("title") or "") + " " + (data.get("content") or "")).strip()
        if content:
            upsert_embedding(project_id, "note", note.id, content)
        return jsonify(_note_to_json(note)), 201


@api_bp.route("/projects/<uuid:project_id>/notes/<uuid:note_id>", methods=["GET", "PATCH", "DELETE"])
def note_detail(project_id, note_id):
    """Get, update, or delete a single note."""
    note = Note.query.filter_by(project_id=project_id, id=note_id).first_or_404()

    if request.method == "GET":
        return jsonify(_note_to_json(note))

    if request.method == "PATCH":
        data = request.json or {}
        if "title" in data:
            note.title = data["title"]
        if "content" in data:
            note.content = data["content"]
        if "node_ids" in data:
            note.node_id = _join_note_link_ids(data.get("node_ids"))
        if "edge_ids" in data:
            note.edge_id = _join_note_link_ids(data.get("edge_ids"))
        db.session.commit()
        content = ((note.title or "") + " " + (note.content or "")).strip()
        if content:
            upsert_embedding(project_id, "note", note.id, content)
        return jsonify(_note_to_json(note))

    if request.method == "DELETE":
        delete_embeddings_for_source(project_id, "note", note.id)
        db.session.delete(note)
        db.session.commit()
        return jsonify({"message": "Note deleted"})


@api_bp.route("/rag/search", methods=["POST"])
def rag_search():
    """Search embeddings using vector similarity (embedding service + pgvector)."""
    data = request.json or {}
    project_id = data.get("project_id")
    query = data.get("query")
    try:
        limit = min(int(data.get("limit", 5)), 50)
    except (TypeError, ValueError):
        limit = 5
    source_types = data.get("source_types", ["node", "edge", "note", "ticket", "ticket_comment"])

    if not query:
        return jsonify({"error": "Query is required"}), 400
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    try:
        project_uuid = UUID(project_id)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid project_id"}), 400
    if Project.query.get(project_uuid) is None:
        return jsonify({"error": "Project not found"}), 404

    try:
        query_embedding = embed_single(query)
    except Exception as e:
        current_app.logger.warning("Embedding service error: %s", e)
        return jsonify({"error": "Embedding service unavailable", "detail": str(e)}), 503

    vec_str = "[" + ",".join(str(f) for f in query_embedding) + "]"
    rows = db.session.execute(
        text("""
            SELECT id, project_id, source_type, source_id, content,
                   (embedding <-> CAST(:vec AS vector)) AS distance
            FROM rag_embeddings
            WHERE project_id = :project_id AND source_type = ANY(:source_types)
            ORDER BY embedding <-> CAST(:vec AS vector)
            LIMIT :limit
        """),
        {"vec": vec_str, "project_id": project_uuid, "source_types": source_types, "limit": limit},
    ).fetchall()

    return jsonify({
        "results": [
            {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "source_type": r.source_type,
                "source_id": str(r.source_id),
                "content": r.content,
                "distance": float(r.distance),
            }
            for r in rows
        ],
    })


@api_bp.route("/projects/<uuid:project_id>/memory/index", methods=["POST"])
def memory_index(project_id):
    """Index documents into project memory (HippoRAG). Locked per project. Auth: Bearer (worker)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    if Project.query.get(project_id) is None:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    docs = data.get("docs")
    if not docs or not isinstance(docs, list):
        return jsonify({"error": "docs (list of strings) is required"}), 400
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return jsonify({"error": "MEMORY_SAVE_DIR not configured"}), 503
    try:
        from utils.memory import index as memory_index_fn, get_hipporag_kwargs
        memory_index_fn(project_id, docs, base_save_dir, **get_hipporag_kwargs())
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory index failed")
        return jsonify({"error": "Index failed", "detail": str(e)}), 500
    return jsonify({"message": "Indexed", "count": len(docs)})


@api_bp.route("/projects/<uuid:project_id>/memory/retrieve", methods=["POST"])
def memory_retrieve(project_id):
    """Retrieve relevant passages for queries (HippoRAG). Locked per project. Auth: Bearer (worker)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    if Project.query.get(project_id) is None:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    queries = data.get("queries")
    if not queries or not isinstance(queries, list):
        return jsonify({"error": "queries (list of strings) is required"}), 400
    num_to_retrieve = data.get("num_to_retrieve")
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return jsonify({"error": "MEMORY_SAVE_DIR not configured"}), 503
    try:
        from utils.memory import retrieve as memory_retrieve_fn, get_hipporag_kwargs
        results = memory_retrieve_fn(
            project_id, queries, base_save_dir,
            num_to_retrieve=num_to_retrieve,
            **get_hipporag_kwargs(),
        )
        # If no memory yet (e.g. existing project), bootstrap one doc then retry so agent gets something
        if results and all(len((r.get("docs") or [])) == 0 for r in results):
            project = Project.query.get(project_id)
            if project:
                _bootstrap_project_memory(project)
                results = memory_retrieve_fn(
                    project_id, queries, base_save_dir,
                    num_to_retrieve=num_to_retrieve,
                    **get_hipporag_kwargs(),
                )
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory retrieve failed")
        return jsonify({"error": "Retrieve failed", "detail": str(e)}), 500
    return jsonify({"results": results})


@api_bp.route("/projects/<uuid:project_id>/memory/delete", methods=["POST"])
def memory_delete(project_id):
    """Remove documents from project memory (HippoRAG). Locked per project. Auth: Bearer (worker)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    if Project.query.get(project_id) is None:
        return jsonify({"error": "Project not found"}), 404
    data = request.json or {}
    docs = data.get("docs")
    if not docs or not isinstance(docs, list):
        return jsonify({"error": "docs (list of strings) is required"}), 400
    base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
    if not base_save_dir:
        return jsonify({"error": "MEMORY_SAVE_DIR not configured"}), 503
    try:
        from utils.memory import delete as memory_delete_fn, get_hipporag_kwargs
        memory_delete_fn(project_id, docs, base_save_dir, **get_hipporag_kwargs())
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory delete failed")
        return jsonify({"error": "Delete failed", "detail": str(e)}), 500
    return jsonify({"message": "Deleted", "count": len(docs)})


@api_bp.route("/projects/<uuid:project_id>/start", methods=["POST"])
def project_start(project_id):
    """Move all backlog tickets to queued, then immediately dispatch those with no unfinished deps.
    This is the 'Go' button — VP approves the full backlog to run autonomously."""
    Project.query.get_or_404(project_id)
    backlog_tickets = Ticket.query.filter_by(project_id=project_id, column_id="backlog").all()
    for t in backlog_tickets:
        t.column_id = "queued"
    db.session.commit()
    _dispatch_unblocked_queued(project_id)
    queued_count = Ticket.query.filter_by(project_id=project_id, column_id="queued").count()
    in_progress_count = Ticket.query.filter_by(project_id=project_id, column_id="in_progress").count()
    return jsonify({
        "queued": queued_count,
        "dispatched": in_progress_count,
        "message": f"Moved {len(backlog_tickets)} tickets to queued; {in_progress_count} dispatched immediately.",
    })




# ---------------------------------------------------------------------------
# Ship Room — human-facing wave composition and release PR management
# ---------------------------------------------------------------------------

def _wave_detail(project_id, wave_num: int) -> dict:
    """Build the full wave detail payload used by multiple ship endpoints."""
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == wave_num]

    accepted_attempts = []
    for t in wave_tickets:
        a = _get_accepted_attempt(t.id)
        if a:
            accepted_attempts.append(a)

    project = Project.query.get(project_id)
    frontier = getattr(project, "shipped_frontier", None) or None

    # Most recent non-failed ship run for this wave
    ship_run = (
        ShipRun.query
        .filter_by(project_id=project_id, wave_num=wave_num)
        .filter(ShipRun.status.notin_(["failed"]))
        .order_by(ShipRun.created_at.desc())
        .first()
    )

    # Wave is ready when all tickets have accepted attempts
    accepted_ticket_ids = {str(a.ticket_id) for a in accepted_attempts}
    all_done = bool(wave_tickets) and all(str(t.id) in accepted_ticket_ids for t in wave_tickets)
    can_compose = (
        all_done and
        len(accepted_attempts) > 0 and
        (ship_run is None or ship_run.status in ("compose_failed", "failed"))
    )

    stale_count = sum(
        1 for a in accepted_attempts
        if frontier and a.base_hash and a.base_hash != frontier
    )

    return {
        "wave_num": wave_num,
        "tickets": [_ticket_to_json(t) for t in wave_tickets],
        "accepted_attempts": [
            _attempt_to_json(a, shipped_frontier=frontier) for a in accepted_attempts
        ],
        "ship_run": _ship_run_to_json(ship_run) if ship_run else None,
        "can_compose": can_compose,
        "all_done": all_done,
        "shipped_frontier": frontier,
        "stale_count": stale_count,
    }


@api_bp.route("/projects/<uuid:project_id>/ship/waves", methods=["GET"])
def ship_waves(project_id):
    """List all waves with accepted attempt counts and ship run status."""
    Project.query.get_or_404(project_id)
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    if not tickets:
        return jsonify([])
    waves = _compute_waves(tickets)
    runs = {
        r.wave_num: r
        for r in ShipRun.query.filter_by(project_id=project_id).order_by(ShipRun.created_at.desc()).all()
    }

    wave_map: dict = {}
    for t in tickets:
        w = waves.get(str(t.id), 0)
        wave_map.setdefault(w, {"wave_num": w, "tickets": [], "accepted_count": 0})
        wave_map[w]["tickets"].append(str(t.id))
        if _get_accepted_attempt(t.id):
            wave_map[w]["accepted_count"] += 1

    for w, entry in wave_map.items():
        entry["ticket_count"] = len(entry.pop("tickets"))
        wave_tix = [t for t in tickets if waves.get(str(t.id), 0) == w]
        entry["all_done"] = bool(wave_tix) and all(
            _get_accepted_attempt(t.id) for t in wave_tix
        )
        run = runs.get(w)
        entry["ship_run"] = _ship_run_to_json(run) if run else None

    return jsonify(sorted(wave_map.values(), key=lambda x: x["wave_num"]))


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>", methods=["GET"])
def ship_wave_detail(project_id, wave_num):
    """Full detail for a wave: tickets, accepted attempts, ship run, staleness."""
    Project.query.get_or_404(project_id)
    return jsonify(_wave_detail(project_id, wave_num))


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/compose", methods=["POST"])
def ship_wave_compose(project_id, wave_num):
    """Queue a ship run for this wave. Coordinator will pick it up and run the shipper."""
    project = Project.query.get_or_404(project_id)
    if (getattr(project, "git_mode", None) or "swarm") != "swarm":
        return jsonify({"error": "Project is not in swarm mode"}), 400

    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == wave_num]

    if not wave_tickets:
        return jsonify({"error": f"No tickets in wave {wave_num}"}), 404

    accepted = [_get_accepted_attempt(t.id) for t in wave_tickets]
    if not any(accepted):
        return jsonify({"error": "No accepted attempts found for this wave. Agents must complete tickets first."}), 409
    if not all(accepted):
        missing = [wave_tickets[i].title for i, a in enumerate(accepted) if not a]
        return jsonify({"error": f"Some tickets have no accepted attempt yet: {', '.join(missing[:3])}"}), 409

    # Idempotent: return the existing run if one is already active.
    # Rare concurrent double-compose is handled gracefully: the shipper detects
    # an existing PR for the same branch and returns it rather than creating a duplicate.
    existing = (
        ShipRun.query
        .filter_by(project_id=project_id, wave_num=wave_num)
        .filter(ShipRun.status.in_(["queued", "running"]))
        .first()
    )
    if existing:
        return jsonify(_ship_run_to_json(existing)), 200

    run = ShipRun(project_id=str(project_id), wave_num=wave_num, status="queued")
    db.session.add(run)
    db.session.commit()
    current_app.logger.info("Compose queued for wave %d project %s run %s", wave_num, project_id, run.id)
    return jsonify(_ship_run_to_json(run)), 201


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/ship", methods=["POST"])
def ship_wave_ship(project_id, wave_num):
    """Advance the shipped_frontier for this wave and mark attempts shipped.

    Two paths (GitHub is optional):
      - With github_url + release_pr_number: merge the release PR via gh, then advance.
      - Without (local-mode or no-main): advance frontier directly from composed_commit_hash.
    """
    project = Project.query.get_or_404(project_id)
    slug = _repo_slug_from_github_url(project.github_url) if project.github_url else None

    run = (
        ShipRun.query
        .filter_by(project_id=project_id, wave_num=wave_num, status="ready_to_ship")
        .order_by(ShipRun.created_at.desc())
        .first()
    )
    if not run:
        return jsonify({"error": "No ship run in ready_to_ship state for this wave"}), 409

    # Decide path: GitHub PR merge OR direct frontier advance
    use_github = bool(slug and run.release_pr_number)

    # Ancestry validation (plan 8.2): each accepted attempt's base must be traceable
    # to the project's shipped_frontier or another accepted attempt's commit.
    frontier = getattr(project, "shipped_frontier", None)
    if frontier:
        tickets_all_check = Ticket.query.filter_by(project_id=project_id).all()
        waves_check = _compute_waves(tickets_all_check)
        wave_tickets_check = [t for t in tickets_all_check if waves_check.get(str(t.id), 0) == wave_num]
        accepted_hashes = set()
        for t in wave_tickets_check:
            a = _get_accepted_attempt(t.id)
            if a and a.agenthub_commit_hash:
                accepted_hashes.add(a.agenthub_commit_hash)
        ancestry_errors = []
        for t in wave_tickets_check:
            a = _get_accepted_attempt(t.id)
            if not a or not a.base_hash:
                continue  # can't verify — skip
            if a.base_hash == frontier:
                continue  # built directly on root — clean
            if a.base_hash in accepted_hashes:
                continue  # built on a peer attempt — clean
            # Unrecognized base: warn in logs, surface in error if clearly broken
            current_app.logger.warning(
                "ancestry_check project=%s wave=%d ticket=%s attempt=%s base=%s "
                "not recognized as frontier=%s or peer attempt",
                project_id, wave_num, t.id, a.id,
                (a.base_hash or "")[:12], (frontier or "")[:12],
            )
            ancestry_errors.append(
                f"Ticket '{t.title[:40]}' attempt base {a.base_hash[:12]} "
                "is not the current frontier or a known peer attempt. "
                "The release branch may have unexpected content."
            )
        if ancestry_errors:
            return jsonify({
                "error": "Ancestry validation failed — unsafe to ship.",
                "details": ancestry_errors,
                "hint": "Recompose this wave to rebuild the release branch from the current frontier.",
            }), 409

    run.status = "shipping"
    db.session.commit()

    new_tip = None

    if use_github:
        # GitHub path: verify PR open, merge via gh, fetch new main tip
        try:
            r_check = subprocess.run(
                ["gh", "pr", "view", str(run.release_pr_number), "--json", "state,mergedAt", "-R", slug],
                capture_output=True, text=True, timeout=15, env=_env_for_gh_user(),
            )
            if r_check.returncode == 0:
                pr_state = json.loads(r_check.stdout or "{}")
                state = (pr_state.get("state") or "").upper()
                if state == "MERGED":
                    run.status = "ready_to_ship"
                    db.session.commit()
                    return jsonify({"error": "Release PR is already merged."}), 409
                if state == "CLOSED":
                    run.status = "ready_to_ship"
                    db.session.commit()
                    return jsonify({"error": "Release PR was closed without merging. Recompose the wave."}), 409
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # Can't verify — proceed

        data = request.json or {}
        merge_method = (data.get("merge_method") or "merge").strip().lower()
        if merge_method not in ("merge", "squash", "rebase"):
            merge_method = "merge"

        try:
            r = subprocess.run(
                ["gh", "pr", "merge", str(run.release_pr_number), f"--{merge_method}", "-R", slug],
                capture_output=True, text=True, timeout=60, env=_env_for_gh_user(),
            )
            if r.returncode != 0:
                run.status = "ready_to_ship"
                run.error = (r.stderr or r.stdout or "")[:2000]
                db.session.commit()
                return jsonify({"error": "PR merge failed", "detail": run.error}), 502
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            run.status = "ready_to_ship"
            run.error = str(exc)[:2000]
            db.session.commit()
            return jsonify({"error": "PR merge failed", "detail": str(exc)}), 502

        # Fetch new main tip from GitHub
        for branch in ("main", "master"):
            r_tip = subprocess.run(
                ["gh", "api", f"repos/{slug}/git/refs/heads/{branch}"],
                capture_output=True, text=True, timeout=15, env=_env_for_gh_user(),
            )
            if r_tip.returncode == 0:
                ref_data = json.loads(r_tip.stdout or "{}")
                new_tip = (ref_data.get("object") or {}).get("sha") or None
                if new_tip:
                    break
    else:
        # No-main / local path: advance frontier directly from composed_commit_hash.
        # GitHub is optional — no PR merge needed.
        new_tip = run.composed_commit_hash
        current_app.logger.info(
            "ship_wave_ship: no GitHub URL or PR — advancing frontier directly from composed hash %s",
            (new_tip or "")[:12],
        )

    # Shared: record shipped state, transition attempts, advance frontier
    from datetime import datetime, timezone
    run.status = "shipped"
    run.shipped_commit_hash = new_tip
    run.shipped_at = datetime.now(timezone.utc)
    db.session.commit()

    tickets = Ticket.query.filter_by(project_id=project_id).all()
    all_waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if all_waves.get(str(t.id), 0) == wave_num]
    for t in wave_tickets:
        attempt = _get_accepted_attempt(t.id)
        if not attempt or attempt.status == "shipped":
            continue
        try:
            path = {
                "accepted": ["composed", "release_pr_open", "shipped"],
                "composed": ["release_pr_open", "shipped"],
                "release_pr_open": ["shipped"],
            }
            for next_status in path.get(attempt.status, []):
                _transition_attempt(attempt, next_status, reason=f"wave {wave_num} shipped")
        except ValueError:
            current_app.logger.warning(
                "Could not transition attempt %s (status=%s) to shipped",
                attempt.id, attempt.status,
            )
    db.session.commit()

    if new_tip:
        try:
            _apply_root_refresh(project, new_tip, source="release_pr_merge")
        except Exception as exc:
            current_app.logger.warning("Root refresh after ship failed: %s", exc)

    _post_event(
        _wave_channel(project.name, wave_num),
        f"wave_shipped: wave {wave_num}" + (f" commit {new_tip[:12]}" if new_tip else ""),
    )

    return jsonify(_ship_run_to_json(run))


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/timeline", methods=["GET"])
def ship_wave_timeline(project_id, wave_num):
    """Aggregate AgentHub channel posts for a wave into a chronological timeline.
    Fetches the wave channel + all ticket channels for tickets in this wave."""
    project = Project.query.get_or_404(project_id)
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == wave_num]

    posts = []

    # Wave channel
    wave_ch = _wave_channel(project.name, wave_num)
    for p in _fetch_channel_posts(wave_ch, limit=100):
        posts.append({**p, "_channel": wave_ch, "_channel_type": "wave"})

    # Per-ticket channels
    for t in wave_tickets:
        ch = _ticket_channel(str(t.id))
        for p in _fetch_channel_posts(ch, limit=50):
            posts.append({**p, "_channel": ch, "_channel_type": "ticket", "_ticket_title": t.title})

    # Sort chronologically (created_at string is ISO — lexicographic sort works)
    posts.sort(key=lambda p: p.get("created_at") or "")

    return jsonify(posts)


@api_bp.route("/projects/<uuid:project_id>/ship/waves/<int:wave_num>/feedback", methods=["POST"])
def ship_wave_feedback(project_id, wave_num):
    """Post feedback to the wave's AgentHub channel. Body: { message, target_ticket_id (optional) }."""
    Project.query.get_or_404(project_id)
    data = request.json or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    project = Project.query.get(project_id)
    target_ticket = (data.get("target_ticket_id") or "").strip()
    if target_ticket:
        channel = _ticket_channel(target_ticket)
    else:
        project_name = project.name if project else str(project_id)
        channel = _wave_channel(project_name, wave_num)
    _post_event(channel, f"[feedback] {message}")

    return jsonify({"message": "Feedback recorded"})


# ---------------------------------------------------------------------------
# Worker-facing merge routes (coordinator claims and executes merge runs)
# ---------------------------------------------------------------------------

def _collect_wave_commit_hashes(wave_tickets: list, project) -> list:
    """Collect AgentHub commit hashes for a set of wave tickets from ticket_attempts."""
    hashes = []
    for t in wave_tickets:
        attempt = (
            TicketAttempt.query
            .filter_by(ticket_id=t.id)
            .filter(TicketAttempt.status.in_(_SATISFIED_STATUSES))
            .order_by(TicketAttempt.attempt_num.desc())
            .first()
        )
        if attempt and attempt.agenthub_commit_hash:
            hashes.append(attempt.agenthub_commit_hash)
    return hashes

@api_bp.route("/worker/ship-run/next", methods=["POST"])
def worker_ship_run_next():
    """Coordinator claims the next queued merge run.
    Returns 204 if nothing to do, or {run, project, commit_hashes, wave_tickets}."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status

    run = (
        ShipRun.query.filter_by(status="queued")
        .order_by(ShipRun.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not run:
        return "", 204

    run.status = "running"
    db.session.commit()

    project = Project.query.get(run.project_id)
    if project:
        _post_event(
            _wave_channel(project.name, run.wave_num),
            f"release_composition_started: wave {run.wave_num} run {str(run.id)[:8]}",
        )
    tickets = Ticket.query.filter_by(project_id=run.project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == run.wave_num]

    commit_hashes = _collect_wave_commit_hashes(wave_tickets, project)

    return jsonify({
        "run": _ship_run_to_json(run),
        "project": {
            "id": str(project.id),
            "name": project.name,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "git_mode": project.git_mode,
        },
        "wave_tickets": [
            {"id": str(t.id), "title": t.title, "column_id": t.column_id}
            for t in wave_tickets
        ],
        "commit_hashes": commit_hashes,
    }), 200


@api_bp.route("/worker/ship-run/<uuid:run_id>", methods=["GET"])
def worker_ship_run_get(run_id):
    """Fetch full data for a specific (already-claimed) merge run.
    Used by coordinator-spawned merger subprocesses that were pre-claimed via /worker/ship-run/next."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    project = Project.query.get(run.project_id)
    tickets = Ticket.query.filter_by(project_id=run.project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == run.wave_num]
    commit_hashes = _collect_wave_commit_hashes(wave_tickets, project)
    return jsonify({
        "run": _ship_run_to_json(run),
        "project": {
            "id": str(project.id),
            "name": project.name,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "git_mode": project.git_mode,
        },
        "wave_tickets": [
            {"id": str(t.id), "title": t.title, "column_id": t.column_id}
            for t in wave_tickets
        ],
        "commit_hashes": commit_hashes,
    }), 200


@api_bp.route("/worker/ship-run/<uuid:run_id>/composed", methods=["POST"])
def worker_ship_run_composed(run_id):
    """Shipper reports successful composition: release branch created, PR opened, ready to ship.
    Body: release_branch, release_pr_url, release_pr_number, composed_commit_hash, base_main_hash,
          test_status, test_output, changed_files, summary."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "ready_to_ship"
    run.release_branch = (data.get("release_branch") or "").strip() or None
    run.release_pr_url = (data.get("release_pr_url") or "").strip() or None
    run.release_pr_number = data.get("release_pr_number") or None
    run.composed_commit_hash = (data.get("composed_commit_hash") or "").strip() or None
    run.base_main_hash = (data.get("base_main_hash") or "").strip() or None
    run.test_status = (data.get("test_status") or "").strip() or None
    run.test_output = (data.get("test_output") or "")[:8000] or None
    run.changed_files = data.get("changed_files") or []
    run.summary = (data.get("summary") or "").strip() or None
    db.session.commit()
    current_app.logger.info(
        "Ship run %s composed for wave %d: PR #%s branch %s",
        run_id, run.wave_num, run.release_pr_number, run.release_branch,
    )
    project = Project.query.get(run.project_id)
    if project:
        pr_ref = f"PR #{run.release_pr_number}" if run.release_pr_number else run.release_branch or "no PR"
        _post_event(
            _wave_channel(project.name, run.wave_num),
            f"release_pr_opened: {pr_ref} tests={run.test_status or 'skipped'} "
            f"files={len(run.changed_files or [])}",
        )
    return jsonify(_ship_run_to_json(run))


@api_bp.route("/worker/ship-run/<uuid:run_id>/done", methods=["POST"])
def worker_ship_run_done(run_id):
    """Legacy: merge agent reports success (old swarm-branch merger). {commit_hash, pr_url}."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "done"
    run.commit_hash = (data.get("commit_hash") or "").strip() or None
    run.pr_url = (data.get("pr_url") or "").strip() or None
    db.session.commit()

    # Advance the shipped_frontier so queued work in the next wave builds on the merged state
    if run.commit_hash:
        project = Project.query.get(run.project_id)
        if project:
            try:
                _apply_root_refresh(project, run.commit_hash, source="wave_merge")
            except Exception as exc:
                current_app.logger.warning("Root refresh after merge failed: %s", exc)

    return jsonify(_ship_run_to_json(run))


@api_bp.route("/worker/ship-run/<uuid:run_id>/fail", methods=["POST"])
def worker_ship_run_fail(run_id):
    """Shipper or merger reports failure. Body: error, fix_ticket_title, fix_ticket_description, compose_failed (bool).
    If compose_failed=true, status is set to compose_failed rather than failed."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = ShipRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "compose_failed" if data.get("compose_failed") else "failed"
    run.error = (data.get("error") or "")[:4000]
    db.session.commit()

    project = Project.query.get(run.project_id)
    if project:
        error_short = (run.error or "")[:200]
        _post_event(
            _wave_channel(project.name, run.wave_num),
            f"release_composition_failed: {error_short}",
        )

    # Auto-create a fix ticket if the agent supplied one
    fix_title = (data.get("fix_ticket_title") or "").strip()
    fix_desc = (data.get("fix_ticket_description") or "").strip()
    fix_ticket_id = None
    if fix_title:
        fix = Ticket(
            project_id=run.project_id,
            title=fix_title,
            description=fix_desc or fix_title,
            column_id="backlog",
            priority="high",
            status="todo",
            associated_node_ids=["*"],
        )
        db.session.add(fix)
        db.session.commit()
        fix_ticket_id = str(fix.id)

    result = _ship_run_to_json(run)
    if fix_ticket_id:
        result["fix_ticket_id"] = fix_ticket_id
    return jsonify(result)


@api_bp.route("/worker/ship-run/reset-stale", methods=["POST"])
def worker_ship_run_reset_stale():
    """Reset ship runs stuck in 'running' state after a coordinator/shipper restart.
    Body: optional {"max_age_seconds": N} — reset runs older than N seconds (default 1800)."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    data = request.json or {}
    try:
        max_age = int(data.get("max_age_seconds", 1800))
    except (TypeError, ValueError):
        max_age = 1800
    from datetime import datetime, timezone, timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age)
    stale = ShipRun.query.filter(
        ShipRun.status == "running",
        ShipRun.updated_at < cutoff,
    ).all()
    count = len(stale)
    for run in stale:
        run.status = "queued"  # re-queue so coordinator picks it up again
        run.error = f"Reset by coordinator after {max_age}s stale timeout."
    db.session.commit()
    current_app.logger.info("Reset %d stale running ship run(s) (older than %ds)", count, max_age)
    return jsonify({"reset": count, "max_age_seconds": max_age})
