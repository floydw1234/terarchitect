"""Unit tests for wave computation logic (no DB, no network)."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# Replicate the pure logic from routes.py so we can test it in isolation
def compute_waves(ticket_dicts: list) -> dict:
    """
    ticket_dicts: list of {"id": str, "depends_on_ticket_ids": [...]}
    Returns {id: wave_num}
    """
    id_to_deps = {
        t["id"]: set(str(d) for d in (t.get("depends_on_ticket_ids") or []))
        for t in ticket_dicts
    }
    known_ids = set(id_to_deps.keys())
    waves = {}
    changed = True
    while changed:
        changed = False
        for tid, deps in id_to_deps.items():
            if tid in waves:
                continue
            local_deps = deps & known_ids
            if any(d not in waves for d in local_deps):
                continue
            w = (max(waves[d] for d in local_deps) + 1) if local_deps else 0
            waves[tid] = w
            changed = True
    for tid in id_to_deps:
        waves.setdefault(tid, 0)
    return waves


class TestComputeWaves:

    def test_no_deps_all_wave_zero(self):
        tickets = [{"id": "a", "depends_on_ticket_ids": []},
                   {"id": "b", "depends_on_ticket_ids": []},
                   {"id": "c", "depends_on_ticket_ids": []}]
        waves = compute_waves(tickets)
        assert waves == {"a": 0, "b": 0, "c": 0}

    def test_simple_chain(self):
        # a → b → c
        tickets = [
            {"id": "a", "depends_on_ticket_ids": []},
            {"id": "b", "depends_on_ticket_ids": ["a"]},
            {"id": "c", "depends_on_ticket_ids": ["b"]},
        ]
        waves = compute_waves(tickets)
        assert waves["a"] == 0
        assert waves["b"] == 1
        assert waves["c"] == 2

    def test_diamond_dependency(self):
        # a, b in wave 0; c depends on both; d depends on c
        tickets = [
            {"id": "a", "depends_on_ticket_ids": []},
            {"id": "b", "depends_on_ticket_ids": []},
            {"id": "c", "depends_on_ticket_ids": ["a", "b"]},
            {"id": "d", "depends_on_ticket_ids": ["c"]},
        ]
        waves = compute_waves(tickets)
        assert waves["a"] == 0
        assert waves["b"] == 0
        assert waves["c"] == 1
        assert waves["d"] == 2

    def test_multiple_roots_different_chains(self):
        tickets = [
            {"id": "a", "depends_on_ticket_ids": []},
            {"id": "b", "depends_on_ticket_ids": ["a"]},
            {"id": "x", "depends_on_ticket_ids": []},
            {"id": "y", "depends_on_ticket_ids": ["x"]},
        ]
        waves = compute_waves(tickets)
        assert waves["a"] == 0
        assert waves["b"] == 1
        assert waves["x"] == 0
        assert waves["y"] == 1

    def test_unknown_dep_ref_ignored(self):
        # dep "z" doesn't exist in the ticket list — treat as no dep
        tickets = [{"id": "a", "depends_on_ticket_ids": ["z"]}]
        waves = compute_waves(tickets)
        assert waves["a"] == 0  # "z" is external, ignored

    def test_circular_dep_gets_wave_zero(self):
        # a → b → a  (cycle): both should fall back to 0
        tickets = [
            {"id": "a", "depends_on_ticket_ids": ["b"]},
            {"id": "b", "depends_on_ticket_ids": ["a"]},
        ]
        waves = compute_waves(tickets)
        assert waves["a"] == 0
        assert waves["b"] == 0

    def test_single_ticket(self):
        waves = compute_waves([{"id": "solo", "depends_on_ticket_ids": []}])
        assert waves == {"solo": 0}

    def test_empty(self):
        assert compute_waves([]) == {}

    def test_wave_grouping_three_waves(self):
        # setup: 6 tickets across 3 waves
        tickets = [
            {"id": "a1", "depends_on_ticket_ids": []},
            {"id": "a2", "depends_on_ticket_ids": []},
            {"id": "b1", "depends_on_ticket_ids": ["a1"]},
            {"id": "b2", "depends_on_ticket_ids": ["a2"]},
            {"id": "c1", "depends_on_ticket_ids": ["b1", "b2"]},
            {"id": "c2", "depends_on_ticket_ids": ["b1"]},
        ]
        waves = compute_waves(tickets)
        assert waves["a1"] == 0
        assert waves["a2"] == 0
        assert waves["b1"] == 1
        assert waves["b2"] == 1
        assert waves["c1"] == 2
        assert waves["c2"] == 2
