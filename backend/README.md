# Terarchitect Backend

Flask backend for the Terarchitect visual SDLC orchestrator.

**Runs on host** (not Docker) for access to Claude Code CLI and local project paths.

## Setup

```bash
cd backend
pip install -r requirements.txt
```

## Run

```bash
# Start postgres + frontend (Docker)
docker compose up -d

# Run backend on host
cd backend
flask run --host=0.0.0.0 --port=5010
# Or: ./run.sh
```

## Environment Variables (.env in backend/)

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL (default: `postgresql://terarchitect:terarchitect@localhost:5432/terarchitect`) |
| `VLLM_URL` | vLLM server (default: http://localhost:8000) |
| `AGENT_API_URL` | OpenAI-compatible API for agent (default: `{VLLM_URL}/v1/chat/completions`) |
| `AGENT_MODEL` | Model name for agent API |
| `AGENT_API_KEY` | API key (optional for vLLM) |
| `MIDDLE_AGENT_DEBUG` | Set to `1` to log agent activity |

## Middle Agent

Runs when a ticket is moved to "In Progress". Requires:
- vLLM (or OpenAI-compatible API) for agent decisions
- Claude Code CLI on PATH
- Project path set to a local directory the backend can access
