"""
Tier 2c — Full-stack integration tests: swarm mode with the prod agenthub Docker image.

Uses the pre-built terarchitect-agenthub:latest image (no Go source build required).
The ah binary is extracted from the image at test time.

Requirements:
  - terarchitect-agenthub:latest must exist locally:
      docker compose build agenthub
  - The terarchitect backend + postgres must be running (handled by compose_services fixture
    or --no-compose if already up on localhost:5011).

Run with:
    pytest tests/integration/test_swarm_docker.py -m swarm_docker -v
Or against an already-running backend:
    pytest tests/integration/test_swarm_docker.py -m swarm_docker --no-compose -v
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

pytestmark = pytest.mark.swarm_docker

AGENT_TIMEOUT = 180
SWARM_TICKET_COUNT = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _swarm_env(
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
    existing_path = os.environ.get("PATH", "")
    return {
        **os.environ,
        "TICKET_ID": ticket_id,
        "PROJECT_ID": project_id,
        "TERARCHITECT_API_URL": api_url,
        "TERARCHITECT_MODE": "swarm",
        "AGENTHUB_URL": ah_url,
        "AGENTHUB_API_KEY": ah_api_key,
        "AGENTHUB_AGENT_ID": ah_agent_id,
        "AGENTHUB_BRANCH": "swarm",
        "AGENT_WORKSPACE": str(work_dir),
        "DIRECTOR_PROVIDER": "openai",
        "DIRECTOR_LLM_URL": f"{stub_llm_url}/v1/chat/completions",
        "DIRECTOR_MODEL": "stub-model",
        "DIRECTOR_API_KEY": "stub-key",
        "WORKER_MODE": "stub",
        "WORKER_API_KEY": "stub",
        "GIT_USER_NAME": "Test Agent",
        "GIT_USER_EMAIL": "agent@test.example.com",
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


def _unique_agent_id(prefix: str = "td") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _seed_dag(origin_dir: Path, ah_url: str, admin_key: str, ah_bin_dir: str, tmp_dir: Path) -> str:
    """Push a full bundle of origin HEAD to seed the agenthub DAG."""
    seed_id = _unique_agent_id("seed")
    seed_key = _register_agent(ah_url, admin_key, seed_id)

    seed_clone = tmp_dir / "seed_clone"
    subprocess.run(["git", "clone", str(origin_dir), str(seed_clone)],
                   check=True, capture_output=True)
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
        cwd=seed_clone, env=env, capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"DAG seed push failed (exit {r.returncode}):\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=seed_clone
    ).decode().strip()


def _diag(r: subprocess.CompletedProcess) -> str:
    return f"stdout:\n{(r.stdout or '')[-2000:]}\nstderr:\n{(r.stderr or '')[-2000:]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.swarm_docker
class TestSwarmDockerImage:

    def test_server_health(self, agenthub_docker: dict):
        """The prod Docker container responds to /api/health."""
        import urllib.request
        with urllib.request.urlopen(
            f"{agenthub_docker['url']}/api/health", timeout=5
        ) as resp:
            assert resp.status == 200

    def test_single_agent_pushes_to_dag(
        self,
        api: API,
        stub_llm: str,
        agenthub_docker: dict,
        tmp_path: Path,
    ):
        """A single agent completes a ticket and its commit appears as a DAG leaf."""
        ah_url = agenthub_docker["url"]
        admin_key = agenthub_docker["admin_key"]
        ah_bin_dir = agenthub_docker["ah_bin_dir"]

        agent_id = _unique_agent_id("single")
        api_key = _register_agent(ah_url, admin_key, agent_id)

        work_dir, origin_dir = make_local_git_repo(tmp_path)
        _seed_dag(origin_dir, ah_url, admin_key, ah_bin_dir, tmp_path)

        project = api.post("/api/projects", {
            "name": "docker-swarm-single-test",
            "description": "swarm_docker: single agent push",
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
                "title": "Docker swarm task",
                "description": "Write docker_output.txt",
                "column_id": "backlog",
                "priority": "medium",
                "status": "todo",
            })
            ticket_id = ticket["id"]
            api.patch(f"/api/projects/{project_id}/tickets/{ticket_id}",
                      {"column_id": "in_progress"})

            env = _swarm_env(
                api.base_url, ticket_id, project_id, work_dir,
                stub_llm, ah_url, api_key, agent_id, ah_bin_dir,
            )
            result = _run_agent(env)

            final = api.get(f"/api/projects/{project_id}/tickets/{ticket_id}")
            assert final.get("column_id") == "done", (
                f"Ticket not done.\n{_diag(result)}"
            )

            # Verify commit appears as a leaf in the real DAG
            leaves = _ah_get(ah_url, api_key, "/api/git/leaves")
            agent_leaves = [c for c in leaves if c.get("agent_id") == agent_id]
            assert agent_leaves, (
                f"No leaf from agent '{agent_id}' in DAG. All leaves: {leaves}\n{_diag(result)}"
            )

            # Verify the ticket channel has a done post
            channel = _ticket_channel(ticket_id)
            posts = _ah_get(ah_url, api_key, f"/api/channels/{channel}/posts")
            assert any("done" in (p.get("content") or "") for p in posts), (
                f"No 'done' post in channel '{channel}': {posts}"
            )

        finally:
            try:
                api.delete(f"/api/projects/{project_id}",
                           {"confirm_name": "docker-swarm-single-test"})
            except APIError:
                pass

    def test_three_agents_push_to_dag(
        self,
        api: API,
        stub_llm: str,
        agenthub_docker: dict,
        tmp_path: Path,
    ):
        """Three concurrent agents all push into the real prod DAG and reach done."""
        ah_url = agenthub_docker["url"]
        admin_key = agenthub_docker["admin_key"]
        ah_bin_dir = agenthub_docker["ah_bin_dir"]

        agents = []
        for i in range(SWARM_TICKET_COUNT):
            agent_id = _unique_agent_id(f"t3-{i}")
            api_key = _register_agent(ah_url, admin_key, agent_id)
            agents.append({"id": agent_id, "key": api_key})

        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)],
                       check=True, capture_output=True)
        seed = tmp_path / "seed"
        subprocess.run(["git", "clone", str(origin), str(seed)],
                       check=True, capture_output=True)
        (seed / "README.md").write_text("# Docker swarm test\n")
        subprocess.run(["git", "add", "README.md"], cwd=seed,
                       check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.name=Seed", "-c", "user.email=s@s.com",
                        "commit", "-m", "Initial commit"],
                       cwd=seed, check=True, capture_output=True)
        subprocess.run(["git", "push", "-u", "origin", "HEAD"],
                       cwd=seed, check=True, capture_output=True)

        _seed_dag(origin, ah_url, admin_key, ah_bin_dir, tmp_path)

        work_dirs = []
        for i in range(SWARM_TICKET_COUNT):
            wd = tmp_path / f"work_{i}"
            subprocess.run(["git", "clone", str(origin), str(wd)],
                           check=True, capture_output=True)
            work_dirs.append(wd)

        project = api.post("/api/projects", {
            "name": "docker-swarm-three-test",
            "description": "swarm_docker: 3 concurrent agents",
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

            ticket_ids = []
            for i in range(SWARM_TICKET_COUNT):
                ticket = api.post(f"/api/projects/{project_id}/tickets", {
                    "title": f"Docker swarm task {i + 1}",
                    "description": f"Write output_{i}.txt",
                    "column_id": "backlog",
                    "priority": "medium",
                    "status": "todo",
                })
                ticket_ids.append(ticket["id"])

            for tid in ticket_ids:
                api.patch(f"/api/projects/{project_id}/tickets/{tid}",
                          {"column_id": "in_progress"})

            envs = [
                _swarm_env(
                    api.base_url, ticket_ids[i], project_id, work_dirs[i],
                    stub_llm, ah_url, agents[i]["key"], agents[i]["id"], ah_bin_dir,
                )
                for i in range(SWARM_TICKET_COUNT)
            ]

            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=SWARM_TICKET_COUNT) as pool:
                futures = [pool.submit(_run_agent, env) for env in envs]
                for fut in concurrent.futures.as_completed(futures):
                    results.append(fut.result())

            for i, tid in enumerate(ticket_ids):
                final = api.get(f"/api/projects/{project_id}/tickets/{tid}")
                col = final.get("column_id", "")
                assert col == "done", (
                    f"Ticket {i + 1} ({tid}) expected 'done', got '{col}'\n"
                    + _diag(results[i] if i < len(results) else results[-1])
                )

            leaves = _ah_get(ah_url, agents[0]["key"], "/api/git/leaves")
            assert len(leaves) >= SWARM_TICKET_COUNT, (
                f"Expected >= {SWARM_TICKET_COUNT} leaves in DAG, got {len(leaves)}: {leaves}"
            )

        finally:
            try:
                api.delete(f"/api/projects/{project_id}",
                           {"confirm_name": "docker-swarm-three-test"})
            except APIError:
                pass
