"""
Database Models for Terarchitect
"""
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    git_repo_path = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    graphs = db.relationship("Graph", backref="project", uselist=False, cascade="all, delete-orphan")
    kanban_boards = db.relationship("KanbanBoard", backref="project", uselist=False, cascade="all, delete-orphan")
    tickets = db.relationship("Ticket", backref="project", cascade="all, delete-orphan")
    notes = db.relationship("Note", backref="project", cascade="all, delete-orphan")
    execution_logs = db.relationship("ExecutionLog", backref="project", cascade="all, delete-orphan")
    prs = db.relationship("PR", backref="project", cascade="all, delete-orphan")
    settings = db.relationship("Setting", backref="project", cascade="all, delete-orphan")
    rag_embeddings = db.relationship("RAGEmbedding", backref="project", cascade="all, delete-orphan")


class Graph(db.Model):
    __tablename__ = "graphs"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    nodes = db.Column(db.JSONB, default=lambda: [])
    edges = db.Column(db.JSONB, default=lambda: [])
    version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class KanbanBoard(db.Model):
    __tablename__ = "kanban_boards"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    columns = db.Column(db.JSONB, default=lambda: [{"id": "backlog", "title": "Backlog", "order": 0}])
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    column_id = db.Column(db.UUID, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    associated_node_ids = db.Column(db.JSONB, default=lambda: [])
    associated_edge_ids = db.Column(db.JSONB, default=lambda: [])
    priority = db.Column(db.String(50), default="medium")
    status = db.Column(db.String(50), default="todo")
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    comments = db.relationship("TicketComment", backref="ticket", cascade="all, delete-orphan")
    execution_logs = db.relationship("ExecutionLog", backref="ticket", cascade="all, delete-orphan")
    pr = db.relationship("PR", backref="ticket", uselist=False, cascade="all, delete-orphan")


class TicketComment(db.Model):
    __tablename__ = "ticket_comments"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    ticket_id = db.Column(db.UUID, db.ForeignKey("tickets.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_summary = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())


class Note(db.Model):
    __tablename__ = "notes"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    node_id = db.Column(db.Text)
    edge_id = db.Column(db.Text)
    title = db.Column(db.String(255))
    content = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class ExecutionLog(db.Model):
    __tablename__ = "execution_logs"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    ticket_id = db.Column(db.UUID, db.ForeignKey("tickets.id"))
    session_id = db.Column(db.String(255))
    step = db.Column(db.String(100))
    summary = db.Column(db.Text)
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    success = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())


class PR(db.Model):
    __tablename__ = "prs"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    ticket_id = db.Column(db.UUID, db.ForeignKey("tickets.id"))
    pr_number = db.Column(db.Integer)
    pr_url = db.Column(db.Text)
    commit_hash = db.Column(db.String(255))
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    key = db.Column(db.String(255), nullable=False)
    value = db.Column(db.JSONB)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (db.UniqueConstraint("project_id", "key", name="_project_setting_key"),)


class RAGEmbedding(db.Model):
    __tablename__ = "rag_embeddings"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    source_type = db.Column(db.String(50), nullable=False)  # "node", "edge", "note", "ticket", "ticket_comment"
    source_id = db.Column(db.UUID, nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(db.ARRAY(db.FLOAT), nullable=False)  # 1536 dimensions
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
