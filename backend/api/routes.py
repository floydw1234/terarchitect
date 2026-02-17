"""
API Routes for Terarchitect
"""
import base64
import json
import os
import re
import queue
import subprocess
import threading
import time
from uuid import UUID, uuid5, NAMESPACE_DNS

import requests
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text, nullslast

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, Setting, AppSetting, RAGEmbedding, ExecutionLog, PR, PRReviewComment
from utils.embedding_client import embed_single
from utils.rag import upsert_embedding, delete_embeddings_for_source
from utils.app_settings import (
    get_all_for_api,
    set_value,
    delete_key,
    ALLOWED_KEYS,
    SENSITIVE_KEYS,
    get_gh_env_for_user,
    get_gh_env_for_agent,
    get_setting_or_env,
)
from utils.app_settings_crypto import is_encryption_available

api_bp = Blueprint("api", __name__)


def _env_for_gh_user():
    """Env for gh CLI in UI context (PR comment, approve, merge, poll). Uses stored user token if set."""
    return {**os.environ, **get_gh_env_for_user()}


# Single agent queue: one worker runs jobs (ticket or review) one after another so only one agent touches the repo at a time.
_agent_queue = queue.Queue()
# Keys for jobs in queue or in progress; don't enqueue duplicate ticket/review work.
_agent_queue_keys = set()
_agent_queue_lock = threading.Lock()
# Hard execution mutex: only one agent run may execute at a time in this process.
_agent_run_lock = threading.Lock()

PROJECT_SETTING_DOCKER_IMAGE = "docker_image"
PROJECT_SETTING_DOCKER_IMAGE_OPTIONS = "docker_image_options"
PROJECT_SETTING_DOCKERFILE = "dockerfile"
DEFAULT_DOCKER_IMAGE_SUGGESTIONS = [
    "python:3.12-slim",
    "python:3.11-slim",
    "node:20-slim",
    "node:22-slim",
    "eclipse-temurin:17-jdk",
    "eclipse-temurin:21-jdk",
    "golang:1.22",
    "rust:1.76-slim",
    "mcr.microsoft.com/dotnet/sdk:8.0",
    "buildpack-deps:bookworm",
]


def _agent_job_key(kind, args):
    """Unique key for a queued or in-progress job. ticket -> ticket:id, review -> review:ticket_id:pr#."""
    if kind == "ticket":
        return f"ticket:{args[0]}"
    if kind == "review":
        # args: (ticket_id, comment_body, pr_number, project_id, github_comment_id)
        return f"review:{args[0]}:{args[2]}"
    return None


def _get_project_setting(project_id, key, default=None):
    row = Setting.query.filter_by(project_id=project_id, key=key).first()
    if not row:
        return default
    return row.value if row.value is not None else default


def _set_project_setting(project_id, key, value):
    row = Setting.query.filter_by(project_id=project_id, key=key).first()
    if value is None:
        if row:
            db.session.delete(row)
        return
    if row:
        row.value = value
    else:
        db.session.add(Setting(project_id=project_id, key=key, value=value))


def _project_docker_config(project_id):
    docker_image = _get_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE)
    if not isinstance(docker_image, str):
        docker_image = None
    else:
        docker_image = docker_image.strip() or None
    docker_image_options = _get_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, [])
    if not isinstance(docker_image_options, list):
        docker_image_options = []
    docker_image_options = [str(v).strip() for v in docker_image_options if str(v).strip()]
    return docker_image, docker_image_options


