Project: Architask AI (Working Title)
Concept: A visual-first, autonomous SDLC (Software Development Lifecycle) orchestrator that uses a "Director-Worker" agent model to build complex systems locally.

## Quick Start

```bash
# Postgres + frontend (Docker)
docker compose up -d

# Backend on host (for OpenCode + local project access)
cd backend && pip install -r requirements.txt && flask run --host=0.0.0.0 --port=5010
```

- App: http://localhost:3000
- API: http://localhost:5010

1. The Core Innovation
Unlike current AI coding tools that rely on a single chat window, this system separates High-Level Design (The Graph) from Local Execution (The Worker). This prevents "context poisoning" and keeps the AI aligned with the overall system architecture.

2. The Three-Layer Workflow
The Architect’s View (Graph Interface): * A visual canvas (nodes and edges) representing the system architecture (e.g., FastAPI Server → Redis Cache → Postgres DB).

Each node contains "contracts": technologies, port schemas, and security rules.

The Command Center (Kanban Board):

Users (or an AI assistant) generate tickets based on the graph.

Moving a ticket to "In Progress" triggers the Architect Agent.

The Autonomous Loop (Agent-on-Agent):

Architect Agent: Reads the graph + ticket $\rightarrow$ Creates a strict Implementation Plan.

Worker Agent (OpenCode): Consumes the plan $\rightarrow$ Executes code via local vLLM $\rightarrow$ Commits to Git $\rightarrow$ Opens a PR.

3. Key Technical Pillars
Context Separation: The Architect Agent holds the big picture; the Worker Agent only sees the relevant files and the specific plan.

Privacy First: Optimized for local inference (vLLM/Ollama). No proprietary code or architecture schemas leave the local machine.

Human-in-the-Loop: The Kanban board acts as the handoff point where a human reviews the AI’s PR before it moves to "Done."

4. Why This Wins (The "Noise" Factor)
System Awareness: It’s the first tool where the agent knows why it’s writing a specific function (because the Graph said so).

Senior Dev Workflow: It mirrors how actual Lead Engineers work—designing the system and delegating the implementation.

Open Source "Glue": It leverages existing power-tools (Aider, OpenCode, React Flow) into a cohesive, professional-grade suite.



pkill -f "python main.py"

