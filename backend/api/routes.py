"""
API Routes for Terarchitect
"""
from flask import Blueprint, jsonify, request
from ..models.db import db, Project, Graph, KanbanBoard, Ticket, Note, Setting, RAGEmbedding

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
            "git_repo_path": p.git_repo_path,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        } for p in projects])

    if request.method == "POST":
        data = request.json
        project = Project(
            name=data.get("name", "Untitled Project"),
            description=data.get("description"),
            git_repo_path=data.get("git_repo_path"),
        )
        db.session.add(project)
        db.session.commit()

        # Initialize graph and kanban board for new project
        graph = Graph(project_id=project.id)
        kanban_board = KanbanBoard(project_id=project.id)
        db.session.add(graph)
        db.session.add(kanban_board)
        db.session.commit()

        return jsonify({
            "id": str(project.id),
            "name": project.name,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
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
            "git_repo_path": project.git_repo_path,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        })

    if request.method == "PUT":
        data = request.json
        project.name = data.get("name", project.name)
        project.description = data.get("description", project.description)
        project.git_repo_path = data.get("git_repo_path", project.git_repo_path)
        db.session.commit()
        return jsonify({"id": str(project.id), "name": project.name})

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
            "nodes": graph.nodes,
            "edges": graph.edges,
            "version": graph.version,
        })

    if request.method == "PUT":
        data = request.json
        graph.nodes = data.get("nodes", graph.nodes)
        graph.edges = data.get("edges", graph.edges)
        graph.version = graph.version + 1
        db.session.commit()
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
        kanban.columns = data.get("columns", kanban.columns)
        db.session.commit()
        return jsonify({"columns": kanban.columns})


@api_bp.route("/projects/<uuid:project_id>/tickets", methods=["GET", "POST"])
def tickets(project_id):
    """List tickets or create a new one."""
    if request.method == "GET":
        tickets = Ticket.query.filter_by(project_id=project_id).all()
        return jsonify([{
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
        } for t in tickets])

    if request.method == "POST":
        data = request.json
        ticket = Ticket(
            project_id=project_id,
            column_id=data.get("column_id"),
            title=data.get("title"),
            description=data.get("description"),
            associated_node_ids=data.get("associated_node_ids", []),
            associated_edge_ids=data.get("associated_edge_ids", []),
            priority=data.get("priority", "medium"),
            status=data.get("status", "todo"),
        )
        db.session.add(ticket)
        db.session.commit()
        return jsonify({"id": str(ticket.id)}), 201


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
        return jsonify({"id": str(note.id)}), 201


@api_bp.route("/rag/search", methods=["POST"])
def rag_search():
    """Search embeddings using vector similarity."""
    data = request.json
    project_id = data.get("project_id")
    query = data.get("query")
    limit = data.get("limit", 5)
    source_types = data.get("source_types", ["node", "edge", "note", "ticket", "ticket_comment"])

    if not query:
        return jsonify({"error": "Query is required"}), 400

    # This would use pgvector's <-> operator for similarity search
    # Implementation would generate embedding for query and find similar entries
    return jsonify({"message": "RAG search not yet implemented"})
