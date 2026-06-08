"""
Database Models for Terarchitect
"""
import uuid

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy import Float
from sqlalchemy.types import TypeDecorator

db = SQLAlchemy()

JSON_TYPE = JSONB().with_variant(db.JSON(), "sqlite")
EMBEDDING_TYPE = ARRAY(Float).with_variant(db.JSON(), "sqlite")
class StringUUID(TypeDecorator):
    impl = db.UUID(as_uuid=False)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value)


UUID_TYPE = StringUUID()


def new_uuid() -> str:
    return str(uuid.uuid4())


class Project(db.Model):
    __tablename__ = "projects"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    project_path = db.Column(db.Text)  # When execution_mode=local: path on host for agent to run in
    github_url = db.Column(db.Text)    # GitHub repository URL for PR creation and docker-mode clone
    execution_mode = db.Column(db.String(50), nullable=False, default="docker")  # "docker" | "local"
    git_mode = db.Column(db.String(20), nullable=False, default="swarm")
    # AgentHub DAG root: the last shipped main commit. All new agent work builds on top of this.
    shipped_frontier = db.Column(db.String(255))
    shipped_frontier_updated_at = db.Column(db.TIMESTAMP)
    # The currently blessed composite workspace (preferred candidate state, pre-ship)
    blessed_workspace_id = db.Column(db.String(255))
    verification_policy = db.Column(JSON_TYPE, default=dict)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    graphs = db.relationship("Graph", backref="project", uselist=False, cascade="all, delete-orphan")
    kanban_boards = db.relationship("KanbanBoard", backref="project", uselist=False, cascade="all, delete-orphan")
    tickets = db.relationship("Ticket", backref="project", cascade="all, delete-orphan")
    notes = db.relationship("Note", backref="project", cascade="all, delete-orphan")
    execution_logs = db.relationship("ExecutionLog", backref="project", cascade="all, delete-orphan")
    ship_runs = db.relationship("ShipRun", backref="project", cascade="all, delete-orphan")
    evidence_bundles = db.relationship("EvidenceBundle", backref="project", cascade="all, delete-orphan")
    evidence_runs = db.relationship("EvidenceRun", backref="project", cascade="all, delete-orphan")
    # No cascade, noload: embedding column is pgvector (OID 16397); ORM must never SELECT it (unknown to ARRAY(Float)).
    rag_embeddings = db.relationship("RAGEmbedding", backref="project", cascade="save-update", lazy="noload")


class Graph(db.Model):
    __tablename__ = "graphs"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    nodes = db.Column(JSON_TYPE, default=[])
    edges = db.Column(JSON_TYPE, default=[])
    version = db.Column(db.Integer, default=1)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class KanbanBoard(db.Model):
    __tablename__ = "kanban_boards"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    columns = db.Column(JSON_TYPE, default=[])
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    column_id = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    associated_node_ids = db.Column(JSON_TYPE, default=[])
    associated_edge_ids = db.Column(JSON_TYPE, default=[])
    priority = db.Column(db.String(50), default="medium")
    status = db.Column(db.String(50), default="todo")
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    depends_on_ticket_ids = db.Column(JSON_TYPE, default=list)
    # Intent fields (Phase 2 — tickets as first-class intent objects)
    # intent_status: draft | ready | active | blocked | archived
    intent_status = db.Column(db.String(50), nullable=False, default="ready")
    rationale = db.Column(db.Text)           # why this work matters
    acceptance_criteria = db.Column(db.Text) # what done looks like
    constraints = db.Column(db.Text)         # limits, non-goals, what not to do
    value_score = db.Column(db.Integer)      # optional 1-10 value estimate
    risk_level = db.Column(db.String(50))    # low | medium | high
    created_source = db.Column(db.String(50))# manual | ai | import
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    comments = db.relationship("TicketComment", backref="ticket", cascade="all, delete-orphan")
    execution_logs = db.relationship("ExecutionLog", backref="ticket", cascade="all, delete-orphan")
    agent_jobs = db.relationship("AgentJob", backref="ticket", cascade="all, delete-orphan")
    attempts = db.relationship("TicketAttempt", backref="ticket", cascade="all, delete-orphan",
                               order_by="TicketAttempt.attempt_num")


