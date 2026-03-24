"""
API Routes for Terarchitect
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from uuid import UUID, uuid5, NAMESPACE_DNS

import requests
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text, nullslast

from models.db import db, Project, Graph, KanbanBoard, Ticket, Note, RAGEmbedding, ExecutionLog, PR, PRReviewComment, AgentJob
from utils.embedding_client import embed_single
from utils.rag import upsert_embedding, delete_embeddings_for_source
from utils.app_settings import (
    get_gh_env_for_user,
    get_dashboard_git_env,
    get_value,
    check_execution_readiness,
    get_frontend_llm_settings,
    get_github_token,
)

api_bp = Blueprint("api", __name__)

# Invisible HTML comment appended to all agent-posted PR comments.
# Used to distinguish agent replies from human reviewer comments during PR polling,
# since the agent and user may share the same GitHub token.
BOT_COMMENT_SIGNATURE = "<!-- terarchitect-bot -->"

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


def _env_for_gh_user():
    """Env for gh CLI in UI context (PR comment, approve, merge, poll). Uses stored user token and dashboard git identity if set."""
    return {**os.environ, **get_gh_env_for_user(), **get_dashboard_git_env()}


# Cancel requested by ticket_id is now stored in agent_jobs.cancel_requested column (DB-backed, process-safe).


def _bootstrap_project_memory(project: Project) -> None:
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


def _project_to_json(project: Project):
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


@api_bp.route("/projects/<uuid:project_id>/graph/generate", methods=["POST"])
def graph_generate(project_id):
    """Clone the project's GitHub repo and use the LLM to generate an architecture graph.
    Only works when the graph is empty (no nodes). Returns generated nodes and edges,
    and writes them directly to the graph."""

    project = Project.query.get_or_404(project_id)
    graph_obj = Graph.query.filter_by(project_id=project_id).first_or_404()

    existing_nodes = graph_obj.nodes if graph_obj.nodes else []
    if existing_nodes:
        return jsonify({"error": "Graph already has nodes. Generate only works on empty graphs."}), 409

    github_url = (project.github_url or "").strip()
    if not github_url:
        return jsonify({"error": "Project has no GitHub URL configured."}), 400

    llm = get_frontend_llm_settings()
    if not llm["model"]:
        return jsonify({"error": "No LLM model configured. Set FRONTEND_LLM_MODEL (or DIRECTOR_MODEL) in backend env."}), 400

    token = get_github_token()

    # --- Clone repo into temp dir ---
    work_dir = tempfile.mkdtemp(prefix="terarchitect_gen_")
    try:
        clone_url = github_url
        if token and "github.com" in clone_url:
            clone_url = clone_url.replace("https://", f"https://{token}@")
        result = subprocess.run(
            ["git", "clone", "--depth=1", clone_url, work_dir],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            return jsonify({"error": f"Failed to clone repo: {result.stderr[:500]}"}), 502

        # --- Build file tree (depth-limited, skip noise dirs) ---
        SKIP_DIRS = {
            "node_modules", ".git", "__pycache__", ".pytest_cache", "dist", "build",
            ".next", "out", "coverage", ".venv", "venv", "env", ".tox", "vendor",
        }
        MANIFEST_NAMES = {
            "package.json", "requirements.txt", "pyproject.toml", "setup.py",
            "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "Gemfile",
        }

        def walk_tree(root: str, rel: str = "", depth: int = 0) -> list[str]:
            lines = []
            try:
                entries = sorted(os.listdir(os.path.join(root, rel) if rel else root))
            except PermissionError:
                return lines
            for entry in entries:
                full = os.path.join(root, rel, entry) if rel else os.path.join(root, entry)
                rel_entry = f"{rel}/{entry}" if rel else entry
                if os.path.isdir(full):
                    if entry in SKIP_DIRS:
                        continue
                    lines.append(f"{'  ' * depth}{rel_entry}/")
                    if depth < 3:
                        lines.extend(walk_tree(root, rel_entry, depth + 1))
                else:
                    lines.append(f"{'  ' * depth}{rel_entry}")
            return lines

        tree_lines = walk_tree(work_dir)
        file_tree = "\n".join(tree_lines[:600])  # cap at 600 lines

        # --- Collect file contents ---
        # Priority 1: config/manifest files (full content)
        # Priority 2: import/require lines from all source files (top 50 lines of each)
        SKIP_DIRS = {
            "node_modules", ".git", "__pycache__", ".pytest_cache", "dist", "build",
            ".next", "out", "coverage", ".venv", "venv", "env", ".tox", "vendor",
            "migrations", "static", "assets", "public", "images", "fonts",
        }
        MANIFEST_NAMES = {
            "package.json", "requirements.txt", "pyproject.toml", "setup.py",
            "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "Gemfile",
        }
        SOURCE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rb", ".java", ".rs"}
        IMPORT_PATTERNS = [
            "import ", "from ", "require(", "require ", "use ", "extern crate",
            "include ", "#include",
        ]

        config_files: list[str] = []
        source_files: list[str] = []

        for dirpath, dirnames, filenames in os.walk(work_dir):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel_dir = os.path.relpath(dirpath, work_dir)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            if depth > 4:
                dirnames.clear()
                continue
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, work_dir)
                lower = fname.lower()
                ext = os.path.splitext(fname)[1].lower()
                if (
                    fname in MANIFEST_NAMES
                    or lower.startswith("dockerfile")
                    or (lower.endswith((".yml", ".yaml")) and depth <= 2)
                    or lower in ("readme.md", ".env.example", ".env.sample")
                ):
                    config_files.append(rel_path)
                elif ext in SOURCE_EXTS:
                    source_files.append(rel_path)

        file_sections: list[str] = []
        total_chars = 0
        CHAR_LIMIT = 100_000

        # Read config files in full (up to 8000 chars each)
        for rel_path in sorted(config_files)[:40]:
            if total_chars >= CHAR_LIMIT:
                break
            try:
                with open(os.path.join(work_dir, rel_path), encoding="utf-8", errors="replace") as f:
                    content = f.read(8000)
                snippet = f"### {rel_path}\n```\n{content}\n```"
                file_sections.append(snippet)
                total_chars += len(snippet)
            except OSError:
                pass

        # Extract import/require lines from source files
        import_sections: list[str] = []
        import_chars = 0
        IMPORT_CHAR_LIMIT = 60_000
        for rel_path in sorted(source_files):
            if import_chars >= IMPORT_CHAR_LIMIT:
                break
            try:
                with open(os.path.join(work_dir, rel_path), encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                # Grab lines that are imports/requires, plus the first line (often module declaration)
                import_lines = [
                    l.rstrip() for l in lines[:120]
                    if any(l.lstrip().startswith(p) for p in IMPORT_PATTERNS)
                ]
                if import_lines:
                    block = f"### {rel_path} (imports)\n" + "\n".join(import_lines)
                    import_sections.append(block)
                    import_chars += len(block)
            except OSError:
                pass

        file_content_block = "\n\n".join(file_sections)
        if import_sections:
            file_content_block += "\n\n## Import Graph (extracted from all source files)\n\n" + "\n\n".join(import_sections)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # --- Build LLM prompt ---
    node_types = ["service", "database", "cache", "queue", "api", "worker", "view", "frontend"]
    prompt = f"""You are an expert software architect performing a deep architecture analysis. Analyze the repository below and produce a DETAILED, COMPREHENSIVE architecture graph as JSON. This is a complex production system — do not produce a superficial high-level diagram. Go deep.

