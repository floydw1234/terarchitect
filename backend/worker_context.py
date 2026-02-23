"""
Build worker-context dict for the agent. Used by GET /api/.../worker-context.
Backend-only; no dependency on the agent package.
"""
from typing import List, Tuple

from models.db import Project, Graph, Note, Ticket


def _ticket_summary(t: Ticket, mark_current: bool = False) -> dict:
    out = {
        "id": str(t.id),
        "title": t.title,
        "description": t.description,
        "priority": t.priority,
        "column_id": t.column_id,
        "status": t.status,
    }
    if mark_current:
        out["_current_ticket"] = True
        out["associated_node_ids"] = t.associated_node_ids or []
        # associated_edges_labeled provides names + ids; no need to pass raw associated_edge_ids to worker
    return out


def _edges_with_readable_endpoints(nodes: list, edges: list) -> list:
    node_label_by_id = {}
    for n in nodes or []:
        nid = n.get("id")
        if nid is not None:
            data = n.get("data") or {}
            node_label_by_id[nid] = data.get("label") or nid
    out = []
    for e in edges or []:
        copy = dict(e)
        copy["source_label"] = node_label_by_id.get(e.get("source"), e.get("source") or "")
        copy["target_label"] = node_label_by_id.get(e.get("target"), e.get("target") or "")
        out.append(copy)
    return out


def _expand_all_marker(
    nodes: list, edges: list, node_ids: list, edge_ids: list
) -> Tuple[List[str], List[str]]:
    _ALL = ["*"]
    nids = list(node_ids or [])
    eids = list(edge_ids or [])
    if nids == _ALL or (len(nids) == 1 and nids[0] == "*"):
        nids = [n.get("id") for n in (nodes or []) if n.get("id") is not None]
    if eids == _ALL or (len(eids) == 1 and eids[0] == "*"):
        eids = [e.get("id") for e in (edges or []) if e.get("id") is not None]
    return nids, eids


def _relevant_subgraph(
    nodes: list, edges: list, node_ids: list, edge_ids: list
) -> Tuple[list, list]:
    node_set = set(node_ids or [])
    edge_set = set(edge_ids or [])
    if not node_set and not edge_set:
        return [], []
    relevant_nodes = [n for n in nodes if n.get("id") in node_set]
    relevant_edges = [
        e for e in edges
        if e.get("id") in edge_set
        or e.get("source") in node_set
        or e.get("target") in node_set
    ]
    return relevant_nodes, relevant_edges


def build_worker_context(ticket: Ticket) -> dict:
    """Build worker-context dict from DB. Same shape as agent's build_worker_context."""
    project = Project.query.get(ticket.project_id)
    current_id = ticket.id
    context = {
        "project_name": project.name,
        "project_description": project.description,
        "github_url": project.github_url,
        "current_ticket": _ticket_summary(ticket, mark_current=True),
        "graph": None,
        "notes": [],
        "backlog_tickets": [],
        "in_progress_tickets": [],
        "done_tickets": [],
    }
    graph = Graph.query.filter_by(project_id=ticket.project_id).first()
    if graph:
        nodes = graph.nodes if graph.nodes else []
        edges = graph.edges if graph.edges else []
        full_enriched_edges = _edges_with_readable_endpoints(nodes, edges)
        context["graph"] = {"nodes": nodes, "edges": full_enriched_edges}
        node_ids, edge_ids = _expand_all_marker(
            nodes, edges, ticket.associated_node_ids or [], ticket.associated_edge_ids or []
        )
        rel_nodes, rel_edges = _relevant_subgraph(nodes, edges, node_ids, edge_ids)
        rel_enriched_edges = _edges_with_readable_endpoints(rel_nodes, rel_edges)
        for e in rel_enriched_edges:
            e["label_and_id"] = "{} → {}: {}".format(
                e.get("source_label", ""), e.get("target_label", ""), e.get("id", "")
            )
        rel_nodes_with_label_and_id = []
        for n in rel_nodes:
            copy = dict(n)
            data = copy.get("data") or {}
            label = data.get("label") or copy.get("id") or ""
            copy["label_and_id"] = "{}: {}".format(label, copy.get("id", ""))
            rel_nodes_with_label_and_id.append(copy)
        context["graph_relevant_to_current_ticket"] = {
            "nodes": rel_nodes_with_label_and_id,
            "edges": rel_enriched_edges,
        }
        node_label_by_id = {
            n.get("id"): (n.get("data") or {}).get("label") or n.get("id")
            for n in nodes
        }
        edge_label_by_id = {
            e.get("id"): "{} → {}".format(e.get("source_label", ""), e.get("target_label", ""))
            for e in full_enriched_edges
        }
        exp_node_ids, exp_edge_ids = _expand_all_marker(
            nodes, edges, ticket.associated_node_ids or [], ticket.associated_edge_ids or []
        )
        context["current_ticket"]["associated_nodes_labeled"] = [
            "{}: {}".format(node_label_by_id.get(nid, nid), nid) for nid in exp_node_ids
        ]
        context["current_ticket"]["associated_edges_labeled"] = [
            "{}: {}".format(edge_label_by_id.get(eid, eid), eid) for eid in exp_edge_ids
        ]
    else:
        context["graph_relevant_to_current_ticket"] = {"nodes": [], "edges": []}
        context["current_ticket"]["associated_nodes_labeled"] = []
        context["current_ticket"]["associated_edges_labeled"] = []

    notes = Note.query.filter_by(project_id=ticket.project_id).all()
    context["notes"] = [{"title": n.title, "content": n.content, "node_id": n.node_id} for n in notes]
    backlog = (
        Ticket.query.filter_by(project_id=ticket.project_id, column_id="backlog")
        .order_by(Ticket.updated_at.desc())
        .limit(10)
        .all()
    )
    in_progress = (
        Ticket.query.filter_by(project_id=ticket.project_id, column_id="in_progress")
        .order_by(Ticket.updated_at.desc())
        .limit(6)
        .all()
    )
    done = (
        Ticket.query.filter_by(project_id=ticket.project_id, column_id="done")
        .order_by(Ticket.updated_at.desc())
        .limit(6)
        .all()
    )
    context["backlog_tickets"] = [_ticket_summary(t) for t in backlog[:10]]
    in_progress_summaries = []
    for t in in_progress:
        if t.id == current_id:
            in_progress_summaries.insert(0, _ticket_summary(t, mark_current=True))
        else:
            in_progress_summaries.append(_ticket_summary(t))
    context["in_progress_tickets"] = in_progress_summaries[:5]
    context["done_tickets"] = [_ticket_summary(t) for t in done[:5]]
    return context
