#!/usr/bin/env bash
# Phase 3/4: Container entrypoint. Run one ticket job; exit 0 = success, non-zero = failure.
# Coordinator passes: JOB_KIND, TICKET_ID, PROJECT_ID, TERARCHITECT_API_URL, REPO_URL.

set -e

load_secret_file() {
  _target_var="$1"
  _path_var="${_target_var}_PATH"
  eval "_current_value=\${$_target_var:-}"
  eval "_secret_path=\${$_path_var:-}"
  if [ -n "$_current_value" ] || [ -z "$_secret_path" ]; then
    return 0
  fi
  if [ ! -f "$_secret_path" ]; then
    echo "[entrypoint] ${_path_var} points to a missing file: ${_secret_path}" >&2
    exit 1
  fi
  export "$_target_var=$(tr -d '\r\n' < "$_secret_path")"
}

load_secret_file AGENTHUB_API_KEY
load_secret_file DIRECTOR_API_KEY
load_secret_file OPENROUTER_API_KEY
load_secret_file WORKER_API_KEY
load_secret_file OPENAI_API_KEY
load_secret_file TERARCHITECT_WORKER_API_KEY

# AGENT_WORKSPACE only set by coordinator for local execution. In Docker we leave it unset so the runner clones the repo.
if [ -n "$AGENT_WORKSPACE" ]; then
  mkdir -p "$AGENT_WORKSPACE"
fi

# --- Docker-in-Docker ---
# Start an isolated Docker daemon inside this container so each agent job has its own Docker
# namespace. Requires the outer container to be started with --privileged (set by the coordinator
# when AGENT_DOCKER_MODE=dind, the default). Skip if DOCKER_HOST already points to an external
# daemon (e.g. a docker:dind sidecar or the legacy DooD socket mount).
if [ -z "$DOCKER_HOST" ]; then
  mkdir -p /var/lib/docker
  dockerd \
    --host=unix:///var/run/docker.sock \
    --log-level=warn \
    > /tmp/dockerd.log 2>&1 &
  _DOCKERD_PID=$!
  echo "[entrypoint] started dockerd (pid=${_DOCKERD_PID})"
  _i=0
  while [ $_i -lt 30 ]; do
    if docker info >/dev/null 2>&1; then break; fi
    sleep 1
    _i=$((_i + 1))
  done
  if [ $_i -eq 30 ]; then
    echo "[entrypoint] dockerd did not become ready in 30s" >&2
    cat /tmp/dockerd.log >&2
    exit 1
  fi
  echo "[entrypoint] dockerd ready"
fi

# --- OpenCode server ---
# Only start OpenCode when it is actually needed.
_worker_mode="${WORKER_MODE:-opencode}"
if [ "$_worker_mode" = "opencode" ]; then
  _oc_port="${OPENCODE_SERVER_PORT:-4096}"
  # Inject provider config from agent settings (WORKER_LLM_URL, WORKER_MODEL, WORKER_API_KEY) so OpenCode server has the LLM at startup.
  if [ -n "${WORKER_LLM_URL}" ] || [ -n "${WORKER_MODEL}" ]; then
    _oc_config="$(python /app/agent_runner/build_opencode_config.py 2>/dev/null)"
    if [ -n "$_oc_config" ]; then
      export OPENCODE_CONFIG_CONTENT="$_oc_config"
    fi
  fi
  # Enable OpenCode's built-in websearch tool (Exa-backed) for the worker.
  # No API key required; OPENCODE_ENABLE_EXA activates the websearch tool.
  export OPENCODE_ENABLE_EXA="${OPENCODE_ENABLE_EXA:-1}"
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
fi

exec python -m agent_runner ticket
