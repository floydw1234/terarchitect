# Terarchitect Backend

Flask backend for the Terarchitect visual SDLC orchestrator.

## Setup

```bash
cd backend
pip install -r requirements.txt
flask run
```

## Environment Variables
- `DATABASE_URL` - PostgreSQL connection string
- `VLLM_URL` - vLLM server URL (default: http://localhost:8000)
- `VLLM_PROXY_URL` - vLLM web search proxy URL (default: http://localhost:8080)
- `FLASK_ENV` - Environment (development/production)

## Database Migrations

```bash
# Apply migrations
psql -U terarchitect -d terarchitect -f ../migrations/001_create_schema.sql
```

## Middle Agent

The middle agent is in `middle_agent/agent.py`. It:
- Polls for "In Progress" tickets
- Loads graph context
- Spawns Claude Code sessions
- Orchestrates implementation
- Creates PRs when complete