class TicketComment(db.Model):
    __tablename__ = "ticket_comments"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    ticket_id = db.Column(UUID_TYPE, db.ForeignKey("tickets.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_summary = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())


class Note(db.Model):
    __tablename__ = "notes"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    node_id = db.Column(db.Text)
    edge_id = db.Column(db.Text)
    title = db.Column(db.String(255))
    content = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class ExecutionLog(db.Model):
    __tablename__ = "execution_logs"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    ticket_id = db.Column(UUID_TYPE, db.ForeignKey("tickets.id"))
    session_id = db.Column(db.String(255))
    step = db.Column(db.String(100))
    summary = db.Column(db.Text)
    raw_output = db.Column(db.Text)  # Full worker output for debugging
    input_tokens = db.Column(db.Integer)
    output_tokens = db.Column(db.Integer)
    success = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())


class AgentJob(db.Model):
    """Queue for ticket agent work. Coordinator claims via POST /api/worker/jobs/start."""

    __tablename__ = "agent_jobs"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    ticket_id = db.Column(UUID_TYPE, db.ForeignKey("tickets.id"), nullable=False)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    kind = db.Column(db.String(50), nullable=False, default="ticket")
    status = db.Column(db.String(50), nullable=False, default="pending")  # pending | running | completed | failed
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class TicketAttempt(db.Model):
    """Records each AgentHub attempt for a ticket in swarm mode.
    The MVP path uses proposed/accepted/rejected/failed/shipped, but the live
    code still tolerates legacy-compatible validating/composed/release_pr_open
    states so older callbacks and tests keep working.
    Each accepted attempt records the AgentHub commit hash and base hash for composability."""

    __tablename__ = "ticket_attempts"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    ticket_id = db.Column(UUID_TYPE, db.ForeignKey("tickets.id"), nullable=False)
    agenthub_commit_hash = db.Column(db.String(255))
    base_hash = db.Column(db.String(255))
    wave_num = db.Column(db.Integer, default=0)
    attempt_num = db.Column(db.Integer, nullable=False, default=1)
    agent_id = db.Column(db.String(255))
    # MVP-facing states: proposed | accepted | rejected | superseded | failed | shipped
    # Legacy-compatible states retained in the live code: validating | composed
    # | release_pr_open
    status = db.Column(db.String(50), nullable=False, default="proposed")
    summary = db.Column(db.Text)
    test_status = db.Column(db.String(50))   # passed | failed | skipped | None
    test_output = db.Column(db.Text)
    validation_error = db.Column(db.Text)    # set when validation fails
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class ShipRun(db.Model):
    """Tracks wave ship runs. Auto-queued when a wave completes; shipper composes a release branch and PR."""

    __tablename__ = "ship_runs"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    wave_num = db.Column(db.Integer, nullable=False)
    # MVP-facing states: queued | composing | failed | ready_to_ship | shipping | shipped
    # Legacy-compatible callbacks still emit running | compose_failed.
    status = db.Column(db.String(50), nullable=False, default="queued")
    error = db.Column(db.Text)
    # Release branch composition
    release_branch = db.Column(db.Text)
    base_main_hash = db.Column(db.String(255))        # main tip at compose time
    composed_commit_hash = db.Column(db.String(255))  # HEAD of release branch after composition
    changed_files = db.Column(JSON_TYPE, default=list)
    summary = db.Column(db.Text)
    # Tests
    test_status = db.Column(db.String(50))   # passed | failed | skipped
    test_output = db.Column(db.Text)
    # Release PR
    release_pr_url = db.Column(db.Text)
    release_pr_number = db.Column(db.Integer)
    # Ship record
    shipped_at = db.Column(db.TIMESTAMP)
    shipped_commit_hash = db.Column(db.String(255))
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class CompositeWorkspace(db.Model):
    """A candidate codebase state composed from selected AgentHub leaves.
    Separate from ShipRun — a workspace can be previewed and blessed
    without shipping to main. Phase 9 lab-grade workspace surface."""

    __tablename__ = "composite_workspaces"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    # The root the workspace was based on (project.shipped_frontier at creation time)
    base_root_hash = db.Column(db.String(255))
    # Selected attempt IDs and their resolved leaf hashes
    selected_attempt_ids = db.Column(JSON_TYPE, default=list)   # [uuid, ...]
    selected_leaf_hashes = db.Column(JSON_TYPE, default=list)   # [commit_hash, ...]
    # draft | composing | conflicted | test_failed | preview_ready | blessed | snapshot_candidate | discarded
    status = db.Column(db.String(50), nullable=False, default="draft")
    # Composition results
    composed_commit_hash = db.Column(db.String(255))
    conflict_summary = db.Column(db.Text)
    changed_files = db.Column(JSON_TYPE, default=list)
    summary = db.Column(db.Text)
    # Tests
    test_status = db.Column(db.String(50))
    test_output = db.Column(db.Text)
    # Preview (optional — future phase)
    preview_url = db.Column(db.Text)
    preview_status = db.Column(db.String(50))
    preview_command = db.Column(JSON_TYPE, default=list)
    preview_error = db.Column(db.Text)
    # Who created this workspace
    created_by = db.Column(db.String(255))
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class EvidenceBundle(db.Model):
    """Verification evidence for an attempt, ShipRun, Composite Workspace, or future Snapshot."""

    __tablename__ = "evidence_bundles"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(UUID_TYPE, nullable=False)
    base_hash = db.Column(db.String(255))
    candidate_hash = db.Column(db.String(255))
    selected_attempt_ids = db.Column(JSON_TYPE, default=list)
    selected_leaf_hashes = db.Column(JSON_TYPE, default=list)
    status = db.Column(db.String(50), nullable=False, default="collecting")
    risk_level = db.Column(db.String(50), nullable=False, default="unknown")
    summary = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    checks = db.relationship("EvidenceCheck", backref="bundle", cascade="all, delete-orphan",
                             order_by="EvidenceCheck.created_at")


class EvidenceCheck(db.Model):
    """One verification result within an evidence bundle."""

    __tablename__ = "evidence_checks"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    evidence_bundle_id = db.Column(UUID_TYPE, db.ForeignKey("evidence_bundles.id"), nullable=False)
    check_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="skipped")
    tool_name = db.Column(db.String(255))
    command = db.Column(db.Text)
    output = db.Column(db.Text)
    artifact_url = db.Column(db.Text)
    check_metadata = db.Column(JSON_TYPE, default=dict)
    started_at = db.Column(db.TIMESTAMP)
    finished_at = db.Column(db.TIMESTAMP)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())


