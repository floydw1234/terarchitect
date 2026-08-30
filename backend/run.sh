#!/bin/bash
# Run backend on host - requires postgres + frontend via docker, vLLM, Claude Code CLI.
# Always use Terarchitect's repo-local venv so backend deps never land in Hermes' venv.
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${BACKEND_DIR}/.." && pwd)"
PYTHON="${REPO_ROOT}/.venv/bin/python"

if [ ! -x "${PYTHON}" ]; then
  "${REPO_ROOT}/scripts/bootstrap-python-env.sh"
fi

cd "${BACKEND_DIR}"
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/backend:${REPO_ROOT}/agent"
export DATABASE_URL="${DATABASE_URL:-postgresql://terarchitect:***@localhost:5432/terarchitect}"
exec "${PYTHON}" -m flask run --host=0.0.0.0 --port=5010
