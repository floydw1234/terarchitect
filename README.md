Project: Architask AI (Working Title)
Concept: A visual-first, autonomous SDLC orchestrator that uses a "Director-Worker" agent model to build complex systems locally.

## Quick Start

```bash
# Postgres + backend + frontend (all via Docker)
docker compose up -d
```

Or run the backend on the host: `cd backend && pip install -r requirements.txt && flask run --host=0.0.0.0 --port=5010` (use `DATABASE_URL=postgresql://terarchitect:terarchitect@localhost:5433/terarchitect` for compose Postgres).

- App: http://localhost:3000
- API: http://localhost:5010

Execution (tickets → agent): see **Flow** below and [docs/RUNBOOK.md](docs/RUNBOOK.md).

---

## Flow (no Dockerfile mixing)

1. **Build the agent image once** (from this repo). There is a single image; nothing is combined with a “project Dockerfile.”
   ```bash
   docker build -f Dockerfile.agent -t terarchitect-agent .
   ```

2. **User enqueues a ticket**  
   In the app, create a project (with its GitHub repo URL), add tickets, and move one to **In Progress**. The backend enqueues a job (project_id, ticket_id, repo_url = that project’s GitHub URL).

3. **Coordinator runs the agent image**  
   The coordinator is a host process (not a container). It claims jobs from the API and, for each job, runs **the same** image you built:
   ```text
   docker run ... -e REPO_URL=... -e TICKET_ID=... terarchitect-agent
   ```
   It does **not** pull or build from the project repo; it only runs the pre-built `terarchitect-agent` image.

4. **Inside the container**  
   The agent runner clones **the project repo** (REPO_URL — the codebase you’re building, e.g. your app), creates branch `ticket-{id}` from the default branch (clone is depth 1, so it’s the latest at clone time), then the Director + OpenCode implement the ticket in that clone and open a PR.

So: **one image** (Terarchitect’s Dockerfile.agent), **one container per job**, **clone the project repo inside the container** — no combining of Dockerfiles.

---

## Repo layout

| Path | Role |
|------|------|
| `backend/` | Flask API + DB. Enqueues jobs only; no in-process agent. |
| `frontend/` | React UI (graph, Kanban). |
| `coordinator/` | Host-side Python app. Claims jobs, runs `docker run ... terarchitect-agent`. Run from repo root: `PYTHONPATH=. python -m coordinator` (or install as systemd service; see RUNBOOK). |
| `agent/` | Director + runner + worker wiring. Packaged into the `terarchitect-agent` image; not run directly from repo except for dev. |

---

## Deployment

- **App:** API + DB + frontend only. See `docs/RUNBOOK.md`.
- **Coordinator:** Run on a host with Docker; it starts agent containers. Same image every time; no per-project image build.
- **Agent image:** Build once with `docker build -f Dockerfile.agent -t terarchitect-agent .`

---

## The Core Innovation

Unlike single-chat tools, this system separates **high-level design (the graph)** from **local execution (the worker)**. The Director Agent reads the graph and ticket and directs the Worker (OpenCode, Aider, etc.) to implement in the project repo. The Kanban board is the handoff: move a ticket to In Progress to enqueue work; review the AI’s PR before moving to Done.

Technical pillars: context separation (Director has the big picture; worker sees plan + files), local inference (vLLM/Ollama), human-in-the-loop (review PRs).
