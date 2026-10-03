"""Ensure agent modules import cleanly with flat PYTHONPATH (terarchitect-agent image layout)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_AGENT_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module",
    [
        "agent_runner.__main__",
        "middle_agent.agent",
        "utils.worker_api_key",
        "shipper.shipper",
        "workspace_composer.composer",
    ],
)
def test_agent_module_imports_with_flat_pythonpath(module: str) -> None:
    env = {**os.environ, "PYTHONPATH": str(_AGENT_DIR)}
    proc = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(_AGENT_DIR),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
