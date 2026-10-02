#!/usr/bin/env bash
# Same Python unit/API checks as the python-smoke job in .github/workflows/ci.yml.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

export PYTHONPATH="${PYTHONPATH:-backend:agent}"
# Avoid broken third-party pytest plugins from the host (e.g. ROS launch_testing).
export PYTEST_DISABLE_PLUGIN_AUTOLOAD="${PYTEST_DISABLE_PLUGIN_AUTOLOAD:-1}"

if [[ -n "${PYTEST:-}" ]]; then
  RUNNER=( "${PYTEST}" )
elif [[ -x "${ROOT}/.venv/bin/python" ]]; then
  RUNNER=( "${ROOT}/.venv/bin/python" -m pytest )
else
  RUNNER=( python -m pytest )
fi

# All in-process unit/API tests. Skip compose-backed integration suites and
# live HippoRAG/embedding tests (need docker/postgres/LLMs).
exec "${RUNNER[@]}" -q \
  tests \
  backend/tests \
  agent/tests \
  coordinator/tests \
  --ignore=tests/integration \
  --ignore=backend/tests/test_memory_hipporag.py
