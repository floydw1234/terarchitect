# Masterplan: Terarchitect (Architask AI)

## Vision
A visual-first, autonomous SDLC orchestrator using a "Director-Worker" agent model to build complex systems locally.

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                          Terarchitect                                 │
├───────────────────────────────────────────────────────────────────────┤
│  Frontend (React) → Backend (Flask) → Middle Agent (vLLM)             │
│                                    ↓                                  │
│                             PostgreSQL                                │
│                                    ↓                                  │
│                           Claude Code CLI                             │
└───────────────────────────────────────────────────────────────────────┘
```

## Tech Stack
- **Frontend**: React (simple, no TS required)
- **Backend**: Flask
- **Database**: PostgreSQL
- **LLM**: vLLM running Qwen-Coder-Next-FP8 (80b) on localhost:8000
- **Web Search**: Proxy on localhost:8080
- **Agent**: Qwen-Coder-30B (same vLLM instance, concurrent requests)
- **Execution**: Claude Code via CLI

## Key Components

### 1. Visual Graph Editor (Frontend)
- React + React Flow
- Stores architecture diagrams as JSON
- Each node contains: technologies, port schemas, security rules
- Edges represent connections between services

### 2. Kanban Board (Frontend)
- Users/AI generate tickets based on graph
- Ticket states: Backlog → In Progress → Done
- "In Progress" triggers Architect Agent → Middle Agent

### 3. Middle Agent (vLLM)
**Responsibilities:**
- Poll DB for "In Progress" tickets
- Load relevant graph context (filtered to ticket)
- Spawn Claude Code CLI session per ticket
- Send context + task to Claude Code
- Read output, assess completion (model decides)
- If incomplete → send follow-up prompts
- If complete → commit → push → open GH PR → move ticket

**Claude Code CLI Commands:**
```bash
# Start persistent session
claude --session-id "<ticket-id>" --dangerously-skip-permissions

# Send prompt
claude -p --session-id "<ticket-id>" --output-format json "<prompt>"

# Full flags used:
# --print (-p), --session-id, --continue (-c)
# --dangerously-skip-permissions, --output-format json
```

### 4. Backend (Flask)
- REST API for frontend
- Graph storage/retrieval (PostgreSQL)
- Ticket management
- Triggers Middle Agent for "In Progress" tickets

## Data Flow

1. User creates graph diagram → saved to DB
2. User creates ticket linked to graph → moves to "In Progress"
3. Backend detects "In Progress" → notifies Middle Agent
4. Middle Agent (vLLM):
   - Reads graph context
   - Spawns Claude Code session
   - Sends prompt + context
   - Reads output → decides if complete
   - Loops until done
   - Commits, pushes, opens PR
5. User reviews PR → merges

## vLLM Setup
- Qwen-Coder-Next-FP8 (80b) on `localhost:8000`
- Web search proxy on `localhost:8080`
- Handles concurrent requests for Agent + Claude Code

## Deployment
- Self-hosted first
- Docker Compose: postgres, flask+node (react)

## Current State
- README exists with vision
- Training data collected (230 QA pairs from Cursor chats)
- Full implementation in progress

## Project Structure
```
terarchitect/
├── backend/                # Flask backend
│   ├── api/               # API routes
│   │   ├── __init__.py
│   │   └── routes.py
│   ├── models/            # Database models
│   │   ├── __init__.py
│   │   └── db.py
│   ├── middle_agent/      # Agent orchestration
│   │   ├── __init__.py
│   │   └── agent.py
│   ├── app.py
│   └── requirements.txt
├── frontend/              # React frontend
│   ├── public/
│   ├── src/
│   │   ├── components/
│   │   │   └── Navbar.tsx
│   │   ├── pages/
│   │   │   ├── ProjectsPage.tsx
│   │   │   ├── ProjectPage.tsx
│   │   │   ├── GraphEditorPage.tsx
│   │   │   └── KanbanPage.tsx
│   │   ├── App.tsx
│   │   └── index.tsx
│   └── package.json
├── migrations/            # PostgreSQL migrations
│   └── 001_create_schema.sql
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
└── MASTERPLAN.md
```

## Database Schema

### Tables
| Table | Description |
|-------|-------------|
| `projects` | Main project container |
| `graphs` | Visual architecture diagrams (nodes/edges JSONB) |
| `kanban_boards` | Kanban state (columns JSONB) |
| `tickets` | Kanban cards with associated nodes/edges |
| `ticket_comments` | Agent communication (one-way) |
| `notes` | Free-form notes linked to nodes/edges |
| `execution_logs` | Middle Agent activity tracking |
| `prs` | GitHub PR tracking |
| `settings` | Project configuration |
| `rag_embeddings` | Vector embeddings for RAG search |

## API Endpoints

### Projects
- `GET /api/projects` - List all projects
- `POST /api/projects` - Create new project
- `GET /api/projects/:id` - Get project details
- `PUT /api/projects/:id` - Update project
- `DELETE /api/projects/:id` - Delete project

### Graph
- `GET /api/projects/:id/graph` - Get project graph
- `PUT /api/projects/:id/graph` - Update graph

### Kanban
- `GET /api/projects/:id/kanban` - Get kanban board
- `PUT /api/projects/:id/kanban` - Update kanban board

### Tickets
- `GET /api/projects/:id/tickets` - List tickets
- `POST /api/projects/:id/tickets` - Create ticket

### Notes
- `GET /api/projects/:id/notes` - List notes
- `POST /api/projects/:id/notes` - Create note

### RAG
- `POST /api/rag/search` - Vector similarity search

## Setup & Development

### Prerequisites
- Docker and Docker Compose
- PostgreSQL with pgvector extension
- vLLM running Qwen-Coder-Next-FP8 on localhost:8000
- Claude Code CLI installed

### Docker Deployment
```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Environment Variables
```env
DATABASE_URL=postgresql://terarchitect:terarchitect@postgres:5432/terarchitect
VLLM_URL=http://host.docker.internal:8000
VLLM_PROXY_URL=http://host.docker.internal:8080
FLASK_ENV=development
```

### Development (Local)
```bash
# Start PostgreSQL (with pgvector)
docker run -p 5432:5432 -e POSTGRES_PASSWORD=terarchitect pgvector/pgvector:pg16

# Apply migrations
psql -U terarchitect -d terarchitect -f migrations/001_create_schema.sql

# Start backend
cd backend
pip install -r requirements.txt
flask run

# Start frontend
cd frontend
npm install
npm start
```

## Middle Agent Flow
```
1. Poll DB for "In Progress" tickets
2. Load context: project info, graph, notes
3. Spawn Claude Code session
4. Send initial prompt with context
5. Loop:
   - Read Claude Code output
   - Model decides if complete
   - If not: generate next prompt
   - If yes: finalize
6. Commit → Push → Create PR → Move to Done
```