def _project_to_json(project: Project):
    docker_image, docker_image_options = _project_docker_config(project.id)
    dockerfile = _get_project_setting(project.id, PROJECT_SETTING_DOCKERFILE)
    if dockerfile is not None and not isinstance(dockerfile, str):
        dockerfile = str(dockerfile) if dockerfile else None
    elif dockerfile is not None:
        dockerfile = (dockerfile or "").strip() or None
    return {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "project_path": project.project_path,
        "github_url": project.github_url,
        "docker_image": docker_image,
        "docker_image_options": docker_image_options,
        "dockerfile": dockerfile,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _normalize_frontend_llm_chat_url(raw_url: str) -> str:
    url = (raw_url or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return f"{url}/chat/completions"
    return f"{url}/v1/chat/completions"


def _extract_node_technologies(nodes) -> list[str]:
    techs = []
    for node in (nodes or []):
        data = node.get("data") if isinstance(node, dict) else {}
        if not isinstance(data, dict):
            continue
        raw_values = []
        if "technologies" in data:
            raw_values = data.get("technologies")
        elif "tech" in data:
            raw_values = data.get("tech")
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        if not isinstance(raw_values, list):
            continue
        for t in raw_values:
            s = str(t).strip()
            if s:
                techs.append(s)
    seen = set()
    out = []
    for t in techs:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _parse_docker_image_candidates(raw_text: str) -> list[str]:
    content = (raw_text or "").strip()
    if not content:
        return []
    parsed = None
    try:
        parsed = json.loads(content)
    except Exception:
        pass
    if parsed is None:
        m = re.search(r"\[[\s\S]*\]", content)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                parsed = None
    candidates = []
    if isinstance(parsed, dict):
        maybe = parsed.get("images")
        if isinstance(maybe, list):
            candidates = maybe
    elif isinstance(parsed, list):
        candidates = parsed
    cleaned = []
    for item in candidates:
        s = str(item).strip().strip("`").strip('"').strip("'")
        if not s or " " in s:
            continue
        cleaned.append(s)
    uniq = []
    seen = set()
    for s in cleaned:
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
        if len(uniq) >= 10:
            break
    if len(uniq) < 10:
        for fallback in DEFAULT_DOCKER_IMAGE_SUGGESTIONS:
            key = fallback.lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(fallback)
            if len(uniq) >= 10:
                break
    return uniq[:10]


def _generate_docker_image_options_from_graph(project_id, nodes):
    technologies = _extract_node_technologies(nodes)
    if not technologies:
        _set_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, [])
        db.session.commit()
        return [], None

    llm_url = (get_setting_or_env("FRONTEND_LLM_URL") or "").strip()
    llm_model = (get_setting_or_env("FRONTEND_LLM_MODEL") or "").strip()
    llm_api_key = (get_setting_or_env("FRONTEND_LLM_API_KEY") or "").strip()
    chat_url = _normalize_frontend_llm_chat_url(llm_url)
    if not chat_url or not llm_model:
        msg = "Set FRONTEND_LLM_URL and FRONTEND_LLM_MODEL in Settings to auto-generate Docker image options."
        current_app.logger.info("Docker image suggestion skipped for project %s: %s", project_id, msg)
        _set_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, [])
        db.session.commit()
        return [], msg

    system_prompt = (
        "You are selecting Docker base images for development workspaces. "
        "If your runtime supports web search/browsing tools, use them to check current official image names/tags. "
        "If web search is unavailable, proceed using your internal knowledge. "
        "Return ONLY valid JSON: an array of exactly 10 Docker image references (strings, include tags). "
        "No markdown, no comments."
    )
    user_prompt = (
        "Project technologies extracted from the architecture graph:\n"
        f"{json.dumps(technologies)}\n\n"
        "Generate 10 practical Docker images for coding/build/test workflows for this stack. "
        "Prioritize broadly useful official images and keep each item as a plain image reference with tag. "
        "Prefer stable, current tags (or widely adopted LTS tags) and avoid obscure/unofficial images."
    )
    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"

    try:
        resp = requests.post(chat_url, json=payload, headers=headers, timeout=45)
        if resp.status_code >= 400:
            msg = f"Frontend LLM request failed ({resp.status_code})."
            current_app.logger.warning("Docker image suggestion failed for project %s: %s", project_id, msg)
            _set_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, [])
            db.session.commit()
            return [], msg
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        images = _parse_docker_image_candidates(content)
        if not images:
            msg = "Frontend LLM returned no usable Docker image suggestions."
            current_app.logger.warning("Docker image suggestion parse failed for project %s", project_id)
            _set_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, [])
            db.session.commit()
            return [], msg
        _set_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, images)
        db.session.commit()
        return images, None
    except Exception as e:
        msg = f"Frontend LLM error: {e}"
        current_app.logger.warning("Docker image suggestion error for project %s: %s", project_id, e)
        _set_project_setting(project_id, PROJECT_SETTING_DOCKER_IMAGE_OPTIONS, [])
        db.session.commit()
        return [], msg


