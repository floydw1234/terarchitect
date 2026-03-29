"""Unit tests for the planning agent (no LLM calls, no backend)."""

import json
import os
import sys
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from agent.planner.planner import _build_prompt, _SYSTEM_PROMPT


def _make_graph(n_nodes=3, n_edges=2):
    nodes = [{"id": f"node-{i}", "label": f"Service {i}", "type": "service"} for i in range(n_nodes)]
    edges = [
        {"id": f"edge-{i}", "source": f"node-{i}", "target": f"node-{i+1}", "label": f"calls"}
        for i in range(n_edges)
    ]
    return {"nodes": nodes, "edges": edges}


class TestBuildPrompt:
    def test_includes_node_ids(self):
        project = {"name": "MyApp", "description": "A cool app"}
        graph = _make_graph()
        notes = []
        prompt = _build_prompt(project, graph, notes, max_tickets=10)
        assert "node-0" in prompt
        assert "node-1" in prompt
        assert "node-2" in prompt

    def test_includes_edge_ids(self):
        project = {"name": "MyApp", "description": ""}
        graph = _make_graph()
        notes = []
        prompt = _build_prompt(project, graph, notes, max_tickets=10)
        assert "edge-0" in prompt
        assert "edge-1" in prompt

    def test_global_notes_appear_in_objectives(self):
        project = {"name": "MyApp", "description": ""}
        graph = _make_graph(1, 0)
        notes = [
            {"content": "Build a login system", "node_id": None, "edge_id": None},
            {"content": "Add rate limiting", "node_id": None, "edge_id": None},
        ]
        prompt = _build_prompt(project, graph, notes, max_tickets=10)
        assert "Build a login system" in prompt
        assert "Add rate limiting" in prompt

    def test_node_notes_are_attached_to_nodes(self):
        project = {"name": "MyApp", "description": ""}
        graph = _make_graph(2, 0)
        notes = [
            {"content": "Must support OAuth2", "node_id": "node-0", "edge_id": None},
        ]
        prompt = _build_prompt(project, graph, notes, max_tickets=10)
        assert "Must support OAuth2" in prompt
        # Should appear near the node section, not just objectives
        node_section_idx = prompt.index("## Architectural Graph — Nodes")
        note_idx = prompt.index("Must support OAuth2")
        assert note_idx > node_section_idx

    def test_falls_back_to_description_when_no_notes(self):
        project = {"name": "MyApp", "description": "Build a task manager"}
        graph = _make_graph(1, 0)
        notes = []
        prompt = _build_prompt(project, graph, notes, max_tickets=10)
        assert "Build a task manager" in prompt

    def test_empty_graph_handled_gracefully(self):
        project = {"name": "MyApp", "description": "desc"}
        graph = {"nodes": [], "edges": []}
        notes = []
        prompt = _build_prompt(project, graph, notes, max_tickets=5)
        assert "no nodes" in prompt.lower() or "(no nodes" in prompt


class TestSystemPrompt:
    def test_max_tickets_interpolated(self):
        rendered = _SYSTEM_PROMPT.format(max_tickets=15)
        assert "15" in rendered

    def test_contains_required_fields(self):
        rendered = _SYSTEM_PROMPT.format(max_tickets=10)
        assert "associated_node_ids" in rendered
        assert "associated_edge_ids" in rendered
        assert "depends_on_titles" in rendered
        assert "priority" in rendered
