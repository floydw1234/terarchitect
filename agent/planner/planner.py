"""
Planning agent for terarchitect.

Reads the project's architectural graph + notes, calls an LLM to decompose
objectives into tickets (each mapped to specific graph nodes/edges), and POSTs
the tickets to the backend API.

Usage (standalone):
    python -m agent.planner

Required env vars:
    PROJECT_ID              — project UUID
    TERARCHITECT_API_URL    — backend base URL (default: http://localhost:5010)
    DIRECTOR_LLM_URL        — LLM completions endpoint
    DIRECTOR_MODEL          — model name
    DIRECTOR_API_KEY        — API key

Optional env vars:
    DIRECTOR_PROVIDER       — "openai" (default) | "anthropic"
    PLANNER_MAX_TICKETS     — max tickets to generate (default: 20)
    PLANNER_DRY_RUN         — "1" to print plan without posting tickets
"""

import json
import os
import sys
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _api_get(base_url: str, path: str):
    try:
        resp = requests.get(f"{base_url}{path}", timeout=15)
        if resp.ok:
            return resp.json()
        print(f"[planner] GET {path} → {resp.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"[planner] GET {path} failed: {e}", file=sys.stderr)
    return None


def _api_post(base_url: str, path: str, body: dict):
    try:
        resp = requests.post(f"{base_url}{path}", json=body, timeout=15)
        if resp.ok:
            return resp.json()
        print(f"[planner] POST {path} → {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[planner] POST {path} failed: {e}", file=sys.stderr)
    return None


def _api_patch(base_url: str, path: str, body: dict):
    try:
        resp = requests.patch(f"{base_url}{path}", json=body, timeout=15)
        if resp.ok:
            return resp.json()
        print(f"[planner] PATCH {path} → {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[planner] PATCH {path} failed: {e}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a senior tech lead decomposing a software project into engineering tickets.

Given a project's architectural graph (nodes = components/services/modules,
edges = dependencies/interfaces/data flows) and project objectives/notes,
produce a JSON array of focused engineering tickets.

Each ticket MUST be mapped to the specific graph nodes or edges it will touch.
This is critical — it prevents scheduling conflicts when agents run in parallel
(swarm mode) and ensures the right agent is assigned to each component.

Output ONLY valid JSON in this exact format (no markdown, no commentary):
{{
  "tickets": [
    {{
      "title": "Short imperative title (80 chars max)",
      "description": "Detailed description including: what to implement, acceptance criteria, and any relevant context. Be specific enough for an autonomous agent to execute this without further clarification.",
      "associated_node_ids": ["node-id-1"],
      "associated_edge_ids": [],
      "depends_on_titles": [],
      "priority": "high"
    }}
  ]
}}

Field rules:
- associated_node_ids: IDs of graph nodes this ticket directly modifies (use exact IDs from the graph)
- associated_edge_ids: IDs of graph edges this ticket modifies (usually empty unless implementing an interface/integration)
- Every ticket must have at least one entry in associated_node_ids or associated_edge_ids.
  Use ["*"] in associated_node_ids ONLY for cross-cutting concerns that touch the whole codebase (e.g. CI setup, global config, database migrations).
- depends_on_titles: titles of other tickets in this plan that must complete first
- priority: "high" | "medium" | "low"
- Keep tickets small and focused: one component, one concern, ~1-2 days of work
- Generate between 3 and {max_tickets} tickets total
"""


def _llm_call(system: str, prompt: str) -> str:
    """Call the configured LLM and return the raw response string."""
    llm_url = _env("DIRECTOR_LLM_URL")
    model = _env("DIRECTOR_MODEL", "gpt-4o")
    api_key = _env("DIRECTOR_API_KEY")
    provider = _env("DIRECTOR_PROVIDER", "openai")

    if not llm_url:
        raise RuntimeError(
            "DIRECTOR_LLM_URL is not set. "
            "Example: export DIRECTOR_LLM_URL=https://api.openai.com/v1/chat/completions"
        )

    if provider == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = requests.post(llm_url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    else:
        # OpenAI-compatible
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }
        resp = requests.post(llm_url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _build_prompt(project: dict, graph: dict, notes: list, max_tickets: int) -> str:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    # Index notes by node_id/edge_id for richer context
    node_notes: dict[str, list[str]] = {}
    edge_notes: dict[str, list[str]] = {}
    global_notes: list[str] = []
    for n in notes:
        content = (n.get("content") or "").strip()
        if not content:
            continue
        nid = n.get("node_id")
        eid = n.get("edge_id")
        if nid:
            node_notes.setdefault(nid, []).append(content)
        elif eid:
            edge_notes.setdefault(eid, []).append(content)
        else:
            global_notes.append(content)

    node_lines = []
    for n in nodes:
        nid = n.get("id", "")
        label = n.get("label", "")
        ntype = n.get("type", "component")
        line = f"  - id={nid!r}  label={label!r}  type={ntype!r}"
        if nid in node_notes:
            for note_text in node_notes[nid]:
                line += f"\n      note: {note_text}"
        node_lines.append(line)

    edge_lines = []
    for e in edges:
        eid = e.get("id", "")
        src = e.get("source", "")
        tgt = e.get("target", "")
        label = e.get("label", "")
        line = f"  - id={eid!r}  {src!r} → {tgt!r}  label={label!r}"
        if eid in edge_notes:
            for note_text in edge_notes[eid]:
                line += f"\n      note: {note_text}"
        edge_lines.append(line)

    objective_text = "\n\n".join(global_notes)
    if not objective_text:
        objective_text = project.get("description") or "(no objectives provided)"

    return f"""Project: {project.get('name', 'Untitled')}
Description: {project.get('description', '')}

## Architectural Graph — Nodes ({len(nodes)} total)
{chr(10).join(node_lines) or '(no nodes — treat as single component)'}

## Architectural Graph — Edges ({len(edges)} total)
{chr(10).join(edge_lines) or '(none)'}

## Project Objectives / Notes
{objective_text}

---
Generate the ticket plan now. Output only the JSON object."""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    project_id: str,
    base_url: str,
    max_tickets: int = 20,
    dry_run: bool = False,
) -> list[dict]:
    """
    Fetch project context, call the LLM, post tickets.
    Returns the list of created ticket dicts (or planned dicts if dry_run).
    """
    print(f"[planner] Fetching project {project_id}…")
    project = _api_get(base_url, f"/api/projects/{project_id}")
    if not project:
        raise RuntimeError(f"Could not fetch project {project_id} from {base_url}")

    graph = _api_get(base_url, f"/api/projects/{project_id}/graph") or {"nodes": [], "edges": []}
    notes = _api_get(base_url, f"/api/projects/{project_id}/notes") or []

    node_count = len(graph.get("nodes") or [])
    edge_count = len(graph.get("edges") or [])
    print(f"[planner] Context: {node_count} nodes, {edge_count} edges, {len(notes)} notes")

    system = _SYSTEM_PROMPT.format(max_tickets=max_tickets)
    prompt = _build_prompt(project, graph, notes, max_tickets)

    print("[planner] Calling LLM to generate plan…")
    raw = _llm_call(system, prompt)

    try:
        data = json.loads(raw)
        tickets = data.get("tickets") if isinstance(data, dict) else data
        if not isinstance(tickets, list):
            raise ValueError(f"Expected list under 'tickets', got {type(tickets)}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse LLM response: {e}\nRaw response:\n{raw}") from e

    print(f"[planner] Generated {len(tickets)} tickets:")
    for i, t in enumerate(tickets):
        nodes_touched = t.get("associated_node_ids") or []
        edges_touched = t.get("associated_edge_ids") or []
        deps = t.get("depends_on_titles") or []
        print(f"  {i + 1:2d}. [{t.get('priority', 'medium').upper():6s}] {t['title']}")
        print(f"        nodes={nodes_touched}  edges={edges_touched}")
        if deps:
            print(f"        depends_on={deps}")

    if dry_run:
        print("[planner] DRY RUN — tickets not posted to backend")
        return tickets

    # Post tickets, collect {title → id} for dependency resolution
    posted: dict[str, str] = {}
    created: list[dict] = []
    for ticket in tickets:
        body = {
            "title": ticket["title"],
            "description": ticket.get("description") or ticket["title"],
            "column_id": "backlog",
            "priority": ticket.get("priority") or "medium",
            "status": "todo",
            "associated_node_ids": ticket.get("associated_node_ids") or [],
            "associated_edge_ids": ticket.get("associated_edge_ids") or [],
            "depends_on_ticket_ids": [],  # resolved in second pass
        }
        result = _api_post(base_url, f"/api/projects/{project_id}/tickets", body)
        if result:
            posted[ticket["title"]] = result["id"]
            created.append(result)
        else:
            print(f"[planner] FAILED to create: {ticket['title']}", file=sys.stderr)

    # Second pass: resolve title-based dependencies to real UUIDs
    for ticket in tickets:
        title = ticket["title"]
        if title not in posted:
            continue
        dep_ids = [
            posted[dep_title]
            for dep_title in (ticket.get("depends_on_titles") or [])
            if dep_title in posted
        ]
        if dep_ids:
            _api_patch(
                base_url,
                f"/api/projects/{project_id}/tickets/{posted[title]}",
                {"depends_on_ticket_ids": dep_ids},
            )

    print(f"[planner] Done — {len(created)}/{len(tickets)} tickets created in backlog")
    return created


def main() -> None:
    project_id = _env("PROJECT_ID")
    base_url = _env("TERARCHITECT_API_URL", "http://localhost:5010").rstrip("/")
    max_tickets = int(_env("PLANNER_MAX_TICKETS", "20"))
    dry_run = _env("PLANNER_DRY_RUN") == "1"

    if not project_id:
        print("[planner] Error: PROJECT_ID env var is required", file=sys.stderr)
        sys.exit(1)

    try:
        run(project_id, base_url, max_tickets=max_tickets, dry_run=dry_run)
    except RuntimeError as e:
        print(f"[planner] Error: {e}", file=sys.stderr)
        sys.exit(1)
