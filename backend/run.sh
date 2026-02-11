#!/bin/bash
# Run backend on host - requires postgres + frontend via docker, vLLM, Claude Code CLI
set -e
cd "$(dirname "$0")"
export PYTHONPATH="${PYTHONPATH:-$PWD}"
export DATABASE_URL="${DATABASE_URL:-postgresql://terarchitect:terarchitect@localhost:5432/terarchitect}"
exec flask run --host=0.0.0.0 --port=5010
