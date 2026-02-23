"""
Phase 2: Standalone runner. Runs one ticket using Phase 1 HTTP API only (no Flask/DB).
Usage: TICKET_ID=... PROJECT_ID=... TERARCHITECT_API_URL=... REPO_URL=... GITHUB_TOKEN=... python -m agent_runner ticket
"""
import os
import sys
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional


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
        # Inject token into HTTPS URL for private repos
        if repo_url.startswith("https://github.com/"):
            from urllib.parse import urlparse
            parsed = urlparse(repo_url)
            netloc = f"{token}@github.com"
            auth_url = f"https://{netloc}{parsed.path}{parsed.query or ''}"
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


def _checkout_branch(dest: str, ticket_id: str) -> bool:
    branch = f"ticket-{ticket_id}"
    r = subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=dest,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        # Branch may already exist
        r2 = subprocess.run(["git", "checkout", branch], cwd=dest, capture_output=True, text=True, timeout=10)
        if r2.returncode != 0:
            print(f"git checkout {branch} failed: {r2.stderr or r2.stdout}", file=sys.stderr)
            return False
    return True


def _ensure_git_config(repo_path: str) -> None:
    """Set git user.name and user.email in the repo so commits succeed (required in fresh clones/containers)."""
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
    github_token = _env("GITHUB_TOKEN", required=False) or os.environ.get("GH_TOKEN", "").strip()
    auth_token = (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip() or None

    try:
        ticket_id = uuid.UUID(ticket_id_str)
        project_id = uuid.UUID(project_id_str)
    except ValueError as e:
        print(f"Error: invalid TICKET_ID or PROJECT_ID: {e}", file=sys.stderr)
        sys.exit(1)

    work_dir = (os.environ.get("AGENT_WORKSPACE") or "").strip()
    if work_dir and os.path.isdir(work_dir):
        # Local execution: use existing path, no clone
        if not _checkout_branch(work_dir, ticket_id_str):
            sys.exit(1)
    else:
        # Docker or default: clone then checkout
        repo_url = _env("REPO_URL")
        work_dir = tempfile.mkdtemp(prefix="terarchitect_runner_")
        if not _clone_repo(repo_url, work_dir, github_token):
            sys.exit(1)
        if not _checkout_branch(work_dir, ticket_id_str):
            sys.exit(1)
    _ensure_git_config(work_dir)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
        print(f"[agent_runner] Worker unavailable (OpenCode): {e}", file=sys.stderr)
        backend.log(project_id, ticket_id, str(uuid.uuid4()), "worker_unavailable", str(e), raw_output=str(e.cause) if getattr(e, "cause", None) else None)
        sys.exit(1)


def run_review() -> None:
    """Run PR review flow: address one review comment. Env: TICKET_ID, PROJECT_ID, PR_NUMBER, COMMENT_BODY; REPO_URL or AGENT_WORKSPACE."""
    base_url = _env("TERARCHITECT_API_URL")
    ticket_id_str = _env("TICKET_ID")
    project_id_str = _env("PROJECT_ID")
    pr_number_str = _env("PR_NUMBER")
    comment_body = _env("COMMENT_BODY", required=False) or ""
    github_token = _env("GITHUB_TOKEN", required=False) or os.environ.get("GH_TOKEN", "").strip()
    auth_token = (os.environ.get("TERARCHITECT_WORKER_API_KEY") or "").strip() or None

    try:
        ticket_id = uuid.UUID(ticket_id_str)
        project_id = uuid.UUID(project_id_str)
        pr_number = int(pr_number_str)
    except (ValueError, TypeError) as e:
        print(f"Error: invalid TICKET_ID, PROJECT_ID, or PR_NUMBER: {e}", file=sys.stderr)
        sys.exit(1)

    work_dir = (os.environ.get("AGENT_WORKSPACE") or "").strip()
    if work_dir and os.path.isdir(work_dir):
        if not _checkout_branch(work_dir, ticket_id_str):
            sys.exit(1)
    else:
        repo_url = _env("REPO_URL")
        work_dir = tempfile.mkdtemp(prefix="terarchitect_runner_")
        if not _clone_repo(repo_url, work_dir, github_token):
            sys.exit(1)
        if not _checkout_branch(work_dir, ticket_id_str):
            sys.exit(1)
    _ensure_git_config(work_dir)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
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
        agent.process_ticket_review(ticket_id, comment_body, pr_number, project_id, work_dir)
    except WorkerUnavailableError as e:
        print(f"[agent_runner] Worker unavailable (OpenCode): {e}", file=sys.stderr)
        backend.log(project_id, ticket_id, str(uuid.uuid4()), "worker_unavailable", str(e), raw_output=str(e.cause) if getattr(e, "cause", None) else None)
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ticket":
        run_ticket()
    elif len(sys.argv) > 1 and sys.argv[1] == "review":
        run_review()
    else:
        print("Usage: python -m agent_runner ticket | review", file=sys.stderr)
        print("  ticket: TICKET_ID, PROJECT_ID, TERARCHITECT_API_URL, REPO_URL, [GITHUB_TOKEN]", file=sys.stderr)
        print("  review: + PR_NUMBER, COMMENT_BODY", file=sys.stderr)
        sys.exit(1)
