"""Guard backend image layout: local shipper (cli + agent) must be packaged."""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile.backend"


def test_dockerfile_backend_copies_cli_and_agent_for_shipper():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY cli/" in text
    assert "COPY agent/" in text
    assert "TERARCHITECT_REPO_ROOT" in text
    assert "PYTHONPATH" in text
    assert "agent/requirements.txt" in text


def test_cli_shipper_import_with_backend_container_pythonpath():
    """Simulate /app + /opt/terarchitect layout from Dockerfile.backend."""
    terarchitect_root = REPO_ROOT
    backend_root = REPO_ROOT / "backend"
    prev_path = list(sys.path)
    prev_repo = os.environ.get("TERARCHITECT_REPO_ROOT")
    try:
        sys.path[:] = [
            str(terarchitect_root),
            str(terarchitect_root / "agent"),
            str(backend_root),
            *[p for p in prev_path if p not in {str(terarchitect_root), str(terarchitect_root / "agent"), str(backend_root)}],
        ]
        os.environ["TERARCHITECT_REPO_ROOT"] = str(terarchitect_root)
        import cli._shipper  # noqa: F401
        import agent.shipper.shipper  # noqa: F401
    finally:
        sys.path[:] = prev_path
        if prev_repo is None:
            os.environ.pop("TERARCHITECT_REPO_ROOT", None)
        else:
            os.environ["TERARCHITECT_REPO_ROOT"] = prev_repo
