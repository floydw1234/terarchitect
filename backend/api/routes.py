"""
API Routes for Terarchitect
"""
import json
import os
import queue
import subprocess
import threading
import time
from uuid import UUID, uuid5, NAMESPACE_DNS

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text, nullslast

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, Setting, RAGEmbedding, ExecutionLog, PR, PRReviewComment
from utils.embedding_client import embed_single
from utils.rag import upsert_embedding, delete_embeddings_for_source

api_bp = Blueprint("api", __name__)

# Single agent queue: one worker runs jobs (ticket or review) one after another so only one agent touches the repo at a time.
_agent_queue = queue.Queue()
# Keys for jobs in queue or in progress; don't enqueue duplicate ticket/review work.
_agent_queue_keys = set()
_agent_queue_lock = threading.Lock()
# Hard execution mutex: only one agent run may execute at a time in this process.
_agent_run_lock = threading.Lock()


def _agent_job_key(kind, args):
    """Unique key for a queued or in-progress job. ticket -> ticket:id, review -> review:ticket_id:pr#."""
    if kind == "ticket":
        return f"ticket:{args[0]}"
    if kind == "review":
        # args: (ticket_id, comment_body, pr_number, project_id, github_comment_id)
        return f"review:{args[0]}:{args[2]}"
    return None


@api_bp.route("/projects", methods=["GET", "POST"])
def projects():
    """List all projects or create a new one."""
    if request.method == "GET":
        projects = Project.query.all()
        return jsonify([{
            "id": str(p.id),
            "name": p.name,
            "description": p.description,
            "project_path": p.project_path,
            "github_url": p.github_url,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        } for p in projects])

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

        return jsonify({
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        }), 201


@api_bp.route("/projects/<uuid:project_id>", methods=["GET", "PUT", "DELETE"])
def project_detail(project_id):
    """Get, update, or delete a project."""
    project = Project.query.get_or_404(project_id)

    if request.method == "GET":
        return jsonify({
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        })

    if request.method == "PUT":
        data = request.json
        project.name = data.get("name", project.name)
        project.description = data.get("description", project.description)
        project.project_path = data.get("project_path", project.project_path)
        project.github_url = data.get("github_url", project.github_url)
        db.session.commit()
        return jsonify({
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        })

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

        return jsonify({"version": graph.version})


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
    """Return the login of the bot/gh user whose comments we must ignore. Used to avoid replying to our own PR comments."""
    try:
        r = subprocess.run(
            ["gh", "api", "user", "-q", ".login"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    # Fallback when gh is not in path or not authenticated in this process (e.g. different user).
    return (os.environ.get("PR_BOT_GITHUB_LOGIN", "").strip() or None)


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
