"""
Phase 4: Coordinator. Loop: claim job → run agent in Docker (or on host if execution_mode=local) → complete/fail.
Loads coordinator/.env so DIRECTOR_*, WORKER_*, etc. are available when run from repo root.
- Keeps a per-project image tag in state dir (COORDINATOR_STATE_DIR, default ~/.terarchitect/coordinator).
- If docker run fails, job is marked failed and the Docker error is printed to stderr (no host fallback).
- Docker agent containers reach host services (backend, vLLM, etc.) via host.docker.internal; coordinator
  rewrites localhost/127.0.0.1 in env URLs so this works on Mac, Windows (Docker Desktop), and Linux.
Env: TERARCHITECT_API_URL, [TERARCHITECT_WORKER_API_KEY], [PROJECT_ID or PROJECT_IDS (comma; if omitted, fetches project IDs from GET /api/worker/projects, or claims from any project if fetch fails)],
AGENT_IMAGE (default terarchitect-agent), MAX_CONCURRENT_AGENTS (default 1), POLL_INTERVAL_SEC (default 10),
AGENT_CACHE_VOLUME, COORDINATOR_STATE_DIR, COORDINATOR_REPO_ROOT (for direct agent run fallback).
AGENT_DOCKER_MODE: "dind" (default) — run --privileged with an isolated dockerd inside each container;
  "dood" — mount the host Docker socket (legacy Docker-out-of-Docker, shared daemon).
"""
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

import requests

# Load .env from coordinator directory (so "python -m coordinator" from repo root sees coordinator/.env)
_load_dir = Path(__file__).resolve().parent
load_dotenv(_load_dir / ".env")


def _env(key: str, default: Optional[str] = None) -> str:
    return (os.environ.get(key) or "").strip() or (default or "")


def _state_dir() -> Path:
    d = _env("COORDINATOR_STATE_DIR") or os.path.expanduser("~/.terarchitect/coordinator")
    return Path(d)


def _repo_root() -> Path:
    raw = _env("COORDINATOR_REPO_ROOT")
    if raw:
        return Path(raw)
    # When run as python -m coordinator, __file__ is .../coordinator/__main__.py
    return Path(__file__).resolve().parent.parent


_PROJECT_IMAGES_FILE = "project_images.json"
_state_lock = threading.Lock()


def _load_project_images() -> Dict[str, str]:
    path = _state_dir() / _PROJECT_IMAGES_FILE
    with _state_lock:
        if not path.exists():
            return {}
        try:
            data = path.read_text(encoding="utf-8")
            return json.loads(data) if data.strip() else {}
        except Exception:
            return {}