Repository: {github_url}

## File Tree
```
{file_tree}
```

## Key Config / Manifest Files
{file_content_block}

## Instructions
Produce a thorough architecture graph covering ALL of the following that you can identify:

**Services & Applications**
- Every distinct frontend app, backend service, API server, microservice, or worker process
- Background job processors, schedulers, cron jobs
- Admin panels or internal tooling services

**Data Stores**
- Every database (Postgres, MySQL, MongoDB, SQLite, etc.)
- Search indexes (Elasticsearch, OpenSearch, Solr)
- Caches (Redis, Memcached)
- Object storage (S3, GCS, MinIO)
- Message queues / event streams (Kafka, RabbitMQ, SQS, Celery, etc.)

**External Integrations**
- Third-party APIs (auth providers, payment, email, SMS, analytics, etc.)
- External data sources or feeds
- Webhooks in or out

**Infrastructure**
- Load balancers, API gateways, reverse proxies (nginx, traefik, etc.)
- CDNs or static asset hosts

**Security / Auth**
- Auth services (OAuth, SAML, JWT issuers, session stores)

For EDGES, capture every significant data flow:
- API calls between services
- Database reads/writes
- Queue publish/consume relationships
- Cache reads/writes
- Auth flows

## Output Format
Return ONLY a valid JSON object (no markdown, no explanation) with this exact shape:
{{
  "nodes": [
    {{
      "id": "node-1",
      "type": "<one of: {', '.join(node_types)}>",
      "position": {{"x": <number>, "y": <number>}},
      "data": {{
        "label": "<short name>",
        "description": "<2-3 sentence description of what this component does and its role in the system>",
        "tech": ["<technology>", ...],
        "ports": ["<port>", ...],
        "security": ["<auth method or security concern>", ...]
      }}
    }}
  ],
  "edges": [
    {{
      "id": "edge-1",
      "source": "<node id>",
      "target": "<node id>",
      "data": {{
        "label": "<relationship name>",
        "protocol": "<HTTP | gRPC | TCP | AMQP | REST | GraphQL | etc>"
      }}
    }}
  ]
}}