@api_bp.route("/projects", methods=["GET", "POST"])
def projects():
    """List all projects or create a new one."""
    if request.method == "GET":
        projects = Project.query.all()
        return jsonify([_project_to_json(p) for p in projects])

    if request.method == "POST":
        data = request.json
        project = Project(
            name=data.get("name", "Untitled Project"),
            description=data.get("description"),
            project_path=data.get("project_path"),
            github_url=data.get("github_url"),
        )
        db.session.add(project)
        db.session.commit()

        # Initialize graph and kanban board for new project
        graph = Graph(project_id=project.id)
        default_columns = [
            {"id": "backlog", "title": "Backlog", "order": 0},
            {"id": "in_progress", "title": "In Progress", "order": 1},
            {"id": "in_review", "title": "In Review", "order": 2},
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
        project.project_path = data.get("project_path", project.project_path)
        project.github_url = data.get("github_url", project.github_url)
        if "docker_image" in data:
            docker_image = (data.get("docker_image") or "").strip()
            _set_project_setting(
                project.id,
                PROJECT_SETTING_DOCKER_IMAGE,
                docker_image if docker_image else None,
            )
        if "dockerfile" in data:
            raw = data.get("dockerfile")
            dockerfile = (raw if isinstance(raw, str) else "").strip() or None
            _set_project_setting(project.id, PROJECT_SETTING_DOCKERFILE, dockerfile)
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


@api_bp.route("/projects/<uuid:project_id>/generate-dockerfile", methods=["POST"])
def generate_project_dockerfile(project_id):
    """Generate a Dockerfile from the project repo (GitHub) and store it. Returns the generated dockerfile or an error."""
    try:
        Project.query.get_or_404(project_id)
        dockerfile, err = _generate_dockerfile_from_repo(project_id)
        if err:
            return jsonify({"dockerfile": None, "error": err}), 400
        return jsonify({"dockerfile": dockerfile, "error": None})
    except Exception as e:
        current_app.logger.exception("Generate Dockerfile failed for project %s", project_id)
        return jsonify({"dockerfile": None, "error": f"Server error: {e}"}), 500


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
        current_source_ids = set()
        nodes = graph.nodes if graph.nodes else []
        edges = graph.edges if graph.edges else []
        for node in nodes:
            nid = node.get("id") or node.get("data", {}).get("id")
            if nid is not None:
                current_source_ids.add(uuid5(NAMESPACE_DNS, f"node:{nid}"))
        for edge in edges:
            eid = edge.get("id") or edge.get("data", {}).get("id")
            if eid is not None:
                current_source_ids.add(uuid5(NAMESPACE_DNS, f"edge:{eid}"))
        q = RAGEmbedding.query.filter(
            RAGEmbedding.project_id == project_id,
            RAGEmbedding.source_type.in_(["node", "edge"]),
        )
        if current_source_ids:
            q = q.filter(~RAGEmbedding.source_id.in_(list(current_source_ids)))
        q.delete(synchronize_session=False)
        db.session.commit()
        for node in nodes:
            nid = node.get("id") or node.get("data", {}).get("id")
            if nid is None:
                continue
            label = node.get("data", {}).get("label") or node.get("label") or ""
            ntype = node.get("type") or node.get("data", {}).get("type") or ""
            content = f"{ntype} {label}".strip() or str(nid)
            upsert_embedding(project_id, "node", uuid5(NAMESPACE_DNS, f"node:{nid}"), content)
        for edge in edges:
            eid = edge.get("id") or edge.get("data", {}).get("id")
            if eid is None:
                continue
            src = edge.get("source") or edge.get("data", {}).get("source") or ""
            tgt = edge.get("target") or edge.get("data", {}).get("target") or ""
            label = edge.get("data", {}).get("label") or edge.get("label") or ""
            content = (f"{src} -> {tgt}" + (f" {label}" if label else "")).strip() or str(eid)
            upsert_embedding(project_id, "edge", uuid5(NAMESPACE_DNS, f"edge:{eid}"), content)

        docker_image_options, docker_image_suggestions_error = _generate_docker_image_options_from_graph(project_id, nodes)
        return jsonify({
            "version": graph.version,
            "docker_image_options": docker_image_options,
            "docker_image_suggestions_error": docker_image_suggestions_error,
        })


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
        )
        db.session.add(ticket)
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        return jsonify(_ticket_to_json(ticket)), 201


def _trigger_middle_agent(ticket_id):
    """Enqueue Middle Agent for the ticket. Skip if this ticket is already queued or in progress."""
    import sys
    key = _agent_job_key("ticket", (ticket_id,))
    with _agent_queue_lock:
        if key in _agent_queue_keys:
            current_app.logger.info("Skipping enqueue: ticket %s already queued or in progress", ticket_id)
            return
        _agent_queue_keys.add(key)
    print(f"[Terarchitect] Middle Agent enqueued for ticket {ticket_id}", file=sys.stderr, flush=True)
    current_app.logger.info("Enqueuing Middle Agent for ticket %s", ticket_id)
    _agent_queue.put(("ticket", (ticket_id,)))


def _run_one_agent_job(app, kind, args):
    """Run a single agent job with app context. Caller holds no lock."""
    key = _agent_job_key(kind, args)
    if kind == "review":
        key = _agent_job_key("review", (args[0], args[1], args[2], args[3], args[4]))
    try:
        # Serialize all agent executions globally in-process.
        with _agent_run_lock:
            with app.app_context():
                try:
                    from middle_agent.agent import MiddleAgent, AgentAPIError
                except ImportError as e:
                    app.logger.exception("Middle Agent import failed: %s", e)
                    return
                try:
                    agent = MiddleAgent()
                    if kind == "ticket":
                        agent.process_ticket(args[0])
                    elif kind == "review":
                        agent.process_ticket_review(args[0], args[1], args[2])
                except AgentAPIError as e:
                    app.logger.error("Agent API error: %s", e, exc_info=True)
                except Exception as e:
                    app.logger.exception("Agent failed: %s", e)
        # After review job finishes, mark the comment as addressed so we don't reprocess it.
        if kind == "review" and len(args) >= 5:
            with app.app_context():
                _mark_pr_comment_addressed(args[3], args[2], args[4])
    finally:
        if key is not None:
            with _agent_queue_lock:
                _agent_queue_keys.discard(key)


