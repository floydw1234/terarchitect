"""
Standalone agent runner. Runs one ticket job using the HTTP API.
Usage: TICKET_ID=... PROJECT_ID=... TERARCHITECT_API_URL=... REPO_URL=... python -m agent_runner ticket

Swarm execution requires an explicit AgentHub base leaf/hash or an explicit debug workspace.
No ticket path may silently start from a clone default branch.
"""
import os
import sys
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

_AGENT_DIR = Path(__file__).resolve().parent.parent
_TOP_LEVEL_IMPORT_ROOTS = (str(_AGENT_DIR), str(_AGENT_DIR.parent))
for _path in reversed(_TOP_LEVEL_IMPORT_ROOTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from middle_agent.git_backend import (
    AgentHubMaterializationError,
    materialize_workspace_from_agenthub,
)


def _env(key: str, required: bool = True) -> str:
    val = (os.environ.get(key) or "").strip()
    if required and not val:
        print(f"Error: {key} is required", file=sys.stderr)
        sys.exit(1)
    return val


def _clone_repo(repo_url: str, dest: str, token: Optional[str]) -> bool:
    """Clone repo into dest. Uses GITHUB_TOKEN for auth if set. Returns True on success."""
    env = dict(os.environ)
    if token:
        env["GITHUB_TOKEN"] = token
        if repo_url.startswith("https://github.com/"):
            from urllib.parse import urlparse
            parsed = urlparse(repo_url)
            auth_url = f"https://{token}@github.com{parsed.path}{parsed.query or ''}"
        else:
            auth_url = repo_url
    else:
        auth_url = repo_url
    r = subprocess.run(
        ["git", "clone", "--depth", "1", auth_url, dest],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if r.returncode != 0:
        print(f"git clone failed: {r.stderr or r.stdout}", file=sys.stderr)
        return False
    return True


def _ensure_git_config(repo_path: str) -> None:
    """Set git user.name and user.email so commits succeed in fresh clones/containers."""
    name = (os.environ.get("GIT_USER_NAME") or "Terarchitect Agent").strip()
    email = (os.environ.get("GIT_USER_EMAIL") or "agent@terarchitect.local").strip()
    for key, val in [("user.name", name), ("user.email", email)]:
        subprocess.run(
            ["git", "config", key, val],
            cwd=repo_path,
            capture_output=True,
            timeout=5,
        )


def run_ticket() -> None:
    base_url = _env("TERARCHITECT_API_URL")
    ticket_id_str = _env("TICKET_ID")
    project_id_str = _env("PROJECT_ID")
    github_token = (
        _env("GITHUB_TOKEN", required=False)
        or os.environ.get("GH_TOKEN", "").strip()
        or os.environ.get("GITHUB_AGENT_TOKEN", "").strip()
        or os.environ.get("github_agent_token", "").strip()
    )
    auth_token = (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip() or None

    try:
        ticket_id = uuid.UUID(ticket_id_str)
        project_id = uuid.UUID(project_id_str)
    except ValueError as e:
        print(f"Error: invalid TICKET_ID or PROJECT_ID: {e}", file=sys.stderr)
        sys.exit(1)

    base_leaf_id = (os.environ.get("BASE_LEAF_ID") or "").strip()
    base_hash = (os.environ.get("BASE_HASH") or "").strip()
    work_dir = (os.environ.get("AGENT_WORKSPACE") or "").strip()
    if base_leaf_id or base_hash:
        base_ref = base_leaf_id or base_hash
        try:
            work_dir = materialize_workspace_from_agenthub(base_ref)
        except AgentHubMaterializationError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    elif work_dir and os.path.isdir(work_dir):
        # Explicit local/debug execution mode: use the provided workspace as-is.
        pass
    else:
        print(
            "Error: swarm execution requires BASE_LEAF_ID or BASE_HASH. "
            "Rerun the ticket from the current frontier or import the project into AgentHub explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)

    _ensure_git_config(work_dir)

    from middle_agent.backend import HttpAgentBackend
    from middle_agent.agent import MiddleAgent, WorkerUnavailableError

    backend = HttpAgentBackend(base_url=base_url, auth_token=auth_token)
    docker_error = os.environ.get("TERARCHITECT_DOCKER_RUN_ERROR", "").strip()
    if docker_error:
        print("[agent_runner] Running on host after Docker failed; error passed to backend log.", file=sys.stderr)
        backend.log(
            project_id, ticket_id, str(uuid.uuid4()),
            "docker_run_fallback",
            "Docker run failed; coordinator ran agent on host. Error from Docker (fix or ignore):",
            raw_output=docker_error,
        )
    agent = MiddleAgent(backend=backend)
    try:
        agent.process_ticket(ticket_id, project_path=work_dir, project_id=project_id)
    except WorkerUnavailableError as e:
        print(f"[agent_runner] Worker unavailable: {e}", file=sys.stderr)
        backend.log(project_id, ticket_id, str(uuid.uuid4()), "worker_unavailable", str(e),
                    raw_output=str(e.cause) if getattr(e, "cause", None) else None)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ticket":
        run_ticket()
    else:
        print("Usage: python -m agent_runner ticket", file=sys.stderr)
        print("  Required: TICKET_ID, PROJECT_ID, TERARCHITECT_API_URL, REPO_URL, [GITHUB_TOKEN]", file=sys.stderr)
        sys.exit(1)