## Layout Rules
- Use a 1400x900 canvas. x values 50-1350, y values 50-850.
- Arrange nodes in logical tiers: frontends top, backend services middle, data stores bottom, external integrations right side.
- Space nodes at least 150px apart so labels don't overlap.
- Each node id must be unique (node-1, node-2, ...) and each edge id unique (edge-1, edge-2, ...).
- Edge source/target must reference node ids that exist in the nodes array.
- Aim for 10-20+ nodes for a complex project. Do NOT summarise multiple distinct services into one node.
- Return ONLY the JSON object. No prose, no markdown fences.
"""

    # --- Call LLM ---
    llm_url = (llm["url"] or "").rstrip("/")
    if not llm_url:
        llm_url = "https://api.openai.com/v1"

    headers = {"Content-Type": "application/json"}
    if llm["api_key"]:
        headers["Authorization"] = f"Bearer {llm['api_key']}"

    model_name = llm["model"] or ""
    # gpt-5 and newer OpenAI models use /v1/responses with `input` instead of /v1/chat/completions
    use_responses_api = model_name.startswith("gpt-5") or model_name.startswith("o3") or model_name.startswith("o4")

    try:
        if use_responses_api:
            api_url = f"{llm_url}/responses"
            payload = {
                "model": model_name,
                "input": prompt,
            }
        else:
            api_url = f"{llm_url}/chat/completions"
            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 4096,
            }
        resp = requests.post(api_url, headers=headers, json=payload, timeout=300)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"LLM request failed: {str(e)[:300]}"}), 502

    raw = resp.json()
    # Handle both /responses and /chat/completions response shapes
    if use_responses_api:
        # /v1/responses: output is a list of content blocks
        output_items = raw.get("output") or []
        content = ""
        for item in output_items:
            if isinstance(item, dict):
                for part in (item.get("content") or []):
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        content += part.get("text", "")
                    elif isinstance(part, str):
                        content += part
        if not content:
            content = raw.get("output_text", "")
    else:
        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Strip markdown fences if the model wrapped the JSON anyway
    content = re.sub(r"^```(?:json)?\s*", "", content.strip(), flags=re.MULTILINE)
    content = re.sub(r"\s*```$", "", content.strip(), flags=re.MULTILINE)
    content = content.strip()

    try:
        generated = json.loads(content)
    except (json.JSONDecodeError, ValueError) as e:
        current_app.logger.error("Graph generate: LLM returned non-JSON: %s", content[:500])
        return jsonify({"error": f"LLM returned invalid JSON: {str(e)}"}), 502

    gen_nodes = generated.get("nodes") if isinstance(generated.get("nodes"), list) else []
    gen_edges = generated.get("edges") if isinstance(generated.get("edges"), list) else []

    if not gen_nodes:
        return jsonify({"error": "LLM returned no nodes. Try again or build the graph manually."}), 502

    # --- Persist to graph ---
    graph_obj.nodes = gen_nodes
    graph_obj.edges = gen_edges
    graph_obj.version = graph_obj.version + 1
    db.session.commit()

    return jsonify({
        "nodes": gen_nodes,
        "edges": gen_edges,
        "version": graph_obj.version,
        "node_count": len(gen_nodes),
        "edge_count": len(gen_edges),
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
            depends_on_ticket_ids=data.get("depends_on_ticket_ids", []),
        )
        db.session.add(ticket)
        db.session.commit()
        content = ((ticket.title or "") + " " + (ticket.description or "")).strip()
        if content:
            upsert_embedding(project_id, "ticket", ticket.id, content)
        return jsonify(_ticket_to_json(ticket)), 201


def _enqueue_ticket_job(ticket_id):
    """Enqueue a ticket job to agent_jobs. Skip if project missing URL/path for mode or already pending/running."""
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return
    project = Project.query.get(ticket.project_id)
    if not project:
        return
    execution_mode = getattr(project, "execution_mode", None) or "docker"
    if execution_mode == "local":
        if not (project.project_path or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project is local but has no project path", ticket_id)
            return
    else:
        if not (project.github_url or "").strip():
            current_app.logger.info("Skipping enqueue: ticket %s project has no GitHub URL", ticket_id)
            return
    dep_ids = ticket.depends_on_ticket_ids or []
    if dep_ids:
        blocking = Ticket.query.filter(
            Ticket.id.in_(dep_ids),
            Ticket.column_id != "done",
        ).first()
        if blocking:
            current_app.logger.info(
                "Skipping enqueue: ticket %s blocked by dependency %s (%s)",
                ticket_id, blocking.id, blocking.title,
            )
            return
    existing = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.status.in_(["pending", "running"]),
    ).with_for_update(skip_locked=True).first()
    if existing:
        current_app.logger.info("Skipping enqueue: ticket %s already has job %s", ticket_id, existing.id)
        return
    db.session.add(AgentJob(
        ticket_id=ticket_id,
        project_id=ticket.project_id,
        kind="ticket",
        status="pending",
    ))
    db.session.commit()
    current_app.logger.info("Enqueued ticket job for ticket %s", ticket_id)


def _run_pr_poll_loop(app, pr_poll_seconds=60):
    """Background thread: run PR review comment poll; new comments enqueue to agent_jobs. No in-process agent run."""
    while True:
        time.sleep(pr_poll_seconds)
        try:
            with app.app_context():
                _poll_pr_review_comments()
        except Exception as e:
            if app:
                app.logger.exception("PR review poller error: %s", e)


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
        "failed_count": t.failed_count or 0,
        "depends_on_ticket_ids": t.depends_on_ticket_ids or [],
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    running_job = AgentJob.query.filter_by(ticket_id=t.id, status="running").first()
    out["is_running"] = running_job is not None
    out["running_job_kind"] = running_job.kind if running_job else None
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
            kanban = KanbanBoard.query.filter_by(project_id=project_id).first()
            valid_cols = {c["id"] for c in (kanban.columns or [])} if kanban else set()
            if valid_cols and new_col not in valid_cols:
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
        # Cascade: when a ticket reaches done, auto-enqueue backlog tickets that depended on it
        # and whose remaining dependencies are now all satisfied.
        if data.get("column_id") == "done":
            done_ticket_id = str(ticket.id)
            candidates = Ticket.query.filter(
                Ticket.project_id == project_id,
                Ticket.column_id == "backlog",
            ).all()
            for candidate in candidates:
                dep_ids = [str(d) for d in (candidate.depends_on_ticket_ids or [])]
                if done_ticket_id not in dep_ids:
                    continue
                still_blocking = Ticket.query.filter(
                    Ticket.id.in_(dep_ids),
                    Ticket.column_id != "done",
                ).count()
                if still_blocking == 0:
                    current_app.logger.info(
                        "Auto-enqueuing ticket %s: all dependencies now done", candidate.id
                    )
                    _enqueue_ticket_job(candidate.id)
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

def _job_to_response(job):
    """Build JSON payload for a claimed job."""
    project = Project.query.get(job.project_id)
    repo_url = (project.github_url or "") if project else ""
    execution_mode = getattr(project, "execution_mode", None) or "docker" if project else "docker"
    git_mode = getattr(project, "git_mode", None) or "structured" if project else "structured"
    out = {
        "job_id": str(job.id),
        "ticket_id": str(job.ticket_id),
        "project_id": str(job.project_id),
        "kind": job.kind,
        "repo_url": repo_url,
        "execution_mode": execution_mode,
        "git_mode": git_mode,
        "project_path": (project.project_path or "").strip() or None if project else None,
    }
    if job.kind == "review":
        out["pr_number"] = job.pr_number
        out["comment_body"] = job.comment_body or ""
        out["github_comment_id"] = job.github_comment_id
    return out


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


@api_bp.route("/worker/jobs/start", methods=["POST"])
def worker_jobs_start():
    """Phase 1: Claim one pending job. Body: optional {"project_id": "<uuid>"}. If project_id omitted, claim next pending job from any project. Returns 200 + job or 204."""
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
        if Project.query.get(project_id) is None:
            return jsonify({"error": "Project not found"}), 404
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
    # Ticket jobs: ticket goes back to backlog.
    if job.ticket_id:
        ticket = Ticket.query.filter_by(id=job.ticket_id).first()
        if ticket:
            if job.kind == "review":
                ticket.column_id = "in_review"
            else:
                ticket.column_id = "backlog"
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
                    ticket.column_id = "backlog"
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
    import re
    if not url or not isinstance(url, str):
        return None
    url = url.strip().rstrip("/")
    if "github.com" not in url:
        return None
    path = url.split("github.com")[-1].strip("/")
    parts = path.split("/")
    if len(parts) < 2:
        return None
    slug = "/".join(parts[:2])
    if not re.match(r'^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$', slug):
        return None
    return slug


def _is_approval_comment(body: str) -> bool:
    """LLM-based check: returns True when the comment is a pure approval and the agent should NOT run.
    Delegates to utils.pr_comment_classifier; falls back to False on any error."""
    try:
        from utils.app_settings import get_frontend_llm_settings
        from utils.pr_comment_classifier import classify_comment_is_approval
        result = classify_comment_is_approval(body, get_frontend_llm_settings())
        current_app.logger.info("Approval check for comment (%.60s...): %s", body, result)
        return result
    except Exception as e:
        current_app.logger.warning("Approval comment check failed (%s); defaulting to trigger agent", e)
        return False


def _enqueue_review_job(ticket_id, comment_body, pr_number, project_id, github_comment_id):
    """Enqueue a PR review job to agent_jobs. Skip if same ticket+PR already pending/running."""
    existing = AgentJob.query.filter(
        AgentJob.ticket_id == ticket_id,
        AgentJob.kind == "review",
        AgentJob.pr_number == pr_number,
        AgentJob.status.in_(["pending", "running"]),
    ).with_for_update(skip_locked=True).first()
    if existing:
        current_app.logger.info("Skipping enqueue: ticket %s PR #%s already has job", ticket_id, pr_number)
        return
    db.session.add(AgentJob(
        ticket_id=ticket_id,
        project_id=project_id,
        kind="review",
        status="pending",
        pr_number=pr_number,
        comment_body=comment_body,
        github_comment_id=github_comment_id,
    ))
    db.session.commit()
    current_app.logger.info("Enqueued review job for ticket %s PR #%s", ticket_id, pr_number)


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



def _split_paginate_output(stdout: str) -> list:
    """Parse `gh api --paginate` output into a list of JSON values.
    gh --paginate writes one JSON array per page, concatenated without a separator.
    This splits them by scanning for array boundaries and returns a flat list of all items."""
    results = []
    text = stdout.strip()
    i = 0
    while i < len(text):
        if text[i] != "[":
            i += 1
            continue
        depth = 0
        j = i
        while j < len(text):
            if text[j] == "[":
                depth += 1
            elif text[j] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[i:j + 1])
                        results.append(parsed)
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                    break
            j += 1
        else:
            break
    return results if results else [json.loads(text)]


def _poll_pr_review_comments():
    """Check PRs in review for new comments via gh CLI and trigger review agent for new ones. Call with app context."""
    repo_slug = _repo_slug_from_github_url
    # Tickets in_review with a PR
    prs_in_review = list(
        db.session.query(PR, Ticket, Project)
        .join(Ticket, Ticket.id == PR.ticket_id)
        .join(Project, Project.id == PR.project_id)
        .filter(Ticket.column_id == "in_review")
        .filter(Project.github_url.isnot(None))
        .filter(PR.pr_number.isnot(None))
        .filter(db.or_(Project.git_mode == "structured", Project.git_mode.is_(None)))
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

        # Check if PR was approved (per-reviewer latest state is APPROVED, no blocking CHANGES_REQUESTED) -> move ticket to done
        try:
            r_reviews = subprocess.run(
                ["gh", "api", f"repos/{slug}/pulls/{pr_number}/reviews", "--paginate"],
                capture_output=True,
                text=True,
                timeout=15,
                env=_env_for_gh_user(),
            )
            if r_reviews.returncode == 0 and r_reviews.stdout:
                # gh --paginate writes one JSON array per page concatenated; flatten into one list.
                raw_stdout = r_reviews.stdout.strip()
                reviews: list = []
                for chunk in _split_paginate_output(raw_stdout):
                    if isinstance(chunk, list):
                        reviews.extend(chunk)
                if reviews:
                    # Build per-reviewer latest state; APPROVED only if no reviewer has CHANGES_REQUESTED pending
                    latest_by_reviewer: dict = {}
                    for rev in reviews:
                        login = (rev.get("user") or {}).get("login") or "unknown"
                        state = rev.get("state") or ""
                        if state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                            latest_by_reviewer[login] = state
                    has_approval = any(s == "APPROVED" for s in latest_by_reviewer.values())
                    has_blocking = any(s == "CHANGES_REQUESTED" for s in latest_by_reviewer.values())
                    if has_approval and not has_blocking:
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
                for chunk in _split_paginate_output(r.stdout) if r.stdout else []:
                    if isinstance(chunk, list):
                        raw_comments.extend(chunk)
            except (json.JSONDecodeError, Exception):
                continue
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
        # Mark comments we should never respond to as addressed:
        #   1. Comments with our bot signature (agent's own replies).
        #   2. Comments posted by any GitHub bot account (login ends with "[bot]"),
        #      e.g. claude[bot], orca-security-us[bot], github-actions[bot].
        from sqlalchemy import or_
        bot_comments = PRReviewComment.query.filter(
            PRReviewComment.project_id == project.id,
            PRReviewComment.pr_number == pr_number,
            PRReviewComment.addressed_at.is_(None),
            or_(
                PRReviewComment.body.contains(BOT_COMMENT_SIGNATURE),
                PRReviewComment.author_login.like("%[bot]"),
            ),
        ).all()
        for row in bot_comments:
            row.addressed_at = _dt.utcnow()
            row.updated_at = _dt.utcnow()
        if bot_comments:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        # Trigger only for the single most recent unaddressed human comment (no bot signature)
        next_comment = (
            PRReviewComment.query.filter(
                PRReviewComment.project_id == project.id,
                PRReviewComment.pr_number == pr_number,
                PRReviewComment.addressed_at.is_(None),
                PRReviewComment.body.isnot(None),
                PRReviewComment.body != "",
                ~PRReviewComment.body.contains(BOT_COMMENT_SIGNATURE),
            )
            .order_by(nullslast(PRReviewComment.comment_created_at.desc()))
            .limit(1)
            .first()
        )
        if next_comment:
            # Skip comments that are pure blockquotes (user forwarded a bot comment without
            # adding their own feedback — every non-empty line starts with '>').
            body_lines = [l for l in (next_comment.body or "").splitlines() if l.strip()]
            if body_lines and all(l.lstrip().startswith(">") for l in body_lines):
                from datetime import datetime as _dt
                next_comment.addressed_at = _dt.utcnow()
                next_comment.updated_at = _dt.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                current_app.logger.info(
                    "PR #%s comment %s is a pure quote-forward — skipping agent",
                    pr_number, next_comment.github_comment_id,
                )
            elif _is_approval_comment(next_comment.body):
                # Pure approval — mark addressed and skip firing the agent.
                from datetime import datetime as _dt
                next_comment.addressed_at = _dt.utcnow()
                next_comment.updated_at = _dt.utcnow()
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                current_app.logger.info(
                    "PR #%s comment %s classified as approval — skipping agent",
                    pr_number, next_comment.github_comment_id,
                )
            else:
                _enqueue_review_job(
                    ticket.id,
                    next_comment.body,
                    pr_number,
                    project.id,
                    next_comment.github_comment_id,
                )


