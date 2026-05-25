"""Graph domain helpers: embedding sync and LLM-based architecture generation."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from uuid import uuid5, NAMESPACE_DNS

import requests
from flask import abort, current_app, jsonify

from models.db import db, Graph, Project, RAGEmbedding
from utils.app_settings import get_frontend_llm_settings, get_github_token
from utils.rag import upsert_embedding


def update_graph_embeddings(project_id, graph) -> None:
    """Sync RAG embeddings for all nodes/edges after a graph update.
    Removes stale embeddings for items no longer in the graph, then upserts the rest."""
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


def generate_architecture_graph(project_id):
    """Clone the project's GitHub repo and use the LLM to generate an architecture graph.
    Only works when the graph is empty (no nodes). Returns generated nodes and edges,
    and writes them directly to the graph."""
    project = db.session.get(Project, project_id)
    if not project:
        abort(404)
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
