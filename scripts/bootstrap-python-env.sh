#!/usr/bin/env bash
# Bootstrap Terarchitect's repo-local Python environment.
# Do not run bare `pip install ...` for this repo from Hermes or any other shared venv.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv"
PYTHON_BIN="${PYTHON:-python3}"

cd "${ROOT}"

if [ ! -x "${VENV}/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv "${VENV}"
  else
    "${PYTHON_BIN}" -m venv "${VENV}"
  fi
fi

"${VENV}/bin/python" -m pip install --upgrade pip
"${VENV}/bin/python" -m pip install \
  -r backend/requirements.txt \
  -r agent/requirements.txt \
  -r coordinator/requirements.txt

"${VENV}/bin/python" - <<'PY'
import importlib.metadata as md
import sys

print(f"Terarchitect Python: {sys.executable}")
print(f"OpenAI SDK: {md.version('openai')}")
PY