class EvidenceRun(db.Model):
    """Queue entry for asynchronous evidence execution."""

    __tablename__ = "evidence_runs"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    evidence_bundle_id = db.Column(UUID_TYPE, db.ForeignKey("evidence_bundles.id"))
    run_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="queued")
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(UUID_TYPE, nullable=False)
    check_type = db.Column(db.String(50))
    request_data = db.Column(JSON_TYPE, default=dict)
    error = db.Column(db.Text)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
    started_at = db.Column(db.TIMESTAMP)
    finished_at = db.Column(db.TIMESTAMP)
    updated_at = db.Column(db.TIMESTAMP, default=db.func.now(), onupdate=db.func.now())

    bundle = db.relationship("EvidenceBundle")


class RAGEmbedding(db.Model):
    __tablename__ = "rag_embeddings"

    id = db.Column(UUID_TYPE, primary_key=True, default=new_uuid)
    project_id = db.Column(UUID_TYPE, db.ForeignKey("projects.id"), nullable=False)
    source_type = db.Column(db.String(50), nullable=False)  # "node", "edge", "note", "ticket", "ticket_comment"
    source_id = db.Column(UUID_TYPE, nullable=False)
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(EMBEDDING_TYPE, nullable=False)  # 768 dimensions (embedding service)
    created_at = db.Column(db.TIMESTAMP, default=db.func.now())
