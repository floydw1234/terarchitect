"""`ta run --json` emits the docs/AGENT_API.md agent-result envelope."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cli.commands import run_goal


class _StubAPI:
    def __init__(self, frontiers, attempts=None):
        self._frontiers = list(frontiers)
        self._attempts = attempts or []
        self.posts = []
        self.patches = []

    def get(self, path):
        if path.endswith("/attempts"):
            return self._attempts
        if "/tickets/" in path:
            return {"id": "t1", "is_running": False, "column_id": "done"}
        frontier = self._frontiers.pop(0) if len(self._frontiers) > 1 else self._frontiers[0]
        return {"id": "p1", "shipped_frontier": frontier}

    def post(self, path, body):
        self.posts.append((path, body))
        return {"id": "t1"}

    def patch(self, path, body):
        self.patches.append((path, body))
        return {}


def _args(**kw):
    base = dict(project_id="p1", goal="Do it", timeout=5, attempt_count=3, json=True, output="human")
    base.update(kw)
    return SimpleNamespace(**base)


def test_run_json_shipped_by_auto_ship(monkeypatch, capsys):
    monkeypatch.setattr(run_goal, "_POLL_INTERVAL", 0)
    api = _StubAPI(["aaa", "bbb"])
    with pytest.raises(SystemExit) as exc:
        run_goal._cmd_run(_args(), api)
    assert exc.value.code == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema_version"] == 1
    assert doc["status"] == "shipped"
    assert doc["ticket_id"] == "t1"
    assert doc["shipped_frontier_before"] == "aaa"
    assert doc["shipped_frontier_after"] == "bbb"


def test_run_json_failed_without_validated_attempt(monkeypatch, capsys):
    monkeypatch.setattr(run_goal, "_POLL_INTERVAL", 0)
    monkeypatch.setattr(run_goal, "_wait_for_ship", lambda *a, **k: None)
    api = _StubAPI(["aaa"], attempts=[{"id": "a1", "status": "rejected"}])
    with pytest.raises(SystemExit) as exc:
        run_goal._cmd_run(_args(), api)
    assert exc.value.code == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["status"] == "failed"
    assert "validated" in doc["failure_reason"]
    assert doc["needs"]


def test_run_passes_description(monkeypatch, capsys):
    monkeypatch.setattr(run_goal, "_POLL_INTERVAL", 0)
    api = _StubAPI(["aaa", "bbb"])
    with pytest.raises(SystemExit):
        run_goal._cmd_run(_args(description="Full prompt"), api)
    assert api.posts[0][1]["description"] == "Full prompt"
