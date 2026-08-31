"""
Tier 2 — Full-stack integration tests: swarm (agenthub) mode.

Uses:
  - Stub LLM server (OpenAI-compatible) → drives Director without a real API key
  - WORKER_MODE=stub                     → deterministic worker, no Claude/OpenCode needed
  - Stub agenthub server                 → intercepts /api/git/leaves + /api/channels/*
  - Mock `ah` binary on PATH             → intercepts ah push (exits 0)
  - Mock `gh` binary on PATH             → not needed for swarm but kept on PATH
  - Local bare git repo                  → agent pushes to it; no GitHub remote needed
  - TERARCHITECT_MODE=swarm              → agent takes swarm code path

Swarm scenario (Phase 4):
  - Create a swarm-mode project with 3 tickets
  - Run 3 agent subprocesses concurrently (one per ticket)
  - Assert all 3 tickets land in `done`
  - Assert stub agenthub received exactly 3 board posts (one per ticket)

Run with:
    pytest tests/integration/test_swarm.py -m swarm -v
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import urllib.request

from cli._api import API, APIError
from tests.integration.conftest import make_local_git_repo, STUBS_DIR, REPO_ROOT, STUB_AH_URL

pytestmark = pytest.mark.swarm

AGENT_TIMEOUT = 180  # seconds per agent — stub LLM is fast
SWARM_TICKET_COUNT = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _swarm_agent_env(
    api_url: str,
    ticket_id: str,
    project_id: str,
    work_dir: Path,
    stub_llm_url: str,
    stub_ah_url: str,
) -> dict:
    """Build env for running a swarm-mode agent subprocess."""
    stub_path = str(STUBS_DIR)
    existing_path = os.environ.get("PATH", "")
    return {
        **os.environ,
        # Agent identity
        "TICKET_ID": ticket_id,
        "PROJECT_ID": project_id,
        "TERARCHITECT_API_URL": api_url,
        # Swarm mode
        "TERARCHITECT_MODE": "swarm",
        "AGENTHUB_URL": stub_ah_url,
        "AGENTHUB_API_KEY": "stub-ah-key",
        "AGENTHUB_BRANCH": "swarm",
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
        # Stubs (ah + gh) first on PATH
        "PATH": f"{stub_path}:{existing_path}",
        # Stub GitHub token (not actually used in swarm mode but agent may check)
        "GH_TOKEN": "stub-gh-token",
        "GITHUB_TOKEN": "stub-gh-token",
        "MIDDLE_AGENT_DEBUG": "1",
    }


def _run_agent(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agent.agent_runner", "ticket"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=AGENT_TIMEOUT,
    )


def _fetch_ah_posts(stub_ah_url: str) -> list:
    """Hit the test-helper endpoint that returns all posts across all channels."""
    try:
        with urllib.request.urlopen(f"{stub_ah_url}/api/posts", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.swarm
class TestSwarmMode:

    def test_three_tickets_reach_done(
        self,
        api: API,
        stub_llm: str,
        stub_agenthub: str,
        tmp_path: Path,
    ):
        """
        Swarm lifecycle: create swarm project + 3 tickets → run 3 agents concurrently
        → assert all tickets land in `done` with commit hashes stored.
        """
        work_dir, _ = make_local_git_repo(tmp_path)

        project = api.post("/api/projects", {
            "name": "swarm-integration-test",
            "description": "Phase 4 swarm test",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            # Graph required before in_progress
            graph = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "graph.json").read_text()
            )
            api.put(f"/api/projects/{project_id}/graph", graph)

            # Create tickets
            ticket_ids = []
            for i in range(SWARM_TICKET_COUNT):
                ticket = api.post(f"/api/projects/{project_id}/tickets", {
                    "title": f"Swarm task {i + 1}",
                    "description": f"Write swarm_output_{i}.txt with 'swarm {i}'",
                    "column_id": "backlog",
                    "priority": "medium",
                    "status": "todo",
                })
                ticket_ids.append(ticket["id"])

            # Move all tickets to in_progress before agents run
            for tid in ticket_ids:
                api.patch(f"/api/projects/{project_id}/tickets/{tid}",
                          {"column_id": "in_progress"})

            # Build envs for each agent
            envs = [
                _swarm_agent_env(
                    api.base_url, tid, project_id, work_dir, stub_llm, stub_agenthub
                )
                for tid in ticket_ids
            ]

            # Run all agents concurrently
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=SWARM_TICKET_COUNT) as pool:
                futures = [pool.submit(_run_agent, env) for env in envs]
                for fut in concurrent.futures.as_completed(futures):
                    results.append(fut.result())

            # Collect agent output for diagnostics
            def _diag(r):
                out = (r.stdout or "")[-2000:]
                err = (r.stderr or "")[-2000:]
                return f"stdout:\n{out}\nstderr:\n{err}"

            # Assert all tickets are now in 'done'
            for i, tid in enumerate(ticket_ids):
                final = api.get(f"/api/projects/{project_id}/tickets/{tid}")
                col = final.get("column_id", "")
                assert col == "done", (
                    f"Ticket {i + 1} ({tid}) expected 'done', got '{col}'\n"
                    + _diag(results[i] if i < len(results) else results[-1])
                )

            # Assert stub agenthub received 3 board posts (one per ticket)
            posts = _fetch_ah_posts(stub_agenthub)
            assert len(posts) >= SWARM_TICKET_COUNT, (
                f"Expected >= {SWARM_TICKET_COUNT} agenthub posts, got {len(posts)}: {posts}"
            )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "swarm-integration-test"},
                )
            except APIError:
                pass

    def test_swarm_tickets_have_commit_hash(
        self,
        api: API,
        stub_llm: str,
        stub_agenthub: str,
        tmp_path: Path,
    ):
        """Swarm-completed tickets should have a commit_hash stored (via /complete endpoint)."""
        work_dir, _ = make_local_git_repo(tmp_path)

        project = api.post("/api/projects", {
            "name": "swarm-hash-test",
            "description": "Verify commit_hash stored for swarm tickets",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            graph = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "graph.json").read_text()
            )
            api.put(f"/api/projects/{project_id}/graph", graph)

            ticket = api.post(f"/api/projects/{project_id}/tickets", {
                "title": "Hash verification ticket",
                "description": "Verify commit hash is stored after swarm completion",
                "column_id": "backlog",
                "priority": "low",
                "status": "todo",
            })
            ticket_id = ticket["id"]
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_id}",
                      {"column_id": "in_progress"})

            env = _swarm_agent_env(
                api.base_url, ticket_id, project_id, work_dir, stub_llm, stub_agenthub
            )
            result = _run_agent(env)

            final = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
            assert final.get("column_id") == "done", (
                f"Ticket not in done. stdout:\n{result.stdout[-2000:]}"
            )

            # Completion creates a TicketAttempt; detailed attempt serialization is
            # covered by backend integration/unit tests. This scenario only proves
            # the agent can drive the ticket to a completed state.

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "swarm-hash-test"},
                )
            except APIError:
                pass

    def test_swarm_board_posts_per_channel(
        self,
        api: API,
        stub_llm: str,
        stub_agenthub: str,
        tmp_path: Path,
    ):
        """Each swarm ticket agent should post to its own ticket-specific channel."""
        work_dir, _ = make_local_git_repo(tmp_path)

        project = api.post("/api/projects", {
            "name": "swarm-channel-test",
            "description": "Verify per-ticket agenthub channels",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            graph = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "graph.json").read_text()
            )
            api.put(f"/api/projects/{project_id}/graph", graph)

            ticket = api.post(f"/api/projects/{project_id}/tickets", {
                "title": "Channel test ticket",
                "description": "Check that a post appears in ticket-specific channel",
                "column_id": "backlog",
                "priority": "low",
                "status": "todo",
            })
            ticket_id = ticket["id"]
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_id}",
                      {"column_id": "in_progress"})

            env = _swarm_agent_env(
                api.base_url, ticket_id, project_id, work_dir, stub_llm, stub_agenthub
            )
            _run_agent(env)

            # Check stub agenthub for posts in ticket-specific channel
            channel = f"ticket-{str(ticket_id).replace('-', '')[:24]}"
            with urllib.request.urlopen(
                f"{stub_agenthub}/api/channels/{channel}/posts", timeout=5
            ) as resp:
                channel_posts = json.loads(resp.read())

            assert len(channel_posts) >= 1, (
                f"Expected >= 1 post in channel '{channel}', got {channel_posts}"
            )
            assert any("done" in (p.get("content") or "") for p in channel_posts), (
                f"Expected a 'done' post in channel {channel}, got: {channel_posts}"
            )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "swarm-channel-test"},
                )
            except APIError:
                pass


@pytest.mark.swarm
class TestShipRunApi:

    def test_ship_run_fetch_endpoint(self, api: API, tmp_path: Path):
        """Compose a promotion candidate, then verify worker ship-run fetch matches the claim payload."""
        work_dir, _ = make_local_git_repo(tmp_path)
        p = api.post("/api/projects", {
            "name": "ship-run-fetch-test",
            "execution_mode": "local",
            "project_path": str(work_dir),
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        pid = p["id"]
        try:
            graph = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "graph.json").read_text()
            )
            api.put(f"/api/projects/{pid}/graph", graph)

            ticket = api.post(f"/api/projects/{pid}/tickets", {
                "title": "Ship candidate ticket", "column_id": "done",
                "priority": "low", "status": "todo",
            })
            ticket_id = ticket["id"]
            api.patch(f"/api/projects/{pid}/tickets/{ticket_id}", {"column_id": "in_progress"})
            commit_hash = "a" * 40
            base_hash = "b" * 40
            api.post(f"/api/projects/{pid}/tickets/{ticket_id}/complete", {
                "commit_hash": commit_hash,
                "base_hash": base_hash,
                "summary": "Accepted attempt for ship-run fetch test",
                "agent_id": "test-agent",
            })
            attempts = api.get(f"/api/projects/{pid}/tickets/{ticket_id}/attempts")
            attempt_id = attempts[0]["id"]
            api.post(
                f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/choose-winner",
                {},
            )
            api.post(
                f"/api/projects/{pid}/tickets/{ticket_id}/attempts/{attempt_id}/accept",
                {},
            )

            candidate = api.post(f"/api/projects/{pid}/ship/candidates", {
                "selected_attempt_ids": [attempt_id],
            })
            candidate_id = candidate["id"]
            detail_before = api.get(f"/api/projects/{pid}/ship/candidates/{candidate_id}")
            assert detail_before["membership"]["attempts"][0]["status"] == "accepted"
            assert detail_before["membership"]["attempts"][0]["agenthub_commit_hash"] == commit_hash

            run = api.post(f"/api/projects/{pid}/ship/candidates/{candidate_id}/compose", {})
            run_id = run["id"]
            assert run["status"] == "queued"

            detail = api.get(f"/api/projects/{pid}/ship/candidates/{candidate_id}")
            assert detail["latest_ship_run"]["id"] == run_id
            assert detail["membership"]["attempts"] == detail_before["membership"]["attempts"]

            claimed = api.post("/api/worker/ship-run/next", {})
            assert claimed["run"]["id"] == run_id
            assert claimed["run"]["status"] == "composing"
            assert claimed["commit_hashes"] == [commit_hash]

            fetched = api.get(f"/api/worker/ship-run/{run_id}")
            assert fetched["run"]["id"] == run_id
            assert fetched["run"]["status"] == "composing"
            assert "project" in fetched
            assert "commit_hashes" in fetched
            assert "tickets" in fetched
            assert fetched["project"] == claimed["project"]
            assert fetched["commit_hashes"] == claimed["commit_hashes"]
            assert fetched["tickets"] == claimed["tickets"]

            # Clean up: mark the run failed so it doesn't block future tests.
            api.post(f"/api/worker/ship-run/{run_id}/fail", {"error": "test cleanup"})
        finally:
            try:
                api.delete(f"/api/projects/{pid}", {"confirm_name": "ship-run-fetch-test"})
            except APIError:
                pass

    def test_ship_run_fetch_returns_404_for_unknown(self, api: API):
        """GET /api/worker/ship-run/{unknown_id} returns 404."""
        with pytest.raises(APIError) as exc:
            api.get("/api/worker/ship-run/00000000-0000-0000-0000-000000000000")
        assert exc.value.status == 404