def _save_project_image(project_id: str, image: str) -> None:
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / _PROJECT_IMAGES_FILE
    with _state_lock:
        current = {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        current[project_id] = image
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def _project_ids() -> List[str]:
    raw = _env("PROJECT_IDS") or _env("PROJECT_ID") or ""
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _base_url() -> str:
    url = _env("TERARCHITECT_API_URL")
    if not url:
        print("Error: TERARCHITECT_API_URL is required", file=sys.stderr)
        sys.exit(1)
    return url.rstrip("/")



# Env vars forwarded from coordinator -> agent runtime (docker/local).
# Backend no longer provides agent_env in job payload.
_COORDINATOR_AGENT_ENV_KEYS = (
    "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_AGENT_TOKEN", "github_agent_token",
    "GIT_USER_NAME", "GIT_USER_EMAIL",
    "DIRECTOR_PROVIDER", "DIRECTOR_LLM_URL", "DIRECTOR_MODEL", "DIRECTOR_API_KEY",
    "WORKER_MODE", "WORKER_LLM_URL", "WORKER_MODEL", "WORKER_API_KEY", "WORKER_TIMEOUT_SEC",
    "CLAUDE_CODE_EXTRA_TOOLS",
    "MIDDLE_AGENT_DEBUG",
    "EMBEDDING_PROVIDER", "EMBEDDING_SERVICE_URL", "EMBEDDING_API_KEY",
    "MEMORY_EMBEDDING_MODEL", "MEMORY_EMBEDDING_BASE_URL",
    "MEMORY_LLM_MODEL", "MEMORY_LLM_BASE_URL", "MEMORY_LLM_API_KEY",
    "OPENAI_API_KEY", "openai_api_key",
    "TERARCHITECT_WORKER_API_KEY",
    "OPENCODE_SERVER_URL", "OPENCODE_SERVER_USERNAME", "OPENCODE_SERVER_PASSWORD",
    "AGENTHUB_URL", "AGENTHUB_API_KEY", "AGENTHUB_AGENT_ID",
    "BASE_HASH", "AGENTHUB_ROOT_HASH",
)

def _headers() -> dict:
    token = _env("TERARCHITECT_WORKER_API_KEY")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _max_concurrent(fallback: int = 1) -> int:
    """Read MAX_CONCURRENT_AGENTS from coordinator environment. Set in .env or before starting."""
    raw = _env("MAX_CONCURRENT_AGENTS", str(fallback)) or str(fallback)
    try:
        return max(1, int(raw))
    except (ValueError, TypeError):
        return max(1, fallback)


def fetch_project_ids(base_url: str) -> List[str]:
    """GET /api/worker/projects. Returns list of project id strings, or empty list on failure."""
    try:
        r = requests.get(
            f"{base_url}/api/worker/projects",
            headers=_headers(),
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        projects = data.get("projects") or []
        return [str(p["id"]) for p in projects if p.get("id")]
    except Exception as e:
        print(f"[coordinator] fetch projects error: {e}", file=sys.stderr)
        return []


def claim_job(base_url: str, project_id: Optional[str] = None) -> Optional[dict]:
    """POST /api/worker/jobs/start. If project_id is set, claim next job for that project; else claim next job from any project. Returns job dict or None if 204."""
    try:
        body = {"project_id": project_id} if project_id else {}
        r = requests.post(
            f"{base_url}/api/worker/jobs/start",
            json=body,
            headers=_headers(),
            timeout=30,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[coordinator] claim job error: {e}", file=sys.stderr)
        return None


def claim_workspace_job(base_url: str) -> Optional[dict]:
    """POST /api/worker/workspaces/next. Claims the next workspace queued for composition."""
    try:
        r = requests.post(
            f"{base_url}/api/worker/workspaces/next",
            json={}, headers=_headers(), timeout=30,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[coordinator] claim workspace error: {e}", file=sys.stderr)
        return None


def claim_ship_run(base_url: str) -> Optional[dict]:
    """POST /api/worker/ship-run/next. Claims the next queued merge run.
    Returns the full run payload or None if nothing to do."""
    try:
        r = requests.post(
            f"{base_url}/api/worker/ship-run/next",
            json={},
            headers=_headers(),
            timeout=30,
        )
        if r.status_code == 204:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[coordinator] claim merge run error: {e}", file=sys.stderr)
        return None


# Env vars forwarded from coordinator to workspace composer subprocess.
_COMPOSER_ENV_KEYS = (
    "TERARCHITECT_API_URL", "TERARCHITECT_WORKER_API_KEY",
    "AGENTHUB_URL", "AGENTHUB_API_KEY",
    "WORKSPACE_TEST_COMMAND", "MERGE_TEST_COMMAND",
    "GIT_USER_NAME", "GIT_USER_EMAIL",
)


def _run_workspace_composer(base_url: str, job_data: dict) -> None:
    """Run the workspace composer on the host for a claimed workspace."""
    ws = job_data["workspace"]
    ws_id = ws["id"]
    project_name = job_data["project"].get("name", "")
    print(f"[coordinator] starting workspace composer ws={ws_id} project={project_name!r}", flush=True)

    env = {}
    for key in _COMPOSER_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    env["WORKSPACE_ID"] = str(ws_id)

    repo_root = _repo_root()
    full_env = {**os.environ, **env}
    pythonpath = str(repo_root)
    if full_env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + full_env["PYTHONPATH"]
    full_env["PYTHONPATH"] = pythonpath

    try:
        result = subprocess.run(
            [sys.executable, "-m", "agent.workspace_composer"],
            env=full_env, cwd=str(repo_root),
        )
        if result.returncode == 0:
            print(f"[coordinator] workspace composer {ws_id} completed", flush=True)
        else:
            print(f"[coordinator] workspace composer {ws_id} exited {result.returncode}",
                  file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[coordinator] workspace composer {ws_id} error: {e}", file=sys.stderr, flush=True)


# Env vars forwarded from coordinator to shipper subprocess.
_SHIPPER_ENV_KEYS = (
    "TERARCHITECT_API_URL", "TERARCHITECT_WORKER_API_KEY",
    "AGENTHUB_URL", "AGENTHUB_API_KEY",
    "MERGE_TEST_COMMAND",
    "GIT_USER_NAME", "GIT_USER_EMAIL",
    "GH_TOKEN", "GITHUB_TOKEN",
)


def _run_shipper(base_url: str, run_data: dict) -> None:
    """Run the shipper agent as a subprocess on the host (needs project_path filesystem access).
    Coordinator pre-claimed the run; SHIP_RUN_ID tells shipper which run to process."""
    run_id = run_data["run"]["id"]
    wave_num = run_data["run"].get("wave_num", "?")
    project_name = run_data["project"].get("name", "")
    print(f"[coordinator] starting shipper run={run_id} wave={wave_num} project={project_name!r}", flush=True)

    env = {}
    for key in _SHIPPER_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    env["SHIP_RUN_ID"] = str(run_id)

    repo_root = _repo_root()
    full_env = {**os.environ, **env}
    pythonpath = str(repo_root)
    if full_env.get("PYTHONPATH"):
        pythonpath = pythonpath + os.pathsep + full_env["PYTHONPATH"]
    full_env["PYTHONPATH"] = pythonpath

    try:
        result = subprocess.run(
            [sys.executable, "-m", "agent.shipper"],
            env=full_env,
            cwd=str(repo_root),
        )
        if result.returncode == 0:
            print(f"[coordinator] shipper run {run_id} completed", flush=True)
        else:
            print(f"[coordinator] shipper run {run_id} exited {result.returncode}", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[coordinator] shipper run {run_id} error: {e}", file=sys.stderr, flush=True)


def mark_complete(base_url: str, job_id: str) -> None:
    for attempt in range(3):
        try:
            r = requests.post(
                f"{base_url}/api/worker/jobs/{job_id}/complete",
                headers=_headers(),
                timeout=30,
            )
            if r.status_code in (200, 409):
                return
            r.raise_for_status()
            return
        except Exception as e:
            if attempt == 2:
                print(f"[coordinator] complete error after 3 attempts: {e}", file=sys.stderr)
            else:
                time.sleep(2 ** attempt)


def mark_fail(base_url: str, job_id: str) -> None:
    for attempt in range(3):
        try:
            r = requests.post(
                f"{base_url}/api/worker/jobs/{job_id}/fail",
                headers=_headers(),
                timeout=30,
            )
            if r.status_code in (200, 409):
                return
            r.raise_for_status()
            return
        except Exception as e:
            if attempt == 2:
                print(f"[coordinator] fail error after 3 attempts: {e}", file=sys.stderr)
            else:
                time.sleep(2 ** attempt)


def post_failure_log(
    base_url: str,
    job: dict,
    combined_output: str,
    *,
    exit_code: Optional[int] = None,
    reason: str = "docker",
) -> None:
    """Post failure details to the ticket so the user sees the stacktrace in the UI."""
    project_id = str(job.get("project_id", ""))
    ticket_id = str(job.get("ticket_id", ""))
    job_id = str(job.get("job_id", ""))
    if not project_id or not ticket_id:
        return
    summary = f"Execution failed ({reason})"
    if exit_code is not None:
        summary += f" (exit {exit_code})"
    session_id = f"coordinator-{job_id}"
    payload = {
        "session_id": session_id,
        "step": "failed",
        "summary": summary,
        "raw_output": combined_output,
        "success": False,
    }
    try:
        r = requests.post(
            f"{base_url}/api/projects/{project_id}/tickets/{ticket_id}/logs",
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        if r.ok:
            print(f"[coordinator] posted failure log to ticket {ticket_id}", flush=True)
        else:
            print(f"[coordinator] could not post failure log: {r.status_code} {r.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"[coordinator] post failure log error: {e}", file=sys.stderr)


def job_to_env(job: dict, for_docker: bool = False) -> dict:
    """Build env for container/host from job payload.
    When for_docker=True, only job vars + explicit coordinator-forwarded agent vars are included."""
    env = {} if for_docker else dict(os.environ)
    env["TICKET_ID"] = str(job.get("ticket_id", ""))
    env["PROJECT_ID"] = str(job.get("project_id", ""))
    env["REPO_URL"] = str(job.get("repo_url", ""))
    env["JOB_ID"] = str(job.get("job_id", ""))
    env["JOB_KIND"] = str(job.get("kind", "ticket"))
    env["TERARCHITECT_MODE"] = "swarm"
    # AgentHub DAG selection: ticket jobs always receive an explicit base commit
    # and the current shipped root without any wave-derived meaning.
    if job.get("base_hash"):
        env["BASE_HASH"] = str(job["base_hash"])
    root_hash = job.get("agenthub_root_hash") or job.get("shipped_frontier")
    if root_hash:
        env["AGENTHUB_ROOT_HASH"] = str(root_hash)
    # When execution_mode=local, agent runs on host and uses this path instead of cloning
    if job.get("execution_mode") == "local" and job.get("project_path"):
        env["AGENT_WORKSPACE"] = str(job["project_path"]).strip()
    # App URL: coordinator uses it to claim jobs; container needs to reach host
    if "TERARCHITECT_API_URL" not in env or not env["TERARCHITECT_API_URL"]:
        env["TERARCHITECT_API_URL"] = _env("TERARCHITECT_API_URL", "")

    for key in _COORDINATOR_AGENT_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None and str(val).strip() and (key not in env or not env[key]):
            env[key] = str(val).strip()
    if for_docker:
        # Inside container, localhost is the container. Rewrite any URL with localhost/127.0.0.1 so agent/worker reach host.
        for k, v in list(env.items()):
            if isinstance(v, str) and ("localhost" in v or "127.0.0.1" in v):
                if "http://" in v or "https://" in v:
                    v = v.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
                    env[k] = v
    return env


# Env vars that point to host paths (e.g. Cursor/VS Code git askpass) and break git inside containers.
_DOCKER_STRIP_ENV = frozenset({"GIT_ASKPASS", "SSH_ASKPASS"})

# Keys whose values should be masked in the reproduced shell command written to disk.
_SECRET_ENV_KEYS = frozenset({
    "TERARCHITECT_WORKER_API_KEY", "GH_TOKEN", "GITHUB_TOKEN",
    "AGENT_API_KEY", "WORKER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "AGENTHUB_API_KEY",
})

_RUN_COMMAND_FILE = Path("/tmp") / "terarchitect_run_command.txt"


def _write_run_command(job_id: str, mode: str, *, docker_args: Optional[List[str]] = None, local_cmd: Optional[List[str]] = None, local_env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> None:
    """Write the exact command (and env for local) to coordinator/run_command.txt for debugging/repro."""
    lines = [
        f"# Coordinator run command (job_id={job_id}, mode={mode})",
        "# Copy and run in a shell to reproduce.",
        "",
    ]
    if docker_args is not None:
        # Redact --env-file paths so the run_command.txt file doesn't reference a deleted temp file
        sanitized = []
        skip_next = False
        for arg in docker_args:
            if skip_next:
                sanitized.append("<secrets-env-file>")
                skip_next = False
            elif arg == "--env-file":
                sanitized.append(arg)
                skip_next = True
            else:
                sanitized.append(arg)
        cmd_str = " ".join(shlex.quote(a) for a in sanitized)
        lines.append(cmd_str)
    elif local_cmd is not None and local_env is not None and cwd is not None:
        lines.append(f"# cwd: {cwd}")
        lines.append("")
        for k in sorted(local_env.keys()):
            v = (local_env.get(k) or "")
            if k in _SECRET_ENV_KEYS:
                v = "***"
            else:
                # Escape single quotes for shell: ' -> '\''
                v = v.replace("'", "'\\''")
            lines.append(f"export {k}='{v}'")
        lines.append("")
        lines.append(" ".join(shlex.quote(a) for a in local_cmd))
    else:
        return
    try:
        _RUN_COMMAND_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[coordinator] wrote run command to {_RUN_COMMAND_FILE}", flush=True)
    except Exception as e:
        print(f"[coordinator] could not write run command file: {e}", file=sys.stderr, flush=True)


def _docker_run_args(image: str, job: dict) -> tuple:
    """Build docker run args (env + image). Returns (args, secret_env_path) where secret_env_path is a temp
    file that must be deleted after docker run completes. Cross-platform: container reaches host via
    host.docker.internal. Mac/Windows Docker Desktop provide it; on Linux we add
    --add-host=host.docker.internal:host-gateway. When DOCKER_NETWORK is set (e.g. in compose), add
    --network so agent containers can reach the app. Mounts AGENT_CACHE_VOLUME at /cache so pip and npm
    reuse packages across runs.

    Docker isolation mode (AGENT_DOCKER_MODE):
      "dind" (default) — each agent container starts its own isolated dockerd (--privileged). No shared
        daemon, so concurrent agents never conflict on container names, networks, or ports.
      "dood" — legacy Docker-out-of-Docker: mounts the host socket. All agents share one daemon;
        set AGENT_MOUNT_DOCKER_SOCKET=0 together with DOCKER_HOST to use an external sidecar instead.

    Secrets are written to a temp env-file (--env-file) instead of individual -e flags so they are not
    visible in `ps aux` or Docker daemon logs.
    """
    env = job_to_env(job, for_docker=True)
    for key in _DOCKER_STRIP_ENV:
        env.pop(key, None)

    # Split into secret vars (written to a temp file) and plain vars (passed as -e flags).
    secret_env: dict = {}
    plain_env: dict = {}
    for k, v in env.items():
        if k in _SECRET_ENV_KEYS:
            secret_env[k] = v
        else:
            plain_env[k] = v

    # Write secret vars to a temp file; caller must delete it after docker run.
    # Use /tmp explicitly: on macOS tempfile.gettempdir() returns /var/folders/... which
    # Docker Desktop does not share, but /tmp (→ /private/tmp) is always shared.
    secret_env_path: Optional[str] = None
    if secret_env:
        fd, secret_env_path = tempfile.mkstemp(prefix="terarchitect_env_", suffix=".env", dir="/tmp")
        try:
            with os.fdopen(fd, "w") as f:
                for k, v in secret_env.items():
                    f.write(f"{k}={v}\n")
            os.chmod(secret_env_path, 0o600)
        except Exception:
            try:
                os.unlink(secret_env_path)
            except Exception:
                pass
            secret_env_path = None

    args = ["docker", "run", "--rm"]
    cache_volume = _env("AGENT_CACHE_VOLUME", "terarchitect-agent-cache")
    if cache_volume:
        args.extend(["-v", f"{cache_volume}:/cache"])
    docker_mode = _env("AGENT_DOCKER_MODE", "dind").lower()
    if docker_mode == "dind":
        args.append("--privileged")
    elif _env("AGENT_MOUNT_DOCKER_SOCKET", "1") != "0":
        args.extend(["-v", "/var/run/docker.sock:/var/run/docker.sock"])
    network = _env("DOCKER_NETWORK")
    if network:
        args.extend(["--network", network])
    api_url = env.get("TERARCHITECT_API_URL") or ""
    if "host.docker.internal" in api_url and platform.system() == "Linux":
        args.extend(["--add-host=host.docker.internal:host-gateway"])
    if secret_env_path:
        args.extend(["--env-file", secret_env_path])
    for k, v in plain_env.items():
        if v is not None and v != "":
            args.extend(["-e", f"{k}={v}"])
    args.append(image)
    return args, secret_env_path


def _run_agent_direct(job: dict, docker_error: str, base_url: str, job_id: str = "") -> int:
    """Run the agent on the host (python -m agent.agent_runner). Returns exit code."""
    env = job_to_env(job)
    env["TERARCHITECT_DOCKER_RUN_ERROR"] = docker_error[:8000] if len(docker_error) > 8000 else docker_error
    repo_root = _repo_root()
    full_env = {**os.environ, **env}
    full_env["PYTHONPATH"] = str(repo_root) + (os.pathsep + full_env["PYTHONPATH"] if full_env.get("PYTHONPATH") else "")
    cmd = [sys.executable, "-m", "agent.agent_runner", "ticket"]
    if job_id:
        _write_run_command(job_id, "local", local_cmd=cmd, local_env=full_env, cwd=str(repo_root))
    timeout_sec_raw = _env("WORKER_TIMEOUT_SEC", "")
    timeout_sec: Optional[float] = float(timeout_sec_raw) if timeout_sec_raw else None
    try:
        proc = subprocess.run(
            cmd,
            env=full_env,
            cwd=str(repo_root),
            timeout=timeout_sec,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        print(f"[coordinator] direct run timed out after {timeout_sec}s for job {job_id}", file=sys.stderr)
        return -1
    except FileNotFoundError:
        print(f"[coordinator] direct run failed: agent not found (is COORDINATOR_REPO_ROOT correct? {repo_root})", file=sys.stderr)
        return -1
    except Exception as e:
        print(f"[coordinator] direct run failed: {e}", file=sys.stderr)
        return -1


def _print_docker_error(combined: str, max_chars: int = 4000) -> None:
    """Print Docker run error to console so operator sees it without checking DB."""
    if not combined:
        return
    out = combined[:max_chars] if len(combined) > max_chars else combined
    if len(combined) > max_chars:
        out += f"\n... (truncated, total {len(combined)} chars)"
    print("[coordinator] ----- Docker run error -----", file=sys.stderr, flush=True)
    print(out, file=sys.stderr, flush=True)
    print("[coordinator] ----- end Docker error -----", file=sys.stderr, flush=True)


def _run_job(base_url: str, job_id: str, job: dict, default_image: str, project_images: Dict[str, str]) -> None:
    """Run job: if execution_mode=local run agent on host only; else try docker, then fallback to host on failure."""
    project_id = str(job.get("project_id", ""))
    if job.get("execution_mode") == "local":
        # Local: run agent on host only (AGENT_WORKSPACE set in job_to_env)
        code = _run_agent_direct(job, "", base_url, job_id=job_id)
        if code == 0:
            mark_complete(base_url, job_id)
            print(f"[coordinator] job {job_id} completed (local)")
        else:
            post_failure_log(
                base_url, job,
                f"Local run failed (exit {code}). Check coordinator logs for details.",
                exit_code=code, reason="local",
            )
            mark_fail(base_url, job_id)
            print(f"[coordinator] job {job_id} failed (local exit {code})")
        return
    image = project_images.get(project_id) or default_image
    args, secret_env_path = _docker_run_args(image, job)
    _write_run_command(job_id, "docker", docker_args=args)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=None)
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        combined = f"stdout:\n{out}\n\nstderr:\n{err}" if (out or err) else f"exit code {result.returncode}"
        if result.returncode == 0:
            _save_project_image(project_id, image)
            mark_complete(base_url, job_id)
            print(f"[coordinator] job {job_id} completed (docker)")
            return
        # Docker run failed; post failure to ticket so user sees stacktrace in UI, then mark job failed
        print(f"[coordinator] job {job_id} docker failed (exit {result.returncode})", flush=True)
        _print_docker_error(combined)
        post_failure_log(base_url, job, combined, exit_code=result.returncode, reason="docker")
        mark_fail(base_url, job_id)
    except subprocess.TimeoutExpired:
        post_failure_log(
            base_url, job,
            "Docker run timed out (coordinator did not receive exit from container).",
            reason="docker_timeout",
        )
        mark_fail(base_url, job_id)
        print(f"[coordinator] job {job_id} failed (docker timeout)", file=sys.stderr, flush=True)
    except Exception as e:
        err_msg = str(e)
        print(f"[coordinator] job {job_id} docker error: {e}", file=sys.stderr, flush=True)
        _print_docker_error(err_msg)
        post_failure_log(base_url, job, f"Coordinator error starting or running container:\n{err_msg}", reason="coordinator_error")
        mark_fail(base_url, job_id)
    finally:
        if secret_env_path:
            try:
                os.unlink(secret_env_path)
            except Exception:
                pass


def main() -> None:
    base_url = _base_url()
    project_ids = _project_ids()
    if not project_ids:
        project_ids = fetch_project_ids(base_url)
        if project_ids:
            print(f"[coordinator] fetched {len(project_ids)} project(s) from API", flush=True)
    default_image = _env("AGENT_IMAGE", "terarchitect-agent")
    poll_interval = float(_env("POLL_INTERVAL_SEC", "10") or "10")
    running: List[threading.Thread] = []
    docker_mode = _env("AGENT_DOCKER_MODE", "dind").lower()
    scope = f"projects={project_ids}" if project_ids else "all projects"
    max_concurrent = _max_concurrent(1)
    _project_start_idx = 0  # Round-robin start index for project claims
    print(f"[coordinator] started; scope={scope}, default_image={default_image}, max_concurrent={max_concurrent}, docker_mode={docker_mode}", flush=True)
    print(f"[coordinator] state_dir={_state_dir()}, repo_root={_repo_root()}", flush=True)

    # Reset any jobs left in 'running' state from a previous crashed coordinator.
    # Default: reset jobs running for more than 30 minutes. Override with COORDINATOR_STALE_JOB_AGE_SEC.
    _stale_age_raw = _env("COORDINATOR_STALE_JOB_AGE_SEC", "1800")
    try:
        stale_age = max(0, int(_stale_age_raw))
    except (ValueError, TypeError):
        stale_age = 1800
    try:
        r = requests.post(
            f"{base_url}/api/worker/jobs/reset-stale",
            json={"max_age_seconds": stale_age},
            headers=_headers(),
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("reset", 0) > 0:
                print(f"[coordinator] reset {data['reset']} stale running job(s) from previous session", flush=True)
    except Exception as e:
        print(f"[coordinator] could not reset stale jobs (backend may not be ready yet): {e}", file=sys.stderr)

    # Also reset any ship runs stuck in 'running' from a previous crashed shipper.
    try:
        r2 = requests.post(
            f"{base_url}/api/worker/ship-run/reset-stale",
            json={"max_age_seconds": stale_age},
            headers=_headers(),
            timeout=15,
        )
        if r2.status_code == 200:
            data2 = r2.json()
            if data2.get("reset", 0) > 0:
                print(f"[coordinator] reset {data2['reset']} stale running ship run(s) from previous session", flush=True)
    except Exception as e:
        print(f"[coordinator] could not reset stale ship runs: {e}", file=sys.stderr)

    # Also reset stale workspace composer runs
    try:
        r3 = requests.post(
            f"{base_url}/api/worker/workspaces/reset-stale",
            json={"max_age_seconds": stale_age},
            headers=_headers(), timeout=15,
        )
        if r3.status_code == 200:
            data3 = r3.json()
            if data3.get("reset", 0) > 0:
                print(f"[coordinator] reset {data3['reset']} stale workspace composer run(s)", flush=True)
    except Exception as e:
        print(f"[coordinator] could not reset stale workspace runs: {e}", file=sys.stderr)

    running_mergers: List[threading.Thread] = []
    running_composers: List[threading.Thread] = []

    while True:
        running = [t for t in running if t.is_alive()]
        running_mergers = [t for t in running_mergers if t.is_alive()]

        new_max = _max_concurrent(1)
        if new_max != max_concurrent:
            print(f"[coordinator] max_concurrent changed: {max_concurrent} → {new_max}", flush=True)
            max_concurrent = new_max

        # Claim and start new ticket jobs up to max_concurrent
        while len(running) < max_concurrent:
            job = None
            if project_ids:
                # Round-robin through projects so no single project starves others
                n = len(project_ids)
                for i in range(n):
                    pid = project_ids[(_project_start_idx + i) % n]
                    job = claim_job(base_url, pid)
                    if job is not None:
                        _project_start_idx = (_project_start_idx + i + 1) % n
                        break
            else:
                job = claim_job(base_url)
            if job is None:
                break
            job_id = job.get("job_id", "")
            print(f"[coordinator] claimed job {job_id} (ticket={job.get('ticket_id')}, kind={job.get('kind')})", flush=True)
            project_images = _load_project_images()
            t = threading.Thread(
                target=_run_job,
                args=(base_url, job_id, job, default_image, project_images),
                daemon=False,
            )
            t.start()
            running.append(t)

        # Claim and dispatch ship runs (one shipper at a time)
        if not running_mergers:
            run_data = claim_ship_run(base_url)
            if run_data:
                mt = threading.Thread(
                    target=_run_shipper, args=(base_url, run_data), daemon=False,
                )
                mt.start()
                running_mergers.append(mt)

        # Claim and dispatch workspace composer runs (one at a time)
        running_composers = [t for t in running_composers if t.is_alive()]
        if not running_composers:
            ws_data = claim_workspace_job(base_url)
            if ws_data:
                ct = threading.Thread(
                    target=_run_workspace_composer, args=(base_url, ws_data), daemon=False,
                )
                ct.start()
                running_composers.append(ct)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
