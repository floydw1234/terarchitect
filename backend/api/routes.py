"""
API Routes for Terarchitect
"""
import threading
from uuid import UUID, uuid5, NAMESPACE_DNS

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, Setting, RAGEmbedding, ExecutionLog
from utils.embedding_client import embed_single
from utils.rag import upsert_embedding, delete_embeddings_for_source

api_bp = Blueprint("api", __name__)


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
            {"id": "done", "title": "Done", "order": 2},
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
    """Run Middle Agent for the ticket in a background thread (non-blocking)."""
    import sys
    print(f"[Terarchitect] Middle Agent triggered for ticket {ticket_id}", file=sys.stderr, flush=True)
    app = current_app._get_current_object()
    current_app.logger.info("Starting Middle Agent for ticket %s", ticket_id)

    def run():
        with app.app_context():
            try:
                from middle_agent.agent import MiddleAgent, AgentAPIError
            except ImportError as e:
                current_app.logger.exception("Middle Agent import failed: %s", e)
                return
            try:
                agent = MiddleAgent()
                agent.process_ticket(ticket_id)
            except AgentAPIError as e:
                current_app.logger.error("Middle Agent API error: %s", e, exc_info=True)
            except Exception as e:
                current_app.logger.exception("Middle Agent failed: %s", e)

    threading.Thread(target=run, daemon=True).start()


def _ticket_to_json(t):
    return {
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
        return jsonify([{
            "id": str(n.id),
            "project_id": str(n.project_id),
            "node_id": n.node_id,
            "edge_id": n.edge_id,
            "title": n.title,
            "content": n.content,
            "created_at": n.created_at.isoformat() if n.created_at else None,
        } for n in notes])

    if request.method == "POST":
        data = request.json
        note = Note(
            project_id=project_id,
            node_id=data.get("node_id"),
            edge_id=data.get("edge_id"),
            title=data.get("title"),
            content=data.get("content"),
        )
        db.session.add(note)
        db.session.commit()
        content = ((data.get("title") or "") + " " + (data.get("content") or "")).strip()
        if content:
            upsert_embedding(project_id, "note", note.id, content)
        return jsonify(_note_to_json(note)), 201


def _note_to_json(n):
    return {
        "id": str(n.id),
        "project_id": str(n.project_id),
        "node_id": n.node_id,
        "edge_id": n.edge_id,
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
        if "node_id" in data:
            note.node_id = data["node_id"]
        if "edge_id" in data:
            note.edge_id = data["edge_id"]
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