def _run_agent_and_poll_loop(app, queue_poll_seconds=10, pr_poll_seconds=60):
    """
    Single background thread: every queue_poll_seconds check the queue and run at most one
    agent job; every pr_poll_seconds run the PR comment poll. No blocking get(), no race.
    """
    last_pr_poll = 0.0
    while True:
        time.sleep(queue_poll_seconds)
        now = time.monotonic()
        if now - last_pr_poll >= pr_poll_seconds:
            last_pr_poll = now
            try:
                with app.app_context():
                    _poll_pr_review_comments()
            except Exception as e:
                if app:
                    app.logger.exception("PR review poller error: %s", e)
        try:
            kind, args = _agent_queue.get_nowait()
        except queue.Empty:
            continue
        _run_one_agent_job(app, kind, args)


def _ticket_to_json(t):
    out = {
        "id": str(t.id),
        "project_id": str(t.project_id),
        "column_id": str(t.column_id),
        "title": t.title,
        "description": t.description,
        "associated_node_ids": t.associated_node_ids,
        "associated_edge_ids": t.associated_edge_ids,
        "priority": t.priority,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if t.pr:
        out["pr_url"] = t.pr.pr_url
        out["pr_number"] = t.pr.pr_number
    else:
        out["pr_url"] = None
        out["pr_number"] = None
    return out


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
            graph = Graph.query.filter_by(project_id=project_id).first()
            if not graph or not graph.nodes or len(graph.nodes) == 0:
                return jsonify({
                    "error": "Add at least one node to the graph before moving a ticket to In Progress.",
                }), 400
            docker_image, _ = _project_docker_config(project_id)
            dockerfile = _get_project_setting(project_id, PROJECT_SETTING_DOCKERFILE)
            has_dockerfile = isinstance(dockerfile, str) and (dockerfile or "").strip()
            if not docker_image and not has_dockerfile:
                return jsonify({
                    "error": "Select a Docker image or generate a project Dockerfile in the project settings before moving a ticket to In Progress.",
                }), 400
        if "column_id" in data:
            ticket.column_id = data["column_id"]
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
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        if moved_to_in_progress:
            _trigger_middle_agent(ticket.id)
        return jsonify(_ticket_to_json(ticket))

    if request.method == "DELETE":
        for c in ticket.comments:
            delete_embeddings_for_source(project_id, "ticket_comment", c.id)
        delete_embeddings_for_source(project_id, "ticket", ticket.id)
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
    out = []
    for pr_row, ticket in prs:
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
        out.append({
            "id": str(ticket.id),
            "title": ticket.title,
            "pr_url": pr_row.pr_url,
            "pr_number": pr_row.pr_number,
            "pr_state": pr_state,
            "merged": merged,
            "_sort_ts": ts,
        })
    # Exclude closed PRs that were not merged (e.g. abandoned or closed without merge)
    out = [x for x in out if not (x["pr_state"] == "closed" and not x["merged"])]
    # Pending (open) first, then by most recent
    out.sort(key=lambda x: (x["merged"], -x["_sort_ts"]))
    for item in out:
        del item["_sort_ts"]
    return jsonify(out[:20])


def _get_ticket_pr_slug(project_id, ticket_id):
    """Return (pr_row, slug) for ticket's PR, or (None, None). 404 if ticket/project missing."""
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    project = Project.query.get_or_404(project_id)
    pr_row = PR.query.filter_by(ticket_id=ticket.id).first()
    if not pr_row or not pr_row.pr_number:
        return None, None
    slug = _repo_slug_from_github_url(project.github_url)
    return pr_row, slug


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
    """Request cancellation of the Middle Agent execution for a ticket."""
    ticket = Ticket.query.filter_by(project_id=project_id, id=ticket_id).first_or_404()
    try:
        from middle_agent.agent import cancel_ticket_execution as cancel_fn
    except ImportError as e:
        current_app.logger.exception("Middle Agent import failed for cancel: %s", e)
        return jsonify({"error": "Middle Agent not available"}), 500

    cancelled = cancel_fn(ticket.id)
    if cancelled:
        return jsonify({"message": "Cancellation requested"}), 200
    return jsonify({"message": "No active execution for this ticket"}), 202


@api_bp.route("/settings", methods=["GET"])
def app_settings_get():
    """Return all app settings: sensitive keys as bool (is set), plain keys as value or null."""
    return jsonify(get_all_for_api())


@api_bp.route("/settings", methods=["PUT"])
def app_settings_put():
    """Update app settings. Body: any of ALLOWED_KEYS. Omit = no change, empty string = clear. Sensitive keys require TERARCHITECT_SECRET_KEY."""
    import sys
    try:
        data = request.json or {}
        print("[DEBUG] settings PUT keys in body:", list(data.keys()), file=sys.stderr, flush=True)
        for key in ALLOWED_KEYS:
            if key not in data:
                continue
            val = data[key]
            print(f"[DEBUG] processing key={key!r} val type={type(val).__name__} len={len(str(val)) if val else 0}", file=sys.stderr, flush=True)
            if val is None or (isinstance(val, str) and not val.strip()):
                delete_key(key)
                print(f"[DEBUG] deleted key {key}", file=sys.stderr, flush=True)
            else:
                plain = val if isinstance(val, str) else str(val)
                if key in SENSITIVE_KEYS and not is_encryption_available():
                    print("[DEBUG] 503: encryption not available for sensitive key", file=sys.stderr, flush=True)
                    return jsonify({
                        "error": (
                            "TERARCHITECT_SECRET_KEY must be a 64-character hex string in .env to store secrets. "
                            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
                        )
                    }), 503
                ok = set_value(key, plain)
                print(f"[DEBUG] set_value({key!r}) -> {ok}", file=sys.stderr, flush=True)
                if not ok:
                    return jsonify({"error": f"Failed to save {key}"}), 500
        print("[DEBUG] calling get_all_for_api()", file=sys.stderr, flush=True)
        out = get_all_for_api()
        print("[DEBUG] settings PUT success", file=sys.stderr, flush=True)
        return jsonify(out)
    except Exception as e:
        print(f"[DEBUG] settings PUT exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        import traceback
        traceback.print_exc(file=sys.stderr)
        current_app.logger.exception("Settings PUT failed: %s", e)
        return jsonify({"error": str(e)}), 500


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


def _split_note_link_ids(raw):
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _join_note_link_ids(values):
    ids = _split_note_link_ids(values)
    if not ids:
        return None
    # Preserve order while de-duplicating.
    return ",".join(dict.fromkeys(ids))


def _note_to_json(n):
    return {
        "id": str(n.id),
        "project_id": str(n.project_id),
        "node_ids": _split_note_link_ids(n.node_id),
        "edge_ids": _split_note_link_ids(n.edge_id),
        "title": n.title,
        "content": n.content,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


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
    limit = min(int(data.get("limit", 5)), 50)
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
    """Index documents into project memory (HippoRAG). Locked per project."""
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
    """Retrieve relevant passages for queries (HippoRAG). Locked per project."""
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
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 503
    except Exception as e:
        current_app.logger.exception("Memory retrieve failed")
        return jsonify({"error": "Retrieve failed", "detail": str(e)}), 500
    return jsonify({"results": results})


@api_bp.route("/projects/<uuid:project_id>/memory/delete", methods=["POST"])
def memory_delete(project_id):
    """Remove documents from project memory (HippoRAG). Locked per project."""
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


def _is_test_file(path):
    """True if path looks like a test file (by convention). Excludes __init__.py (package marker)."""
    if not path:
        return False
    path_norm = path.replace("\\", "/")
    path_lower = path_norm.lower()
    base = path_norm.split("/")[-1] if "/" in path_norm else path_norm
    base_lower = base.lower()
    if base_lower == "__init__.py":
        return False
    return (
        "__tests__" in path_lower
        or "/tests/" in path_lower
        or path_lower.endswith("_test.py")
        or (base_lower.startswith("test_") and base_lower.endswith(".py"))
        or path_lower.endswith("_test.go")
        or path_lower.endswith("_test.js")
        or ".test." in path_lower
        or ".spec." in path_lower
        or path_lower.endswith(".test.js")
        or path_lower.endswith(".test.jsx")
        or path_lower.endswith(".test.ts")
        or path_lower.endswith(".test.tsx")
        or path_lower.endswith(".spec.js")
        or path_lower.endswith(".spec.jsx")
    )


def _extract_test_names_from_patch(patch):
    """From a unified diff patch, extract test/spec names from added lines. Returns list of unique strings."""
    if not patch:
        return []
    seen = set()
    out = []
    # Match it('...'), it("..."), test('...'), test("..."), describe('...')
    for m in re.finditer(
        r"""(?:it|test|describe)\s*\(\s*['"`]([^'"`]+)['"`]""",
        patch,
        re.IGNORECASE,
    ):
        name = m.group(1).strip()
        if name and name not in seen and len(name) < 200:
            seen.add(name)
            out.append(name)
    # Match def test_something(
    for m in re.finditer(r"^\+\s*def\s+(test_\w+)\s*\(", patch, re.MULTILINE):
        name = m.group(1).strip()
        name = name.replace("_", " ").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _repo_slug_from_github_url(url):
    """Extract owner/repo from https://github.com/owner/repo or similar. Returns None if not parseable."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip().rstrip("/")
    if "github.com" not in url:
        return None
    path = url.split("github.com")[-1].strip("/")
    parts = path.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else None


# Files we want to send to the LLM for Dockerfile generation (root only or known paths).
_DOCKERFILE_CONTEXT_FILES = {
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "requirements.txt",
    "Pipfile",
    "Pipfile.lock",
    "pyproject.toml",
    "go.mod",
    "go.sum",
    "Cargo.toml",
    "Cargo.lock",
}
_MAX_FILE_CHARS = 8000
_MAX_TOTAL_CONTEXT_CHARS = 45000


def _get_github_token_for_api():
    """Return a GitHub token for API calls (user or agent)."""
    return (
        (get_setting_or_env("github_user_token") or "").strip()
        or (get_setting_or_env("github_agent_token") or "").strip()
    ) or None


def _fetch_repo_files_for_dockerfile(github_url, token):
    """
    Fetch relevant repo files via GitHub API. Returns (files_dict, None) or (None, error_msg).
    files_dict: path -> content (decoded, truncated per file and total).
    """
    slug = _repo_slug_from_github_url(github_url)
    if not slug:
        return None, "Invalid GitHub URL (expected https://github.com/owner/repo)."
    if not token:
        return None, "Configure a GitHub token (Settings) to read the repository."

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "Authorization": f"Bearer {token}",
    }
    api_base = "https://api.github.com/repos/" + slug

    # Get default branch
    try:
        repo_resp = requests.get(api_base, headers=headers, timeout=15)
        if repo_resp.status_code == 404:
            return None, "Repository not found or no access."
        if repo_resp.status_code >= 400:
            return None, f"GitHub API error: {repo_resp.status_code}"
        repo_data = repo_resp.json()
        ref = repo_data.get("default_branch") or "main"
    except requests.RequestException as e:
        return None, f"Failed to reach GitHub: {e}"

    # List root contents
    try:
        contents_resp = requests.get(
            f"{api_base}/contents",
            headers=headers,
            params={"ref": ref},
            timeout=15,
        )
        if contents_resp.status_code >= 400:
            return None, f"Failed to list repo: {contents_resp.status_code}"
        root_items = contents_resp.json()
        if not isinstance(root_items, list):
            return None, "Unexpected repo contents response."
    except requests.RequestException as e:
        return None, f"Failed to list repo: {e}"

    # Collect paths to fetch: root files in our set + any path containing "Dockerfile"
    paths_to_fetch = []
    for item in root_items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        path = item.get("path") or name
        if item.get("type") == "file" and (
            name in _DOCKERFILE_CONTEXT_FILES or "dockerfile" in name.lower()
        ):
            paths_to_fetch.append(path)
        if item.get("type") == "dir" and name.lower() == "docker":
            paths_to_fetch.append(path)  # We'll list this dir next

    # List docker/ if present and add any Dockerfile* there
    for path in list(paths_to_fetch):
        if path == "docker" or path.startswith("docker/"):
            try:
                dir_resp = requests.get(
                    f"{api_base}/contents/{path}",
                    headers=headers,
                    params={"ref": ref},
                    timeout=15,
                )
                if dir_resp.status_code == 200:
                    dir_items = dir_resp.json()
                    if isinstance(dir_items, list):
                        for d in dir_items:
                            if isinstance(d, dict) and (d.get("name") or "").lower().startswith("dockerfile"):
                                subpath = (d.get("path") or d.get("name")).strip()
                                if subpath and subpath not in paths_to_fetch:
                                    paths_to_fetch.append(subpath)
            except requests.RequestException:
                pass
            if path == "docker":
                paths_to_fetch.remove("docker")  # Don't fetch dir as file

    # Fetch each file (base64 decode, truncate)
    files = {}
    total = 0
    for path in paths_to_fetch:
        if total >= _MAX_TOTAL_CONTEXT_CHARS:
            break
        try:
            file_resp = requests.get(
                f"{api_base}/contents/{path}",
                headers=headers,
                params={"ref": ref},
                timeout=15,
            )
            if file_resp.status_code != 200:
                continue
            data = file_resp.json()
            if not isinstance(data, dict) or data.get("type") != "file":
                continue
            b64 = data.get("content")
            if not b64:
                continue
            try:
                raw = base64.b64decode(b64).decode("utf-8", errors="replace")
            except Exception:
                continue
            if len(raw) > _MAX_FILE_CHARS:
                raw = raw[:_MAX_FILE_CHARS] + "\n... (truncated)"
            if total + len(raw) > _MAX_TOTAL_CONTEXT_CHARS:
                raw = raw[: _MAX_TOTAL_CONTEXT_CHARS - total] + "\n... (truncated)"
            files[path] = raw
            total += len(raw)
        except requests.RequestException:
            continue

    if not files:
        return None, "No relevant files found in the repository root (e.g. package.json, requirements.txt, Dockerfile)."
    return files, None


def _generate_dockerfile_from_repo(project_id):
    """
    Fetch repo files via GitHub, call frontend LLM to generate a Dockerfile, store in project Setting.
    Returns (dockerfile_string, error_string). On success error_string is None.
    """
    project = Project.query.get(project_id)
    if not project or not (project.github_url or "").strip():
        return None, "Project has no GitHub URL set."

    token = _get_github_token_for_api()
    files, err = _fetch_repo_files_for_dockerfile((project.github_url or "").strip(), token)
    if err:
        return None, err

    # Optional: graph technologies as hint
    graph = Graph.query.filter_by(project_id=project_id).first()
    tech_hint = ""
    if graph and graph.nodes:
        techs = _extract_node_technologies(graph.nodes)
        if techs:
            tech_hint = f"\nProject technologies (from graph): {json.dumps(techs)}."

    # Build context text for the LLM
    context_parts = []
    for path, content in sorted(files.items()):
        context_parts.append(f"--- {path} ---\n{content}")
    context_body = "\n\n".join(context_parts)

    llm_url = (get_setting_or_env("FRONTEND_LLM_URL") or "").strip()
    llm_model = (get_setting_or_env("FRONTEND_LLM_MODEL") or "").strip()
    llm_api_key = (get_setting_or_env("FRONTEND_LLM_API_KEY") or "").strip()
    chat_url = _normalize_frontend_llm_chat_url(llm_url)
    if not chat_url or not llm_model:
        return None, "Set FRONTEND_LLM_URL and FRONTEND_LLM_MODEL in Settings to generate a Dockerfile."

    system_prompt = (
        "You are generating a single Dockerfile for a development/build workspace. "
        "Use the provided project files (and any existing Dockerfiles in the repo) to infer runtimes and dependencies. "
        "Include all runtimes needed in one image (e.g. Node and Python) so the agent does not reinstall them every run. "
        "Output only the raw Dockerfile contents. No markdown code fence, no explanation before or after."
    )
    user_prompt = (
        "Project files from the repository:\n\n"
        f"{context_body}\n\n"
        f"{tech_hint}\n\n"
        "Generate one Dockerfile that can build and run this project. Use multi-stage builds if helpful. "
        "Output only the Dockerfile, nothing else."
    )

    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json"}
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"

    try:
        resp = requests.post(chat_url, json=payload, headers=headers, timeout=90)
        if resp.status_code >= 400:
            return None, f"LLM request failed: {resp.status_code}"
        data = resp.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        # Strip markdown code block if present
        content = (content or "").strip()
        if content.startswith("```"):
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            content = "\n".join(lines)
            if content.endswith("```"):
                content = content[:-3].rstrip()
        content = content.strip()
        if not content:
            return None, "LLM returned an empty Dockerfile."
        _set_project_setting(project_id, PROJECT_SETTING_DOCKERFILE, content)
        db.session.commit()
        return content, None
    except requests.RequestException as e:
        return None, f"LLM request error: {e}"
    except Exception as e:
        current_app.logger.exception("Dockerfile generation failed for project %s", project_id)
        return None, str(e)


def _trigger_review_agent(ticket_id, comment_body, pr_number, project_id, github_comment_id):
    """Enqueue Middle Agent in PR review mode. Skip if this ticket+PR is already queued or in progress."""
    import sys
    key = _agent_job_key("review", (ticket_id, comment_body, pr_number, project_id, github_comment_id))
    with _agent_queue_lock:
        if key in _agent_queue_keys:
            current_app.logger.info("Skipping enqueue: ticket %s PR #%s already queued or in progress", ticket_id, pr_number)
            return
        _agent_queue_keys.add(key)
    print(f"[Terarchitect] PR review agent enqueued for ticket {ticket_id} PR #{pr_number} comment {github_comment_id}", file=sys.stderr, flush=True)
    current_app.logger.info("Enqueuing PR review agent for ticket %s PR #%s comment %s", ticket_id, pr_number, github_comment_id)
    _agent_queue.put(("review", (ticket_id, comment_body, pr_number, project_id, github_comment_id)))


def _mark_pr_comment_addressed(project_id, pr_number, github_comment_id):
    """Mark a PR comment as addressed (we replied). Call with app context."""
    row = PRReviewComment.query.filter_by(
        project_id=project_id,
        pr_number=pr_number,
        github_comment_id=github_comment_id,
    ).first()
    if row:
        from datetime import datetime
        row.addressed_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        try:
            db.session.commit()
            current_app.logger.info("Marked PR comment %s (PR #%s) as addressed", github_comment_id, pr_number)
        except Exception:
            db.session.rollback()


def _gh_current_login():
    """Return the login of the agent's GitHub user (same token used for PRs and PR comments). We ignore comments from this user to avoid replying to ourselves."""
    try:
        env = {**os.environ, **get_gh_env_for_agent()}
        r = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _poll_pr_review_comments():
    """Check PRs in review for new comments via gh CLI and trigger review agent for new ones. Call with app context."""
    repo_slug = _repo_slug_from_github_url
    gh_login = _gh_current_login()
    # Tickets in_review with a PR
    prs_in_review = list(
        db.session.query(PR, Ticket, Project)
        .join(Ticket, Ticket.id == PR.ticket_id)
        .join(Project, Project.id == PR.project_id)
        .filter(Ticket.column_id == "in_review")
        .filter(Project.github_url.isnot(None))
        .filter(PR.pr_number.isnot(None))
        .all()
    )
    if prs_in_review:
        current_app.logger.info("PR review poll: checking %d PR(s) for new comments", len(prs_in_review))
    for pr_row, ticket, project in prs_in_review:
        slug = repo_slug(project.github_url)
        if not slug:
            continue
        pr_number = pr_row.pr_number

        # Check if PR was merged -> move ticket to done
        try:
            r_pr = subprocess.run(
                ["gh", "api", f"repos/{slug}/pulls/{pr_number}"],
                capture_output=True,
                text=True,
                timeout=15,
                env=_env_for_gh_user(),
            )
            if r_pr.returncode == 0 and r_pr.stdout:
                pr_data = json.loads(r_pr.stdout)
                if pr_data.get("merged"):
                    ticket.column_id = "done"
                    ticket.status = "completed"
                    try:
                        db.session.commit()
                        current_app.logger.info(
                            "PR #%s merged; moved ticket %s to done",
                            pr_number,
                            ticket.id,
                        )
                    except Exception:
                        db.session.rollback()
                    continue  # Skip approval check and comment processing
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        # Check if PR was approved (latest review is APPROVED) -> move ticket to done
        try:
            r_reviews = subprocess.run(
                ["gh", "api", f"repos/{slug}/pulls/{pr_number}/reviews", "--paginate"],
                capture_output=True,
                text=True,
                timeout=15,
                env=_env_for_gh_user(),
            )
            if r_reviews.returncode == 0 and r_reviews.stdout:
                reviews = json.loads(r_reviews.stdout) if r_reviews.stdout else []
                if isinstance(reviews, list) and reviews:
                    # API returns in chronological order; last is most recent
                    latest = reviews[-1]
                    if latest.get("state") == "APPROVED":
                        ticket.column_id = "done"
                        ticket.status = "completed"
                        try:
                            db.session.commit()
                            current_app.logger.info(
                                "PR #%s approved; moved ticket %s to done",
                                pr_number,
                                ticket.id,
                            )
                        except Exception:
                            db.session.rollback()
                        continue  # Skip comment processing for this PR
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        # Issue comments, line (review) comments, and PR review submissions (e.g. "Submit review" with body)
        raw_comments = []
        for endpoint in (
            f"repos/{slug}/issues/{pr_number}/comments",
            f"repos/{slug}/pulls/{pr_number}/comments",
            f"repos/{slug}/pulls/{pr_number}/reviews",
        ):
            try:
                r = subprocess.run(
                    ["gh", "api", endpoint, "--paginate"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=_env_for_gh_user(),
                )
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                current_app.logger.warning("PR poll gh api failed %s: %s", endpoint, e)
                continue
            if r.returncode != 0:
                current_app.logger.warning(
                    "PR poll gh api non-zero %s: code=%s stderr=%s",
                    endpoint, r.returncode, (r.stderr or "").strip()[:200],
                )
                continue
            try:
                chunk = json.loads(r.stdout) if r.stdout else []
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, list):
                raw_comments.extend(chunk)
        # Normalize and upsert into pr_review_comments (id, body, author_login, created_at)
        from datetime import datetime as _dt
        for c in raw_comments:
            cid = c.get("id")
            body = (c.get("body") or "").strip()
            if cid is None or not body:
                continue
            author = (c.get("user") or {}).get("login")
            created = c.get("created_at") or c.get("submitted_at")
            try:
                comment_ts = _dt.fromisoformat(created.replace("Z", "+00:00")) if created else None
            except (ValueError, TypeError):
                comment_ts = None
            row = PRReviewComment.query.filter_by(
                project_id=project.id,
                pr_number=pr_number,
                github_comment_id=int(cid),
            ).first()
            if row:
                row.body = body
                row.author_login = author
                row.comment_created_at = comment_ts
                row.updated_at = _dt.utcnow()
            else:
                db.session.add(PRReviewComment(
                    project_id=project.id,
                    ticket_id=ticket.id,
                    pr_number=pr_number,
                    github_comment_id=int(cid),
                    author_login=author,
                    body=body,
                    comment_created_at=comment_ts,
                ))
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            continue
        # Mark our own (bot) comments as addressed so we never respond to ourselves. If we don't
        # know gh_login we can't tell; skip triggering for this PR to avoid a self-reply loop.
        if gh_login:
            our_comments = PRReviewComment.query.filter_by(
                project_id=project.id,
                pr_number=pr_number,
                author_login=gh_login,
            ).filter(PRReviewComment.addressed_at.is_(None)).all()
            for row in our_comments:
                row.addressed_at = _dt.utcnow()
                row.updated_at = _dt.utcnow()
            if our_comments:
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
        else:
            current_app.logger.info(
                "PR poll: gh_login unknown for %s PR #%s, skipping trigger to avoid replying to own comments",
                project.id, pr_number,
            )
        # Trigger only for the single most recent unaddressed comment from a human (not our bot)
        q = (
            PRReviewComment.query.filter_by(project_id=project.id, pr_number=pr_number)
            .filter(PRReviewComment.addressed_at.is_(None))
            .filter(PRReviewComment.body.isnot(None))
            .filter(PRReviewComment.body != "")
        )
        if gh_login:
            q = q.filter(db.or_(PRReviewComment.author_login.is_(None), PRReviewComment.author_login != gh_login))
        next_comment = q.order_by(nullslast(PRReviewComment.comment_created_at.desc())).limit(1).first()
        if next_comment and gh_login:
            _trigger_review_agent(
                ticket.id,
                next_comment.body,
                pr_number,
                project.id,
                next_comment.github_comment_id,
            )


def _run_pr_review_poller(app, interval_seconds=600):
    """Background loop: every interval_seconds run _poll_pr_review_comments with app context."""
    while True:
        time.sleep(interval_seconds)
        try:
            with app.app_context():
                _poll_pr_review_comments()
        except Exception as e:
            if app:
                app.logger.exception("PR review poller error: %s", e)
