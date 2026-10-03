"""Guard backend image layout: local shipper (cli + agent) must be packaged."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile.backend"
BACKEND_ROOT = REPO_ROOT / "backend"


def _dockerfile_pythonpath() -> list[str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    match = re.search(r"ENV PYTHONPATH=([^\n]+)", text)
    assert match, "Dockerfile.backend must set PYTHONPATH"
    return [p.strip() for p in match.group(1).split(os.pathsep) if p.strip()]


def test_dockerfile_backend_copies_cli_and_agent_for_shipper():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY cli/" in text
    assert "COPY agent/" in text
    assert "TERARCHITECT_REPO_ROOT" in text
    assert "agent/requirements.txt" not in text


def test_dockerfile_pythonpath_puts_app_first_without_agent_tree():
    parts = _dockerfile_pythonpath()
    assert parts[0] == "/app"
    assert "/opt/terarchitect" in parts
    assert "/opt/terarchitect/agent" not in parts
    app_index = parts.index("/app")
    terarchitect_index = parts.index("/opt/terarchitect")
    assert app_index < terarchitect_index


def test_backend_modules_win_over_agent_with_container_pythonpath():
    """Imports must resolve to backend/, not agent/utils, under image PYTHONPATH."""
    terarchitect_root = REPO_ROOT
    pythonpath = _dockerfile_pythonpath()
    # Map container paths to this checkout (backend + cli/agent under repo root).
    host_paths = []
    for entry in pythonpath:
        if entry == "/app":
            host_paths.append(str(BACKEND_ROOT))
        elif entry == "/opt/terarchitect":
            host_paths.append(str(terarchitect_root))
        else:
            host_paths.append(entry)

    prev_path = list(sys.path)
    prev_repo = os.environ.get("TERARCHITECT_REPO_ROOT")
    prev_pp = os.environ.get("PYTHONPATH")
    try:
        sys.path[:] = [
            *host_paths,
            *[p for p in prev_path if p not in set(host_paths)],
        ]
        os.environ["TERARCHITECT_REPO_ROOT"] = str(terarchitect_root)
        os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath)

        import importlib

        utils = importlib.import_module("utils")
        assert Path(utils.__file__).resolve().is_relative_to(BACKEND_ROOT.resolve())

        import cli._shipper  # noqa: F401

        import agent.shipper.shipper  # noqa: F401
    finally:
        sys.path[:] = prev_path
        if prev_repo is None:
            os.environ.pop("TERARCHITECT_REPO_ROOT", None)
        else:
            os.environ["TERARCHITECT_REPO_ROOT"] = prev_repo
        if prev_pp is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = prev_pp


def test_sqlalchemy_pin_keeps_psycopg2_driver_for_postgresql_url():
    """Guard against unpinned SQLAlchemy 2.1+ selecting the psycopg v3 driver."""
    req = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"sqlalchemy\s*>=\s*2\.0\.16\s*,\s*<\s*2\.1", req)
