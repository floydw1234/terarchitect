"""Run the shipper agent locally from the CLI (coordinator-free dogfood path)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Env vars forwarded to the shipper subprocess (mirrors coordinator/coordinator.py).
SHIPPER_ENV_KEYS = (
    "TERARCHITECT_API_URL",
    "TERARCHITECT_WORKER_API_KEY",
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


def run_local_shipper(api_url: str, ship_run_id: str) -> int:
    """Run ``python -m agent.shipper`` once for a pre-queued ShipRun.

    Coordinator normally claims via ``/api/worker/ship-run/next`` and sets
    ``SHIP_RUN_ID``; the shipper accepts a queued run when fetched by id.
    """
    env: dict[str, str] = {}
    for key in SHIPPER_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            env[key] = val
    env["TERARCHITECT_API_URL"] = api_url.rstrip("/")
    env["SHIP_RUN_ID"] = str(ship_run_id)

    repo_root = _repo_root()
    full_env = {**os.environ, **env}
    full_env["PYTHONPATH"] = _runtime_pythonpath(full_env.get("PYTHONPATH"))

    result = subprocess.run(
        [sys.executable, "-m", "agent.shipper"],
        env=full_env,
        cwd=str(repo_root),
    )
    return int(result.returncode)
