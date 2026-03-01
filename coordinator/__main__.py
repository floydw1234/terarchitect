"""
Phase 4: Coordinator. Loop: claim job → run agent in Docker (or on host if execution_mode=local) → complete/fail.
- Keeps a per-project image tag in state dir (COORDINATOR_STATE_DIR, default ~/.terarchitect/coordinator).
- If docker run fails, job is marked failed and the Docker error is printed to stderr (no host fallback).
- Docker agent containers reach host services (backend, vLLM, etc.) via host.docker.internal; coordinator
  rewrites localhost/127.0.0.1 in env URLs so this works on Mac, Windows (Docker Desktop), and Linux.
Env: TERARCHITECT_API_URL, [TERARCHITECT_WORKER_API_KEY], [PROJECT_ID or PROJECT_IDS (comma; if omitted, claims from any project)],
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
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests


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


def _headers() -> dict:
    token = _env("TERARCHITECT_WORKER_API_KEY")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def fetch_max_concurrent(base_url: str, fallback: int) -> int:
    """Fetch MAX_CONCURRENT_AGENTS from the backend settings API. Returns fallback on any error or if unset.
    Called each poll cycle so the value can be changed in the UI without restarting the coordinator."""
    try:
        r = requests.get(f"{base_url}/api/settings", headers=_headers(), timeout=10)
        r.raise_for_status()
        raw = r.json().get("MAX_CONCURRENT_AGENTS")
        if raw is not None and str(raw).strip():
            val = int(str(raw).strip())
            return max(1, val)
    except Exception:
        pass
    return fallback


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


def mark_complete(base_url: str, job_id: str) -> None:
    try:
        requests.post(
            f"{base_url}/api/worker/jobs/{job_id}/complete",
            headers=_headers(),
            timeout=30,
        )
    except Exception as e:
        print(f"[coordinator] complete error: {e}", file=sys.stderr)


def mark_fail(base_url: str, job_id: str) -> None:
    try:
        requests.post(
            f"{base_url}/api/worker/jobs/{job_id}/fail",
            headers=_headers(),
            timeout=30,
        )
    except Exception as e:
        print(f"[coordinator] fail error: {e}", file=sys.stderr)


def job_to_env(job: dict, for_docker: bool = False) -> dict:
    """Build env for container/host from job payload. Job may include agent_env from backend (Settings/DB).
    When for_docker=True, only job + agent_env vars are included (no host os.environ dump)."""
    env = {} if for_docker else dict(os.environ)
    env["TICKET_ID"] = str(job.get("ticket_id", ""))
    env["PROJECT_ID"] = str(job.get("project_id", ""))
    env["REPO_URL"] = str(job.get("repo_url", ""))
    env["JOB_ID"] = str(job.get("job_id", ""))
    env["JOB_KIND"] = str(job.get("kind", "ticket"))
    # When execution_mode=local, agent runs on host and uses this path instead of cloning
    if job.get("execution_mode") == "local" and job.get("project_path"):
        env["AGENT_WORKSPACE"] = str(job["project_path"]).strip()
    # App URL: coordinator uses it to claim jobs; container needs to reach host
    if "TERARCHITECT_API_URL" not in env or not env["TERARCHITECT_API_URL"]:
        env["TERARCHITECT_API_URL"] = _env("TERARCHITECT_API_URL", "")
    # Agent env from backend (Settings/DB) overrides coordinator env for those keys
    for k, v in job.get("agent_env", {}).items():
        if v is not None and str(v).strip():
            env[k] = str(v).strip()
    # Fallback when backend does not send agent_env (e.g. older backend)
    for key in ("TERARCHITECT_WORKER_API_KEY", "GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key) and (key not in env or not env[key]):
            env[key] = os.environ[key]
    if job.get("kind") == "review":
        env["PR_NUMBER"] = str(job.get("pr_number", ""))
        env["COMMENT_BODY"] = str(job.get("comment_body", ""))
        if job.get("github_comment_id") is not None:
            env["GITHUB_COMMENT_ID"] = str(job["github_comment_id"])
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

_RUN_COMMAND_FILE = Path(__file__).resolve().parent / "run_command.txt"


def _write_run_command(job_id: str, mode: str, *, docker_args: Optional[List[str]] = None, local_cmd: Optional[List[str]] = None, local_env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> None:
    """Write the exact command (and env for local) to coordinator/run_command.txt for debugging/repro."""
    lines = [
        f"# Coordinator run command (job_id={job_id}, mode={mode})",
        "# Copy and run in a shell to reproduce.",
        "",
    ]
    if docker_args is not None:
        cmd_str = " ".join(shlex.quote(a) for a in docker_args)
        lines.append(cmd_str)
    elif local_cmd is not None and local_env is not None and cwd is not None:
        lines.append(f"# cwd: {cwd}")
        lines.append("")
        for k in sorted(local_env.keys()):
            v = (local_env.get(k) or "")
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


def _docker_run_args(image: str, job: dict) -> List[str]:
    """Build docker run args (env + image). Cross-platform: container reaches host via host.docker.internal.
    Mac/Windows Docker Desktop provide it; on Linux we add --add-host=host.docker.internal:host-gateway.
    When DOCKER_NETWORK is set (e.g. in compose), add --network so agent containers can reach the app.
    Mounts AGENT_CACHE_VOLUME at /cache so pip and npm reuse packages across runs.

    Docker isolation mode (AGENT_DOCKER_MODE):
      "dind" (default) — each agent container starts its own isolated dockerd (--privileged). No shared
        daemon, so concurrent agents never conflict on container names, networks, or ports.
      "dood" — legacy Docker-out-of-Docker: mounts the host socket. All agents share one daemon;
        set AGENT_MOUNT_DOCKER_SOCKET=0 together with DOCKER_HOST to use an external sidecar instead.
    """
    env = job_to_env(job, for_docker=True)
    for key in _DOCKER_STRIP_ENV:
        env.pop(key, None)
    args = ["docker", "run", "--rm"]
    cache_volume = _env("AGENT_CACHE_VOLUME", "terarchitect-agent-cache")
    if cache_volume:
        args.extend(["-v", f"{cache_volume}:/cache"])
    docker_mode = _env("AGENT_DOCKER_MODE", "dind").lower()
    if docker_mode == "dind":
        # True DinD: privileged so the container can run its own dockerd.
        args.append("--privileged")
    elif _env("AGENT_MOUNT_DOCKER_SOCKET", "1") != "0":
        # Legacy DooD: mount the host Docker socket (shared daemon, potential conflicts).
        args.extend(["-v", "/var/run/docker.sock:/var/run/docker.sock"])
    network = _env("DOCKER_NETWORK")
    if network:
        args.extend(["--network", network])
    api_url = env.get("TERARCHITECT_API_URL") or ""
    if "host.docker.internal" in api_url and platform.system() == "Linux":
        args.extend(["--add-host=host.docker.internal:host-gateway"])
    for k, v in env.items():
        if v is not None and v != "":
            args.extend(["-e", f"{k}={v}"])
    args.append(image)
    return args


def _run_agent_direct(job: dict, docker_error: str, base_url: str, job_id: str = "") -> int:
    """Run the agent on the host (python -m agent.agent_runner). Returns exit code."""
    env = job_to_env(job)
    env["TERARCHITECT_DOCKER_RUN_ERROR"] = docker_error[:8000] if len(docker_error) > 8000 else docker_error
    kind = (job.get("kind") or "ticket").strip().lower()
    sub = "review" if kind == "review" else "ticket"
    repo_root = _repo_root()
    full_env = {**os.environ, **env}
    full_env["PYTHONPATH"] = str(repo_root) + (os.pathsep + full_env["PYTHONPATH"] if full_env.get("PYTHONPATH") else "")
    cmd = [sys.executable, "-m", "agent.agent_runner", sub]
    if job_id:
        _write_run_command(job_id, "local", local_cmd=cmd, local_env=full_env, cwd=str(repo_root))
    try:
        proc = subprocess.run(
            cmd,
            env=full_env,
            cwd=str(repo_root),
            timeout=None,
        )
        return proc.returncode
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
            mark_fail(base_url, job_id)
            print(f"[coordinator] job {job_id} failed (local exit {code})")
        return
    image = project_images.get(project_id) or default_image
    args = _docker_run_args(image, job)
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
        # Docker run failed; mark job failed and print error (no host fallback)
        print(f"[coordinator] job {job_id} docker failed (exit {result.returncode})", flush=True)
        _print_docker_error(combined)
        mark_fail(base_url, job_id)
    except subprocess.TimeoutExpired:
        mark_fail(base_url, job_id)
        print(f"[coordinator] job {job_id} failed (docker timeout)", file=sys.stderr, flush=True)
    except Exception as e:
        err_msg = str(e)
        print(f"[coordinator] job {job_id} docker error: {e}", file=sys.stderr, flush=True)
        _print_docker_error(err_msg)
        mark_fail(base_url, job_id)


def main() -> None:
    project_ids = _project_ids()
    base_url = _base_url()
    default_image = _env("AGENT_IMAGE", "terarchitect-agent")
    # env var is the startup default; the backend setting (set via UI) overrides it each poll cycle.
    env_max_concurrent = max(1, int(_env("MAX_CONCURRENT_AGENTS", "1") or "1"))
    poll_interval = float(_env("POLL_INTERVAL_SEC", "10") or "10")

    running: List[threading.Thread] = []
    docker_mode = _env("AGENT_DOCKER_MODE", "dind").lower()
    scope = f"projects={project_ids}" if project_ids else "all projects"
    # Fetch initial value (may differ from env if already set in the UI)
    max_concurrent = fetch_max_concurrent(base_url, env_max_concurrent)
    print(f"[coordinator] started; scope={scope}, default_image={default_image}, max_concurrent={max_concurrent}, docker_mode={docker_mode}", flush=True)
    print(f"[coordinator] state_dir={_state_dir()}, repo_root={_repo_root()}", flush=True)
    while True:
        # Reap finished threads
        running = [t for t in running if t.is_alive()]

        # Re-read max_concurrent each cycle so UI changes take effect without restart
        new_max = fetch_max_concurrent(base_url, env_max_concurrent)
        if new_max != max_concurrent:
            print(f"[coordinator] max_concurrent changed: {max_concurrent} → {new_max}", flush=True)
            max_concurrent = new_max

        # Claim and start new jobs up to max_concurrent
        while len(running) < max_concurrent:
            job = None
            if project_ids:
                for pid in project_ids:
                    job = claim_job(base_url, pid)
                    if job is not None:
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

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
