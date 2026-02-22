#!/usr/bin/env bash
# Phase 3/4: Container entrypoint. Run one job (ticket or review); exit 0 = success, non-zero = failure.
# Coordinator passes: JOB_KIND, TICKET_ID, PROJECT_ID, TERARCHITECT_API_URL, REPO_URL; for review: PR_NUMBER, COMMENT_BODY.
# Start OpenCode HTTP server in background; agent uses its API (session/message/summarize every 30 turns).
# OpenCode loads providers at startup; set OPENCODE_CONFIG_CONTENT from WORKER_* so terarchitect-proxy provider exists.

set -e
# AGENT_WORKSPACE only set by coordinator for local execution. In Docker we leave it unset so the runner clones the repo.
if [ -n "$AGENT_WORKSPACE" ]; then
  mkdir -p "$AGENT_WORKSPACE"
fi

_oc_port="${OPENCODE_SERVER_PORT:-4096}"
# Inject provider config from agent settings (WORKER_LLM_URL, WORKER_MODEL, WORKER_API_KEY) so OpenCode server has the LLM at startup.
if [ -n "${WORKER_LLM_URL}" ] || [ -n "${WORKER_MODEL}" ]; then
  _oc_config="$(python /app/agent_runner/build_opencode_config.py 2>/dev/null)"
  if [ -n "$_oc_config" ]; then
    export OPENCODE_CONFIG_CONTENT="$_oc_config"
  fi
fi
opencode serve --port "$_oc_port" --hostname 127.0.0.1 &
_i=0
while [ $_i -lt 30 ]; do
  if curl -sf "http://127.0.0.1:${_oc_port}/global/health" >/dev/null 2>&1; then break; fi
  sleep 1
  _i=$((_i + 1))
done
if [ $_i -eq 30 ]; then
  echo "[entrypoint] opencode serve did not become ready in time" >&2
  exit 1
fi
export OPENCODE_SERVER_URL="http://127.0.0.1:${_oc_port}"

case "${JOB_KIND}" in
  review) exec python -m agent_runner review ;;
  *)      exec python -m agent_runner ticket ;;
esac
