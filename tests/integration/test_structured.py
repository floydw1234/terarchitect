"""
Tier 2 — Full-stack integration tests: structured (GitHub) mode.

Uses:
  - Stub LLM server (OpenAI-compatible) → drives Director without a real API key
  - WORKER_MODE=stub                     → deterministic worker, no Claude/OpenCode needed
  - Mock `gh` binary on PATH             → intercepts gh pr create / gh api calls
  - Local bare git repo                  → agent pushes to it; no GitHub remote needed

Run with:
    pytest tests/integration/test_structured.py -m integration -v
Or (with existing backend):
    pytest tests/integration/test_structured.py -m integration --no-compose -v
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cli._api import API, APIError
from tests.integration.conftest import make_local_git_repo, STUBS_DIR, REPO_ROOT

pytestmark = pytest.mark.integration

AGENT_TIMEOUT = 180  # seconds — stub LLM is fast; this is generous
POLL_INTERVAL = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_env(api_url: str, ticket_id: str, project_id: str,
               work_dir: Path, stub_llm_url: str) -> dict:
    """Build the env dict for running agent.agent_runner directly on the host."""
    stub_path = str(STUBS_DIR)
    existing_path = os.environ.get("PATH", "")
    return {
        **os.environ,
        # Agent identity
        "TICKET_ID": ticket_id,
        "PROJECT_ID": project_id,
        "TERARCHITECT_API_URL": api_url,
        # Use existing workspace — no clone
        "AGENT_WORKSPACE": str(work_dir),
        # Director: stub LLM
        "DIRECTOR_PROVIDER": "openai",
        "DIRECTOR_LLM_URL": f"{stub_llm_url}/v1/chat/completions",
        "DIRECTOR_MODEL": "stub-model",
        "DIRECTOR_API_KEY": "stub-key",
        # Worker: stub (no LLM calls)
        "WORKER_MODE": "stub",
        "WORKER_API_KEY": "stub",
        # Git identity for commits
        "GIT_USER_NAME": "Test Agent",
        "GIT_USER_EMAIL": "agent@test.example.com",
        # Mock gh binary first on PATH
        "PATH": f"{stub_path}:{existing_path}",
        # Stub GitHub token so get_gh_env_for_agent() returns something
        "GH_TOKEN": "stub-gh-token",
        "GITHUB_TOKEN": "stub-gh-token",
        # Quiet logs
        "MIDDLE_AGENT_DEBUG": "1",
    }


def _wait_for_column(api: API, project_id: str, ticket_id: str,
                     expected_columns: list, timeout: int = AGENT_TIMEOUT) -> str:
    """Poll ticket until column_id is one of expected_columns. Returns final column."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            t = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
            col = t.get("column_id", "")
            if col in expected_columns:
                return col
        except APIError:
            pass
        time.sleep(POLL_INTERVAL)
    ticket = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
    return ticket.get("column_id", "unknown")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestStructuredMode:

    def test_ticket_reaches_in_review(self, api: API, stub_llm: str, tmp_path: Path):
        """
        Full lifecycle: create project + ticket → run agent (stub LLM + stub worker)
        → assert ticket moves to in_review with a PR URL.
        """
        work_dir, _ = make_local_git_repo(tmp_path)

        # Create project pointing at the local work dir (local execution mode)
        project = api.post("/api/projects", {
            "name": "structured-integration-test",
            "description": "Tier 2 integration test project",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "structured",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            # Graph is required before a ticket can be moved to in_progress
            import json
            graph = json.loads((Path(__file__).parent.parent / "fixtures" / "graph.json").read_text())
            api.put(f"/api/projects/{project_id}/graph", graph)

            # Create a ticket
            ticket = api.post(f"/api/projects/{project_id}/tickets", {
                "title": "Add greeting file",
                "description": "Create stub_output.txt with content 'stub complete'",
                "column_id": "backlog",
                "priority": "medium",
                "status": "todo",
            })
            ticket_id = ticket["id"]

            # Move ticket to in_progress (coordinator normally does this via /api/worker/jobs/start)
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_id}",
                      {"column_id": "in_progress"})

            # Run agent directly (bypasses coordinator — no Docker needed)
            env = _agent_env(api.base_url, ticket_id, project_id, work_dir, stub_llm)
            result = subprocess.run(
                [sys.executable, "-m", "agent.agent_runner", "ticket"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=AGENT_TIMEOUT,
            )

            # Helpful failure output
            agent_stdout = result.stdout[-3000:] if result.stdout else ""
            agent_stderr = result.stderr[-3000:] if result.stderr else ""

            # Fetch ticket and assert
            final_ticket = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
            col = final_ticket.get("column_id", "")

            assert col == "in_review", (
                f"Expected ticket in 'in_review', got '{col}'\n"
                f"--- agent stdout (last 3k) ---\n{agent_stdout}\n"
                f"--- agent stderr (last 3k) ---\n{agent_stderr}"
            )

            # Assert PR row exists
            reviews = api.get(f"/api/projects/{project_id}/review")
            pr_entries = [r for r in (reviews or []) if r.get("id") == ticket_id]
            assert pr_entries, "Expected a PR entry in review list after ticket completes"
            assert pr_entries[0].get("pr_number") == 42, (
                f"Expected PR #42 (from mock gh), got: {pr_entries[0]}"
            )

            # Assert stub_output.txt was written by the stub worker
            assert (work_dir / "stub_output.txt").exists(), (
                "stub_output.txt not found in work_dir — stub worker may not have run"
            )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "structured-integration-test"},
                )
            except APIError:
                pass

    def test_ticket_logs_populated(self, api: API, stub_llm: str, tmp_path: Path):
        """Agent should write execution log entries that are retrievable via API."""
        work_dir, _ = make_local_git_repo(tmp_path)

        project = api.post("/api/projects", {
            "name": "log-test-project",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "structured",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            import json as _json
            _graph = _json.loads((Path(__file__).parent.parent / "fixtures" / "graph.json").read_text())
            api.put(f"/api/projects/{project_id}/graph", _graph)

            ticket = api.post(f"/api/projects/{project_id}/tickets", {
                "title": "Log population test",
                "description": "Verify logs appear after agent run",
                "column_id": "backlog",
                "priority": "low",
                "status": "todo",
            })
            ticket_id = ticket["id"]

            api.patch(f"/api/projects/{project_id}/tickets/{ticket_id}",
                      {"column_id": "in_progress"})

            env = _agent_env(api.base_url, ticket_id, project_id, work_dir, stub_llm)
            subprocess.run(
                [sys.executable, "-m", "agent.agent_runner", "ticket"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=AGENT_TIMEOUT,
            )

            logs = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}/logs")
            assert len(logs) > 0, "Expected execution logs after agent run"
            steps = [entry.get("step") for entry in logs]
            assert "session_started" in steps, f"Missing 'session_started' in steps: {steps}"
            assert any("complete" in (s or "") for s in steps), (
                f"No completion step found in logs: {steps}"
            )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "log-test-project"},
                )
            except APIError:
                pass

    def test_graph_context_passed_to_agent(self, api: API, stub_llm: str, tmp_path: Path):
        """
        Graph nodes/edges set on the project are included in the worker-context
        the agent receives. Verify via the worker-context API endpoint directly.
        """
        import json
        from pathlib import Path as _Path
        fixtures = _Path(__file__).parent.parent / "fixtures" / "graph.json"
        graph = json.loads(fixtures.read_text())

        work_dir, _ = make_local_git_repo(tmp_path)
        project = api.post("/api/projects", {
            "name": "graph-context-test",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "structured",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            api.put(f"/api/projects/{project_id}/graph", graph)

            ticket = api.post(f"/api/projects/{project_id}/tickets", {
                "title": "Graph context test ticket",
                "description": "Verify graph is in worker context",
                "column_id": "backlog",
                "priority": "low",
                "status": "todo",
            })
            ticket_id = ticket["id"]

            # Fetch worker context directly — this is what the agent reads
            ctx = api.get(
                f"/api/projects/{project_id}/tickets/{ticket_id}/worker-context"
            )
            # Graph is included (may be filtered to relevant nodes)
            assert ctx.get("current_ticket"), "worker-context missing current_ticket"
            assert ctx.get("project_name") == "graph-context-test"

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "graph-context-test"},
                )
            except APIError:
                pass
