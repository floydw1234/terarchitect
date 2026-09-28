"""Run the shipper agent locally from the CLI (coordinator-free dogfood path)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

# Docker-compose service hostnames that do not resolve on the host OS.
_DOCKER_AGENTHUB_HOSTS = frozenset({"agenthub"})
_HOST_AGENTHUB_PORT = 8088

# Env vars forwarded to the shipper subprocess (mirrors coordinator/coordinator.py).
SHIPPER_ENV_KEYS = (
    "TERARCHITECT_API_URL",
    "TERARCHITECT_WORKER_API_KEY",
    "TERARCHITECT_AGENTHUB_URL",
    "AGENTHUB_URL",
    "AGENTHUB_API_KEY",
    "MERGE_TEST_COMMAND",
    "GIT_USER_NAME",
    "GIT_USER_EMAIL",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GITHUB_AGENT_TOKEN",
    "github_agent_token",
)


def _repo_root() -> Path:
    raw = (os.environ.get("TERARCHITECT_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent


def remap_agenthub_url_for_host(url: str) -> tuple[str, Optional[str]]:
    """Remap docker-internal AgentHub URLs for host-side CLI shipper runs.

    Returns ``(url, warning)`` where ``warning`` is set when a remap occurred.
    """
    raw = (url or "").strip()
    if not raw:
        return raw, None

    parsed = urlparse(raw)
    hostname = (parsed.hostname or "").lower()
    if hostname not in _DOCKER_AGENTHUB_HOSTS:
        return raw, None

    port = parsed.port
    if port in (None, 8080):
        new_port = _HOST_AGENTHUB_PORT
    else:
        new_port = port

    netloc = f"127.0.0.1:{new_port}"
    remapped = urlunparse(
        (
            parsed.scheme or "http",
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    warning = f"Warning: remapped AGENTHUB_URL for host CLI: {raw} -> {remapped}"
    return remapped, warning


def _runtime_pythonpath(existing: Optional[str] = None) -> str:
    repo_root = _repo_root()
    parts = [str(repo_root), str(repo_root / "agent")]
    if existing:
        parts.append(existing)
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            ordered.append(part)
    return os.pathsep.join(ordered)


def run_local_shipper(api_url: str, ship_run_id: str, *, capture_stdout: bool = False) -> int:
    """Run ``python -m agent.shipper`` once for a pre-queued ShipRun.

    Coordinator normally claims via ``/api/worker/ship-run/next`` and sets
    ``SHIP_RUN_ID``; the shipper accepts a queued run when fetched by id.
    """
    env: dict[str, str] = {}
    host_agenthub = (os.environ.get("TERARCHITECT_AGENTHUB_URL") or "").strip()
    for key in SHIPPER_ENV_KEYS:
        val = os.environ.get(key)
        if not val:
            continue
        if key in {"AGENTHUB_URL", "TERARCHITECT_AGENTHUB_URL"}:
            continue
        env[key] = val
    ah_raw = host_agenthub or (os.environ.get("AGENTHUB_URL") or "").strip()
    if ah_raw:
        remapped, warning = remap_agenthub_url_for_host(ah_raw)
        env["AGENTHUB_URL"] = remapped
        if warning:
            print(warning, file=sys.stderr)
    env["TERARCHITECT_API_URL"] = api_url.rstrip("/")
    env["SHIP_RUN_ID"] = str(ship_run_id)

    repo_root = _repo_root()
    full_env = {**os.environ, **env}
    full_env["PYTHONPATH"] = _runtime_pythonpath(full_env.get("PYTHONPATH"))

    result = subprocess.run(
        [sys.executable, "-m", "agent.shipper"],
        env=full_env,
        cwd=str(repo_root),
        stdout=subprocess.PIPE if capture_stdout else None,
        stderr=None,
    )
    if capture_stdout and result.stdout:
        print(result.stdout.decode(errors="replace"), file=sys.stderr)
    return int(result.returncode)
