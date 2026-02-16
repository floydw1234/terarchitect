"""
Database Models for Terarchitect
"""
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy import Float

db = SQLAlchemy()


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    project_path = db.Column(db.Text)  # Local file path where OpenCode runs
    github_url = db.Column(db.Text)    # GitHub repository URL for PR creation
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    graphs = db.relationship("Graph", backref="project", uselist=False, cascade="all, delete-orphan")
    kanban_boards = db.relationship("KanbanBoard", backref="project", uselist=False, cascade="all, delete-orphan")
    tickets = db.relationship("Ticket", backref="project", cascade="all, delete-orphan")
    notes = db.relationship("Note", backref="project", cascade="all, delete-orphan")
    execution_logs = db.relationship("ExecutionLog", backref="project", cascade="all, delete-orphan")
    prs = db.relationship("PR", backref="project", cascade="all, delete-orphan")
    settings = db.relationship("Setting", backref="project", cascade="all, delete-orphan")
    # No cascade, noload: embedding column is pgvector (OID 16397); ORM must never SELECT it (unknown to ARRAY(Float)).
    rag_embeddings = db.relationship("RAGEmbedding", backref="project", cascade="save-update", lazy="noload")


class Graph(db.Model):
    __tablename__ = "graphs"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    nodes = db.Column(JSONB, default=[])
    edges = db.Column(JSONB, default=[])
    version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class KanbanBoard(db.Model):
    __tablename__ = "kanban_boards"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    columns = db.Column(JSONB, default=[])
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    column_id = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    associated_node_ids = db.Column(JSONB, default=[])
    associated_edge_ids = db.Column(JSONB, default=[])
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
    raw_output = db.Column(db.Text)  # Full worker output for debugging
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


class PRReviewComment(db.Model):
    """Tracks GitHub PR comments and whether we've addressed (replied to) them."""

    __tablename__ = "pr_review_comments"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    ticket_id = db.Column(db.UUID, db.ForeignKey("tickets.id"), nullable=True)
    pr_number = db.Column(db.Integer, nullable=False)
    github_comment_id = db.Column(db.BigInteger, nullable=False)
    author_login = db.Column(db.String(255))
    body = db.Column(db.Text)
    comment_created_at = db.Column(db.TIMESTAMP)
    addressed_at = db.Column(db.TIMESTAMP)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (db.UniqueConstraint("project_id", "pr_number", "github_comment_id", name="_pr_review_comment_uniq"),)


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    key = db.Column(db.String(255), nullable=False)
    value = db.Column(JSONB)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    __table_args__ = (db.UniqueConstraint("project_id", "key", name="_project_setting_key"),)


class AppSetting(db.Model):
    """App-level key/value for tokens and API keys. Values stored encrypted when TERARCHITECT_SECRET_KEY is set."""

    __tablename__ = "app_settings"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    key = db.Column(db.String(255), nullable=False, unique=True)
    value = db.Column(db.Text, nullable=False)  # encrypted or plaintext
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class RAGEmbedding(db.Model):
    __tablename__ = "rag_embeddings"

    id = db.Column(db.UUID, primary_key=True, default=db.func.uuid_generate_v4())
    project_id = db.Column(db.UUID, db.ForeignKey("projects.id"), nullable=False)
    source_type = db.Column(db.String(50), nullable=False)  # "node", "edge", "note", "ticket", "ticket_comment"
    source_id = db.Column(db.UUID, nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(ARRAY(Float), nullable=False)  # 768 dimensions (embedding service)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
