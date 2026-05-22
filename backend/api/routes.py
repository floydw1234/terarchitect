"""
API Routes for Terarchitect
"""
import json
import os
import subprocess
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, ExecutionLog, PR, AgentJob, MergeRun
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
    merge_run_to_json as _merge_run_to_json,
)
from .services.notes_service import (
    join_note_link_ids as _join_note_link_ids,
    note_to_json as _note_to_json,
    split_note_link_ids as _split_note_link_ids,
)
from .services.pr_service import (
    env_for_gh_user as _env_for_gh_user,
    extract_test_names_from_patch as _extract_test_names_from_patch,
    get_ticket_pr_slug as _get_ticket_pr_slug,
    is_test_file as _is_test_file,
    mark_pr_comment_addressed as _mark_pr_comment_addressed,
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
        project = Project(
            name=data.get("name"),
            description=data.get("description"),
            github_url=data.get("github_url"),
            execution_mode="local" if (data.get("execution_mode") or "").strip().lower() == "local" else "docker",
            git_mode="swarm" if (data.get("git_mode") or "").strip().lower() == "swarm" else "structured",
            project_path=data.get("project_path"),
        )
        db.session.add(project)
        db.session.flush()  # assigns project.id from the DB before it's used below
        graph = Graph(project_id=project.id)
        default_columns = [
            {"id": "backlog", "title": "Backlog", "order": 0},
            {"id": "queued", "title": "Queued", "order": 1},
            {"id": "in_progress", "title": "In Progress", "order": 2},
            {"id": "in_review", "title": "In Review", "order": 3},
            {"id": "done", "title": "Done", "order": 4},
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

        return jsonify({
            **_project_to_json(project),
            "created_at": project.created_at.isoformat(),
        }), 201


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
            project.git_mode = "swarm" if (data.get("git_mode") or "").strip().lower() == "swarm" else "structured"
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
                blocking = Ticket.query.filter(
                    Ticket.id.in_(dep_ids),
                    Ticket.column_id != "done",
                ).all()
                if blocking:
                    titles = ", ".join(f'"{b.title}"' for b in blocking[:3])
                    suffix = f" (+{len(blocking) - 3} more)" if len(blocking) > 3 else ""
                    return jsonify({
                        "error": f"Blocked by unfinished tickets: {titles}{suffix}. Complete those first.",
                    }), 400
        if "column_id" in data:
            new_col = data["column_id"]
            _SYSTEM_COLUMNS = {"backlog", "queued", "in_progress", "in_review", "done"}
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
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        if moved_to_in_progress:
            _enqueue_ticket_job(ticket.id)
        # Cascade: when a ticket reaches done, dispatch any queued tickets now unblocked.
        if data.get("column_id") == "done":
            _dispatch_unblocked_queued(project_id)
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
    """Phase 1: Mark ticket complete (worker-facing). Body: pr_url, pr_number, summary; optional review_comment_body. Auth: Bearer."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    if ticket.column_id != "in_progress":
        return jsonify({"error": "Ticket is not in_progress; cannot mark complete"}), 409
    project = Project.query.get(project_id)
    git_mode = getattr(project, "git_mode", None) or "structured"
    data = request.json or {}
    pr_url = (data.get("pr_url") or "").strip() or None
    pr_number = data.get("pr_number")
    if pr_number is not None and not isinstance(pr_number, int):
        try:
            pr_number = int(pr_number)
        except (TypeError, ValueError):
            pr_number = None
    summary = (data.get("summary") or "").strip() or ""
    review_comment_body = (data.get("review_comment_body") or "").strip() or None
    commit_hash = (data.get("commit_hash") or "").strip() or None

    ticket.status = "completed"
    if git_mode == "swarm":
        # Swarm mode: skip PR review, go straight to done
        ticket.column_id = "done"
        if commit_hash:
            existing = PR.query.filter_by(ticket_id=ticket.id).first()
            if existing:
                existing.commit_hash = commit_hash
            else:
                db.session.add(PR(
                    project_id=project_id,
                    ticket_id=ticket.id,
                    commit_hash=commit_hash,
                ))
    else:
        ticket.column_id = "in_review"
        if pr_url is not None:
            existing = PR.query.filter_by(ticket_id=ticket.id).first()
            if existing:
                existing.pr_url = pr_url
                existing.pr_number = pr_number
            else:
                db.session.add(PR(
                    project_id=project_id,
                    ticket_id=ticket.id,
                    pr_url=pr_url,
                    pr_number=pr_number,
                ))
    db.session.commit()

    # Swarm mode: release any queued tickets that were held back due to node conflicts
    if git_mode == "swarm":
        try:
            _dispatch_unblocked_queued(project_id)
        except Exception as exc:
            current_app.logger.warning("Dispatch queued failed: %s", exc)

    # Swarm mode: check if this ticket's wave is fully done → auto-queue merge
    if git_mode == "swarm":
        try:
            _maybe_trigger_wave_merge(project_id, ticket_id)
        except Exception as exc:
            current_app.logger.warning("Wave merge trigger failed: %s", exc)

    return jsonify({"message": "Complete", "ticket_id": str(ticket.id)})


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
    """Get PR summary and commits from GitHub for quick review. 404 if ticket has no PR."""
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    project = Project.query.get_or_404(project_id)
    pr_row = PR.query.filter_by(ticket_id=ticket.id).first()
    if not pr_row or not pr_row.pr_number or not pr_row.pr_url:
        return jsonify({"error": "No PR for this ticket"}), 404
    slug = _repo_slug_from_github_url(project.github_url)
    if not slug:
        return jsonify({"error": "Project has no valid GitHub URL"}), 404

    summary = ""
    commits = []
    test_files = []
    tests_description = ""
    pr_state = "unknown"
    merged = False
    try:
        r_pr = subprocess.run(
            ["gh", "api", f"repos/{slug}/pulls/{pr_row.pr_number}"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_env_for_gh_user(),
        )
        if r_pr.returncode == 0 and r_pr.stdout:
            pr_data = json.loads(r_pr.stdout)
            pr_state = pr_data.get("state") or "unknown"
            merged = bool(pr_data.get("merged"))
            body = (pr_data.get("body") or "").strip()
            if "## What was accomplished" in body:
                part = body.split("## What was accomplished")[-1].strip()
                if "---" in part:
                    part = part.split("---")[0].strip()
                summary = part.strip()
            else:
                summary = body or "No description."

        r_commits = subprocess.run(
            ["gh", "api", f"repos/{slug}/pulls/{pr_row.pr_number}/commits"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_env_for_gh_user(),
        )
        if r_commits.returncode == 0 and r_commits.stdout:
            raw = json.loads(r_commits.stdout)
            list_commits = raw if isinstance(raw, list) else []
            for c in list_commits:
                sha = (c.get("sha") or "")[:7]
                msg = (c.get("commit") or {}).get("message") or ""
                if msg and "\n" in msg:
                    msg = msg.split("\n")[0]
                commits.append({"sha": sha, "message": msg.strip()})

        # Test files: only those changed/added in this PR (from GitHub PR files API)
        r_files = subprocess.run(
            ["gh", "api", f"repos/{slug}/pulls/{pr_row.pr_number}/files"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_env_for_gh_user(),
        )
        if r_files.returncode == 0 and r_files.stdout:
            files_data = json.loads(r_files.stdout)
            files_list = files_data if isinstance(files_data, list) else []
            for f in files_list:
                path = (f.get("filename") or "").strip()
                if not _is_test_file(path):
                    continue
                patch = f.get("patch") or ""
                names = _extract_test_names_from_patch(patch)
                test_files.append({"path": path, "test_names": names})
        test_files.sort(key=lambda x: (x["path"].replace("\\", "/").lower(), x["path"]))

        comments = []
        r_comments = subprocess.run(
            ["gh", "api", f"repos/{slug}/issues/{pr_row.pr_number}/comments"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_env_for_gh_user(),
        )
        if r_comments.returncode == 0 and r_comments.stdout:
            raw_comments = json.loads(r_comments.stdout)
            list_comments = raw_comments if isinstance(raw_comments, list) else []
            for c in list_comments:
                author = (c.get("user") or {}).get("login") or "unknown"
                body = (c.get("body") or "").strip()
                created_at = c.get("created_at")
                comments.append({"author": author, "body": body, "created_at": created_at})
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        current_app.logger.warning("Review fetch failed: %s", e)
        return jsonify({"error": "Failed to fetch PR from GitHub", "detail": str(e)}), 502

    return jsonify({
        "summary": summary,
        "commits": commits,
        "test_files": test_files,
        "tests_description": tests_description,
        "comments": comments,
        "pr_url": pr_row.pr_url,
        "pr_number": pr_row.pr_number,
        "pr_state": pr_state,
        "merged": merged,
    })


@api_bp.route("/projects/<uuid:project_id>/review", methods=["GET"])
def project_review_list(project_id):
    """List up to 20 most recent tickets that have a PR, pending first. With PR status from GitHub."""
    project = Project.query.get_or_404(project_id)
    slug = _repo_slug_from_github_url(project.github_url)
    prs = list(
        db.session.query(PR, Ticket)
        .join(Ticket, Ticket.id == PR.ticket_id)
        .filter(PR.project_id == project_id)
        .filter(PR.pr_number.isnot(None))
        .order_by(PR.created_at.desc())
        .limit(50)
        .all()
    )

    def _fetch_pr_state(pr_row, ticket):
        pr_state = "unknown"
        merged = False
        if slug:
            try:
                r = subprocess.run(
                    ["gh", "api", f"repos/{slug}/pulls/{pr_row.pr_number}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=_env_for_gh_user(),
                )
                if r.returncode == 0 and r.stdout:
                    data = json.loads(r.stdout)
                    pr_state = data.get("state") or "unknown"
                    merged = bool(data.get("merged"))
            except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
                pass
        created = pr_row.created_at
        ts = created.timestamp() if created else 0
        return {
            "id": str(ticket.id),
            "title": ticket.title,
            "pr_url": pr_row.pr_url,
            "pr_number": pr_row.pr_number,
            "pr_state": pr_state,
            "merged": merged,
            "_sort_ts": ts,
        }

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_fetch_pr_state, pr_row, ticket) for pr_row, ticket in prs]
        out = [f.result() for f in concurrent.futures.as_completed(futures)]

    # Exclude closed PRs that were not merged (e.g. abandoned or closed without merge)
    out = [x for x in out if not (x["pr_state"] == "closed" and not x["merged"])]
    # Pending (open) first, then by most recent
    out.sort(key=lambda x: (x["merged"], -x["_sort_ts"]))
    for item in out:
        del item["_sort_ts"]
    return jsonify(out[:20])


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review/comment", methods=["POST"])
def ticket_review_comment(project_id, ticket_id):
    """Post a comment on the ticket's PR. Body: { \"body\": \"...\" }."""
    pr_row, slug = _get_ticket_pr_slug(project_id, ticket_id)
    if not pr_row or not slug:
        return jsonify({"error": "No PR for this ticket"}), 404
    data = request.json or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body is required"}), 400
    if len(body) > 60000:
        body = body[:59997] + "..."
    try:
        r = subprocess.run(
            ["gh", "pr", "comment", str(pr_row.pr_number), "--body", body, "-R", slug],
            capture_output=True,
            text=True,
            timeout=30,
            env=_env_for_gh_user(),
        )
        if r.returncode != 0:
            return jsonify({"error": "Failed to post comment", "detail": (r.stderr or "").strip()}), 502
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return jsonify({"error": "Failed to post comment", "detail": str(e)}), 502
    return jsonify({"message": "Comment posted"})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review/approve", methods=["POST"])
def ticket_review_approve(project_id, ticket_id):
    """Approve the ticket's PR. Body: optional { \"body\": \"...\" }."""
    pr_row, slug = _get_ticket_pr_slug(project_id, ticket_id)
    if not pr_row or not slug:
        return jsonify({"error": "No PR for this ticket"}), 404
    data = request.json or {}
    body = (data.get("body") or "").strip()
    try:
        args = ["gh", "pr", "review", str(pr_row.pr_number), "--approve", "-R", slug]
        if body:
            args.extend(["--body", body[:60000]])
        r = subprocess.run(args, capture_output=True, text=True, timeout=30, env=_env_for_gh_user())
        if r.returncode != 0:
            return jsonify({"error": "Failed to approve", "detail": (r.stderr or "").strip()}), 502
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return jsonify({"error": "Failed to approve", "detail": str(e)}), 502
    return jsonify({"message": "PR approved"})


@api_bp.route("/projects/<uuid:project_id>/tickets/<uuid:ticket_id>/review/merge", methods=["POST"])
def ticket_review_merge(project_id, ticket_id):
    """Merge the ticket's PR. Body: optional { \"merge_method\": \"merge\"|\"squash\"|\"rebase\" }."""
    pr_row, slug = _get_ticket_pr_slug(project_id, ticket_id)
    if not pr_row or not slug:
        return jsonify({"error": "No PR for this ticket"}), 404
    data = request.json or {}
    method = (data.get("merge_method") or "merge").strip().lower()
    if method not in ("merge", "squash", "rebase"):
        method = "merge"
    try:
        flag = "--merge" if method == "merge" else "--squash" if method == "squash" else "--rebase"
        r = subprocess.run(
            ["gh", "pr", "merge", str(pr_row.pr_number), flag, "-R", slug],
            capture_output=True,
            text=True,
            timeout=30,
            env=_env_for_gh_user(),
        )
        if r.returncode != 0:
            return jsonify({"error": "Failed to merge", "detail": (r.stderr or "").strip()}), 502
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return jsonify({"error": "Failed to merge", "detail": str(e)}), 502
    return jsonify({"message": "PR merged"})


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
    if job.kind == "review" and job.pr_number is not None and job.github_comment_id is not None:
        _mark_pr_comment_addressed(job.project_id, job.pr_number, job.github_comment_id)
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
    # Move ticket back to the appropriate column and track failure count.
    # Review jobs: ticket stays in in_review (it was already there, still needs addressing).
    # Ticket jobs: ticket goes back to queued (still approved to run, just needs a retry).
    if job.ticket_id:
        ticket = Ticket.query.filter_by(id=job.ticket_id).first()
        if ticket:
            if job.kind == "review":
                ticket.column_id = "in_review"
            else:
                ticket.column_id = "queued"
            ticket.failed_count = (ticket.failed_count or 0) + 1
    db.session.commit()
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
                if job.kind == "review":
                    ticket.column_id = "in_review"
                else:
                    ticket.column_id = "queued"
                ticket.failed_count = (ticket.failed_count or 0) + 1
    db.session.commit()
    current_app.logger.info("Reset %d stale running jobs (older than %ds)", count, max_age)
    return jsonify({"reset": count, "max_age_seconds": max_age})


@api_bp.route("/ready", methods=["GET"])
def execution_ready():
    """Lightweight readiness check: are required env vars set to run a ticket?
    Returns { ready: bool, missing: [{ key, label }] }. Frontend can use this to disable Run or show a warning."""
    ready, missing = check_execution_readiness()
    return jsonify({
        "ready": ready,
        "missing": [{"key": k, "label": l} for k, l in missing],
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


@api_bp.route("/projects/<uuid:project_id>/merge/runs", methods=["GET"])
def merge_runs_list(project_id):
    """List all merge runs for a project, newest first."""
    Project.query.get_or_404(project_id)
    runs = (
        MergeRun.query.filter_by(project_id=project_id)
        .order_by(MergeRun.created_at.desc())
        .all()
    )
    return jsonify([_merge_run_to_json(r) for r in runs])


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


@api_bp.route("/projects/<uuid:project_id>/merge/trigger", methods=["POST"])
def merge_trigger(project_id):
    """Manually queue a merge run for the current incomplete wave (or a specific wave).
    Body: optional {"wave_num": N}.  Useful for the 'Go' button."""
    project = Project.query.get_or_404(project_id)
    if (getattr(project, "git_mode", None) or "structured") != "swarm":
        return jsonify({"error": "Project is not in swarm mode"}), 400
    data = request.json or {}
    wave_num = data.get("wave_num")

    if wave_num is None:
        # Default: find the lowest wave with all tickets done but no successful merge run
        tickets = Ticket.query.filter_by(project_id=project_id).all()
        if not tickets:
            return jsonify({"error": "No tickets found"}), 404
        waves = _compute_waves(tickets)
        all_wave_nums = sorted(set(waves.values()))
        wave_num = None
        for w in all_wave_nums:
            wt = [t for t in tickets if waves.get(str(t.id), 0) == w]
            if all(t.column_id == "done" for t in wt):
                existing = MergeRun.query.filter_by(
                    project_id=project_id, wave_num=w,
                ).filter(MergeRun.status.in_(["queued", "running", "done"])).first()
                if not existing:
                    wave_num = w
                    break
        if wave_num is None:
            return jsonify({"error": "No eligible wave found (all waves already have a merge run, or not all tickets are done)"}), 409

    run = MergeRun(project_id=str(project_id), wave_num=int(wave_num), status="queued")
    db.session.add(run)
    db.session.commit()
    return jsonify(_merge_run_to_json(run)), 201


@api_bp.route("/projects/<uuid:project_id>/merge/waves", methods=["GET"])
def merge_waves(project_id):
    """Return wave assignment for all tickets + merge run status per wave."""
    Project.query.get_or_404(project_id)
    tickets = Ticket.query.filter_by(project_id=project_id).all()
    waves = _compute_waves(tickets)
    runs = {r.wave_num: r for r in MergeRun.query.filter_by(project_id=project_id).all()}

    wave_map: dict = {}
    for t in tickets:
        w = waves.get(str(t.id), 0)
        wave_map.setdefault(w, {"wave_num": w, "tickets": [], "merge_run": None})
        wave_map[w]["tickets"].append({
            "id": str(t.id),
            "title": t.title,
            "column_id": t.column_id,
        })
    for w, run in runs.items():
        wave_map.setdefault(w, {"wave_num": w, "tickets": [], "merge_run": None})
        wave_map[w]["merge_run"] = _merge_run_to_json(run)

    return jsonify(sorted(wave_map.values(), key=lambda x: x["wave_num"]))


# ---------------------------------------------------------------------------
# Worker-facing merge routes (coordinator claims and executes merge runs)
# ---------------------------------------------------------------------------

@api_bp.route("/worker/merge/next", methods=["POST"])
def worker_merge_next():
    """Coordinator claims the next queued merge run.
    Returns 204 if nothing to do, or {run, project, commit_hashes, wave_tickets}."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status

    run = (
        MergeRun.query.filter_by(status="queued")
        .order_by(MergeRun.created_at.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not run:
        return "", 204

    run.status = "running"
    db.session.commit()

    # Collect commit hashes for this wave's tickets
    project = Project.query.get(run.project_id)
    tickets = Ticket.query.filter_by(project_id=run.project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == run.wave_num]

    commit_hashes = []
    for t in wave_tickets:
        pr_row = PR.query.filter_by(ticket_id=t.id).first()
        if pr_row and pr_row.commit_hash:
            commit_hashes.append(pr_row.commit_hash)

    return jsonify({
        "run": _merge_run_to_json(run),
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


@api_bp.route("/worker/merge/<uuid:run_id>", methods=["GET"])
def worker_merge_get(run_id):
    """Fetch full data for a specific (already-claimed) merge run.
    Used by coordinator-spawned merger subprocesses that were pre-claimed via /worker/merge/next."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = MergeRun.query.filter_by(id=run_id).first_or_404()
    project = Project.query.get(run.project_id)
    tickets = Ticket.query.filter_by(project_id=run.project_id).all()
    waves = _compute_waves(tickets)
    wave_tickets = [t for t in tickets if waves.get(str(t.id), 0) == run.wave_num]
    commit_hashes = []
    for t in wave_tickets:
        pr_row = PR.query.filter_by(ticket_id=t.id).first()
        if pr_row and pr_row.commit_hash:
            commit_hashes.append(pr_row.commit_hash)
    return jsonify({
        "run": _merge_run_to_json(run),
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


@api_bp.route("/worker/merge/<uuid:run_id>/done", methods=["POST"])
def worker_merge_done(run_id):
    """Merge agent reports success: {commit_hash, pr_url}."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = MergeRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "done"
    run.commit_hash = (data.get("commit_hash") or "").strip() or None
    run.pr_url = (data.get("pr_url") or "").strip() or None
    db.session.commit()
    return jsonify(_merge_run_to_json(run))


@api_bp.route("/worker/merge/<uuid:run_id>/fail", methods=["POST"])
def worker_merge_fail(run_id):
    """Merge agent reports failure: {error, fix_ticket_title, fix_ticket_description}.
    Optionally auto-creates a fix ticket in the project's backlog."""
    err, status = _require_worker_auth()
    if err is not None:
        return err, status
    run = MergeRun.query.filter_by(id=run_id).first_or_404()
    data = request.json or {}
    run.status = "failed"
    run.error = (data.get("error") or "")[:4000]
    db.session.commit()

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

    result = _merge_run_to_json(run)
    if fix_ticket_id:
        result["fix_ticket_id"] = fix_ticket_id
    return jsonify(result)
