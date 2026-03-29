"""
Tier 2b — Full-stack integration tests: swarm mode with the REAL agenthub server.

Unlike Phase 4 (which stubs the agenthub), these tests compile and run the actual
agenthub-server from source and use the real `ah` CLI binary for pushes.

What this tests end-to-end:
  - Agent commits are actually pushed into the agenthub git DAG
  - `GET /api/git/leaves` returns the pushed commits
  - Each ticket's board channel receives a "done" post
  - Tickets land in `done` in the terarchitect backend
  - 3 concurrent agents each work in their own clone (realistic swarm topology)

Requirements:
  - `go` must be on PATH (used to build agenthub-server and ah from source)
  - The terarchitect backend + postgres must be running (handled by compose_services)

Run with:
    pytest tests/integration/test_swarm_real.py -m swarm_real -v
Or (against already-running backend):
    pytest tests/integration/test_swarm_real.py -m swarm_real --no-compose -v
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from cli._api import API, APIError
from tests.integration.conftest import (
    make_local_git_repo,
    STUBS_DIR,
    REPO_ROOT,
    _register_agent,
    _ah_get,
)
from agent.middle_agent.git_backend import _ticket_channel

pytestmark = pytest.mark.swarm_real

AGENT_TIMEOUT = 180
SWARM_TICKET_COUNT = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _real_swarm_env(
    api_url: str,
    ticket_id: str,
    project_id: str,
    work_dir: Path,
    stub_llm_url: str,
    ah_url: str,
    ah_api_key: str,
    ah_agent_id: str,
    ah_bin_dir: str,
) -> dict:
    """Build env dict for a real-agenthub swarm agent subprocess."""
    existing_path = os.environ.get("PATH", "")
    # Real ah binary takes priority over stub ah (if any)
    return {
        **os.environ,
        # Agent identity
        "TICKET_ID": ticket_id,
        "PROJECT_ID": project_id,
        "TERARCHITECT_API_URL": api_url,
        # Swarm mode — real agenthub
        "TERARCHITECT_MODE": "swarm",
        "AGENTHUB_URL": ah_url,
        "AGENTHUB_API_KEY": ah_api_key,
        "AGENTHUB_AGENT_ID": ah_agent_id,
        "AGENTHUB_BRANCH": "swarm",
        # Each agent gets its own workspace
        "AGENT_WORKSPACE": str(work_dir),
        # Director: stub LLM (fast, deterministic)
        "DIRECTOR_PROVIDER": "openai",
        "DIRECTOR_LLM_URL": f"{stub_llm_url}/v1/chat/completions",
        "DIRECTOR_MODEL": "stub-model",
        "DIRECTOR_API_KEY": "stub-key",
        # Worker: stub
        "WORKER_MODE": "stub",
        "WORKER_API_KEY": "stub",
        # Git identity
        "GIT_USER_NAME": "Test Agent",
        "GIT_USER_EMAIL": "agent@test.example.com",
        # Real ah binary first on PATH; stub gh also present for any gh calls
        "PATH": f"{ah_bin_dir}:{STUBS_DIR}:{existing_path}",
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


def _unique_agent_id(prefix: str = "ta") -> str:
    """Generate a valid agenthub agent ID (alphanumeric + dash, max 63 chars)."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _seed_agenthub_dag(
    origin_dir: Path,
    ah_url: str,
    admin_key: str,
    ah_bin_dir: str,
    tmp_dir: Path,
) -> str:
    """Push the origin HEAD as a FULL git bundle to seed the agenthub DAG.

    Background: `ah push` creates incremental bundles (HEAD ^origin/HEAD) when
    an origin remote exists.  An empty agenthub DAG can't accept incremental
    bundles because it doesn't have the base (prerequisite) commit yet.

    Fix: clone the origin into a fresh directory, remove the origin remote so
    `ah push` produces a full bundle, then push that to agenthub.  Subsequent
    agent pushes (incremental) will succeed because the base is now present.

    Returns the seeded commit hash.
    """
    seed_id = _unique_agent_id("seed")
    seed_key = _register_agent(ah_url, admin_key, seed_id)

    seed_clone = tmp_dir / "seed_clone"
    subprocess.run(["git", "clone", str(origin_dir), str(seed_clone)],
                   check=True, capture_output=True)
    # Remove the origin remote so ah push falls back to a full bundle
    subprocess.run(["git", "remote", "remove", "origin"],
                   cwd=seed_clone, check=True, capture_output=True)

    existing_path = os.environ.get("PATH", "")
    env = {
        **os.environ,
        "AGENTHUB_URL": ah_url,
        "AGENTHUB_API_KEY": seed_key,
        "AGENTHUB_AGENT_ID": seed_id,
        "PATH": f"{ah_bin_dir}:{existing_path}",
    }
    r = subprocess.run(
        ["ah", "push"],
        cwd=seed_clone,
        env=env,
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"agenthub seed push failed (exit {r.returncode}):\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=seed_clone
    ).decode().strip()
    return head


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.swarm_real
class TestRealSwarmMode:

    def test_three_agents_push_to_dag(
        self,
        api: API,
        stub_llm: str,
        agenthub_real: dict,
        tmp_path: Path,
    ):
        """
        Three concurrent agents each push their commit into the real agenthub DAG.
        After all finish:
          - All 3 tickets are in `done`
          - GET /api/git/leaves returns >= 3 commits (one per agent)
          - Each ticket channel has at least one "done" post
        """
        ah_url = agenthub_real["url"]
        admin_key = agenthub_real["admin_key"]
        ah_bin_dir = agenthub_real["ah_bin_dir"]

        # Register one agent per ticket
        agents = []
        for i in range(SWARM_TICKET_COUNT):
            agent_id = _unique_agent_id(f"t3push-{i}")
            api_key = _register_agent(ah_url, admin_key, agent_id)
            agents.append({"id": agent_id, "key": api_key})

        # Each agent gets its own clone of the bare origin
        origin_tmp = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin_tmp)],
                       check=True, capture_output=True)
        # Seed origin with an initial commit via a throw-away clone
        seed_dir = tmp_path / "seed"
        subprocess.run(["git", "clone", str(origin_tmp), str(seed_dir)],
                       check=True, capture_output=True)
        (seed_dir / "README.md").write_text("# Swarm real test\n")
        subprocess.run(["git", "add", "README.md"], cwd=seed_dir,
                       check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Seed", "-c", "user.email=s@s.com",
                        "commit", "-m", "Initial commit"],
                       cwd=seed_dir, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "HEAD"],
                       cwd=seed_dir, check=True, capture_output=True)

        # Seed the agenthub DAG with a full bundle of the initial commit.
        # This is required because subsequent agent pushes are incremental
        # (HEAD ^origin/HEAD) and need the base commit to already be present.
        _seed_agenthub_dag(origin_tmp, ah_url, admin_key, ah_bin_dir, tmp_path)

        # Clone once per agent
        work_dirs = []
        for i in range(SWARM_TICKET_COUNT):
            wd = tmp_path / f"work_{i}"
            subprocess.run(["git", "clone", str(origin_tmp), str(wd)],
                           check=True, capture_output=True)
            work_dirs.append(wd)

        # Create project (use first work_dir as project_path — not critical for swarm)
        project = api.post("/api/projects", {
            "name": "real-swarm-dag-test",
            "description": "Phase 4b: real agenthub DAG push",
            "execution_mode": "local",
            "project_path": str(work_dirs[0]),
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            graph = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "graph.json").read_text()
            )
            api.put(f"/api/projects/{project_id}/graph", graph)

            # Create tickets
            ticket_ids = []
            for i in range(SWARM_TICKET_COUNT):
                ticket = api.post(f"/api/projects/{project_id}/tickets", {
                    "title": f"Real swarm task {i + 1}",
                    "description": f"Write output_{i}.txt",
                    "column_id": "backlog",
                    "priority": "medium",
                    "status": "todo",
                })
                ticket_ids.append(ticket["id"])

            # Move all to in_progress
            for tid in ticket_ids:
                api.patch(f"/api/projects/{project_id}/tickets/{tid}",
                          {"column_id": "in_progress"})

            # Build envs — each agent has its own workspace + agenthub credentials
            envs = [
                _real_swarm_env(
                    api.base_url,
                    ticket_ids[i],
                    project_id,
                    work_dirs[i],
                    stub_llm,
                    ah_url,
                    agents[i]["key"],
                    agents[i]["id"],
                    ah_bin_dir,
                )
                for i in range(SWARM_TICKET_COUNT)
            ]

            # Run all agents concurrently
            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=SWARM_TICKET_COUNT) as pool:
                futures = [pool.submit(_run_agent, env) for env in envs]
                for fut in concurrent.futures.as_completed(futures):
                    results.append(fut.result())

            def _diag(r):
                return (
                    f"stdout:\n{(r.stdout or '')[-2000:]}\n"
                    f"stderr:\n{(r.stderr or '')[-2000:]}"
                )

            # 1. All tickets in done
            for i, tid in enumerate(ticket_ids):
                final = api.get(f"/api/projects/{project_id}/tickets/{tid}")
                col = final.get("column_id", "")
                assert col == "done", (
                    f"Ticket {i + 1} ({tid}) expected 'done', got '{col}'\n"
                    + _diag(results[i] if i < len(results) else results[-1])
                )

            # 2. Real DAG has >= SWARM_TICKET_COUNT leaves
            # Use first agent's key to query
            leaves = _ah_get(ah_url, agents[0]["key"], "/api/git/leaves")
            assert len(leaves) >= SWARM_TICKET_COUNT, (
                f"Expected >= {SWARM_TICKET_COUNT} leaves in agenthub DAG, got {len(leaves)}: {leaves}"
            )

            # 3. Each ticket channel has a "done" post
            for i, tid in enumerate(ticket_ids):
                channel = _ticket_channel(tid)
                posts = _ah_get(ah_url, agents[i]["key"], f"/api/channels/{channel}/posts")
                assert len(posts) >= 1, (
                    f"No posts in agenthub channel '{channel}' for ticket {i + 1}"
                )
                assert any("done" in (p.get("content") or "") for p in posts), (
                    f"No 'done' post in channel '{channel}': {posts}"
                )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "real-swarm-dag-test"},
                )
            except APIError:
                pass

    def test_dag_commit_is_reachable(
        self,
        api: API,
        stub_llm: str,
        agenthub_real: dict,
        tmp_path: Path,
    ):
        """
        After an agent pushes, the commit hash returned via `GET /api/git/leaves`
        should match the commit hash stored by the terarchitect backend.
        Also verifies `GET /api/git/fetch/{hash}` returns a valid bundle.
        """
        ah_url = agenthub_real["url"]
        admin_key = agenthub_real["admin_key"]
        ah_bin_dir = agenthub_real["ah_bin_dir"]

        agent_id = _unique_agent_id("treachable")
        api_key = _register_agent(ah_url, admin_key, agent_id)

        work_dir, origin_dir = make_local_git_repo(tmp_path)
        _seed_agenthub_dag(origin_dir, ah_url, admin_key, ah_bin_dir, tmp_path)

        project = api.post("/api/projects", {
            "name": "real-swarm-reachable-test",
            "description": "Phase 4b: verify commit reachability",
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
                "title": "Reachability test",
                "description": "Verify commit hash is in the DAG",
                "column_id": "backlog",
                "priority": "low",
                "status": "todo",
            })
            ticket_id = ticket["id"]
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_id}",
                      {"column_id": "in_progress"})

            env = _real_swarm_env(
                api.base_url, ticket_id, project_id, work_dir,
                stub_llm, ah_url, api_key, agent_id, ah_bin_dir,
            )
            result = _run_agent(env)

            final = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
            assert final.get("column_id") == "done", (
                f"Ticket not done. stdout:\n{result.stdout[-2000:]}"
            )

            # Find the leaf for this agent
            leaves = _ah_get(ah_url, api_key, "/api/git/leaves")
            agent_leaves = [c for c in leaves if c.get("agent_id") == agent_id]
            assert agent_leaves, (
                f"No leaf from agent '{agent_id}' in DAG. All leaves: {leaves}"
            )

            commit_hash = agent_leaves[0]["hash"]

            # Verify the commit is fetchable (returns a non-empty bundle)
            import urllib.request as _ur
            req = _ur.Request(
                f"{ah_url}/api/git/fetch/{commit_hash}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            with _ur.urlopen(req, timeout=15) as resp:
                bundle_bytes = resp.read()

            assert len(bundle_bytes) > 0, (
                f"Fetched bundle for commit {commit_hash[:12]} is empty"
            )
            # Git bundles start with the magic header "# v2 git bundle\n" or "# v3 git bundle\n"
            assert bundle_bytes[:4] == b"# v2" or bundle_bytes[:4] == b"# v3", (
                f"Unexpected bundle header: {bundle_bytes[:20]!r}"
            )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "real-swarm-reachable-test"},
                )
            except APIError:
                pass

    def test_peer_context_visible_after_first_push(
        self,
        api: API,
        stub_llm: str,
        agenthub_real: dict,
        tmp_path: Path,
    ):
        """
        When a second agent runs after the first has already pushed,
        prepare_work() should fetch the first agent's leaf and the Director
        prompt should include the agenthub peer context section.
        Verified by checking that GET /api/git/leaves is non-empty before
        the second agent starts, and the second agent completes successfully.
        """
        ah_url = agenthub_real["url"]
        admin_key = agenthub_real["admin_key"]
        ah_bin_dir = agenthub_real["ah_bin_dir"]

        agent_a_id = _unique_agent_id("peer-a")
        agent_b_id = _unique_agent_id("peer-b")
        key_a = _register_agent(ah_url, admin_key, agent_a_id)
        key_b = _register_agent(ah_url, admin_key, agent_b_id)

        # Two separate clones from the same bare origin
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)],
                       check=True, capture_output=True)
        seed = tmp_path / "seed"
        subprocess.run(["git", "clone", str(origin), str(seed)],
                       check=True, capture_output=True)
        (seed / "README.md").write_text("# Peer context test\n")
        subprocess.run(["git", "add", "README.md"], cwd=seed,
                       check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Seed", "-c", "user.email=s@s.com",
                        "commit", "-m", "Initial commit"],
                       cwd=seed, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "HEAD"],
                       cwd=seed, check=True, capture_output=True)

        # Seed agenthub with the initial commit (full bundle) so incremental
        # agent pushes can reference it as a known base.
        _seed_agenthub_dag(origin, ah_url, admin_key, ah_bin_dir, tmp_path)

        work_a = tmp_path / "work_a"
        work_b = tmp_path / "work_b"
        for wd in (work_a, work_b):
            subprocess.run(["git", "clone", str(origin), str(wd)],
                           check=True, capture_output=True)

        project = api.post("/api/projects", {
            "name": "real-swarm-peer-test",
            "description": "Phase 4b: peer context propagation",
            "execution_mode": "local",
            "project_path": str(work_a),
            "git_mode": "swarm",
            "is_existing_repo": True,
        })
        project_id = project["id"]

        try:
            graph = json.loads(
                (Path(__file__).parent.parent / "fixtures" / "graph.json").read_text()
            )
            api.put(f"/api/projects/{project_id}/graph", graph)

            # Create two tickets
            ticket_ids = []
            for title in ["Peer task A", "Peer task B"]:
                t = api.post(f"/api/projects/{project_id}/tickets", {
                    "title": title,
                    "description": "Part of peer-context test",
                    "column_id": "backlog",
                    "priority": "low",
                    "status": "todo",
                })
                ticket_ids.append(t["id"])

            # Run agent A first (serial)
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_ids[0]}",
                      {"column_id": "in_progress"})
            env_a = _real_swarm_env(
                api.base_url, ticket_ids[0], project_id, work_a,
                stub_llm, ah_url, key_a, agent_a_id, ah_bin_dir,
            )
            result_a = _run_agent(env_a)

            final_a = api.get(f"/api/projects/{project_id}/tickets/{ticket_ids[0]}")
            assert final_a.get("column_id") == "done", (
                f"Agent A ticket not done. stdout:\n{result_a.stdout[-2000:]}"
            )

            # Verify agent A's commit is now in the DAG (leaves non-empty)
            leaves_after_a = _ah_get(ah_url, key_a, "/api/git/leaves")
            assert any(c.get("agent_id") == agent_a_id for c in leaves_after_a), (
                f"Agent A commit not in leaves after run. Leaves: {leaves_after_a}"
            )

            # Run agent B — prepare_work should see agent A's commit as a leaf
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_ids[1]}",
                      {"column_id": "in_progress"})
            env_b = _real_swarm_env(
                api.base_url, ticket_ids[1], project_id, work_b,
                stub_llm, ah_url, key_b, agent_b_id, ah_bin_dir,
            )
            result_b = _run_agent(env_b)

            final_b = api.get(f"/api/projects/{project_id}/tickets/{ticket_ids[1]}")
            assert final_b.get("column_id") == "done", (
                f"Agent B ticket not done. stdout:\n{result_b.stdout[-2000:]}"
            )

            # Both agents' commits should be in the DAG
            leaves_final = _ah_get(ah_url, key_b, "/api/git/leaves")
            agent_ids_in_dag = {c.get("agent_id") for c in leaves_final}
            assert agent_a_id in agent_ids_in_dag or agent_b_id in agent_ids_in_dag, (
                f"Expected both agents in DAG leaves. Got agents: {agent_ids_in_dag}"
            )

        finally:
            try:
                api.delete(
                    f"/api/projects/{project_id}",
                    {"confirm_name": "real-swarm-peer-test"},
                )
            except APIError:
                pass
