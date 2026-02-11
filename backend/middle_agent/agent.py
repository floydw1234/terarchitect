"""
Middle Agent for Terarchitect
"""
import os
import sys
import json
import subprocess
import uuid
from datetime import datetime
import requests
from typing import Optional, Tuple, Dict, Any

from models.db import db, Ticket, ExecutionLog

# Track active agent sessions so they can be cancelled.
_active_sessions: Dict[uuid.UUID, Dict[str, Any]] = {}

# System prompt for the Middle Agent (vLLM): orchestrator role, how to assess and craft prompts
AGENT_SYSTEM_PROMPT = """You are the Middle Agent for Terarchitect, a visual SDLC orchestrator.

Your role (Director):
- You orchestrate Claude Code (the Worker) to implement tickets based on the architecture graph.
- You load context (project, graph, notes), send it to Claude Code, and assess Claude's responses.
- The graph defines the system: nodes = services/components (technologies, ports, security); edges = connections.
- You decide when the task is complete and what to tell Claude next.

When Claude is NOT complete:
- Craft a clear, focused prompt for the next turn. Include exactly what Claude should do next.
- Always instruct Claude to work VERY SLOWLY: one file at a time, verify each change before proceeding.
- Do not rush. Quality over speed.
- If Claude seems stuck or confused, ask a clarifying question or suggest a smaller step.

When Claude IS complete:
- Respond with JSON: {"complete": true, "summary": "brief description"}
- Only mark complete when the ticket's goal is actually achieved (code works, tests pass, etc.).

Respond in JSON. If complete: {"complete": true, "summary": "..."}. If not: {"complete": false, "next_prompt": "the exact prompt to send to Claude next"}."""


class AgentAPIError(Exception):
    """Raised when the agent's LLM API is unavailable or returns invalid data."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class MiddleAgent:
    """Agent that orchestrates Claude Code for implementation tasks."""

    def __init__(self):
        self.vllm_proxy_url = os.environ.get("VLLM_PROXY_URL", "http://localhost:8080")
        cmd = os.environ.get("CLAUDE_CODE_CMD", "claude")
        self.claude_code_cmd = cmd.split() if isinstance(cmd, str) else [cmd]
        self.debug = os.environ.get("MIDDLE_AGENT_DEBUG", "").lower() in ("1", "true", "yes")

        # OpenAI-compatible API for agent decisions (vLLM, OpenAI, or any compatible endpoint)
        vllm_base = os.environ.get("VLLM_URL", "http://localhost:8000").rstrip("/")
        agent_url = os.environ.get("AGENT_API_URL", "").strip()
        self.agent_api_url = agent_url or f"{vllm_base}/v1/chat/completions"
        self.agent_model = os.environ.get("AGENT_MODEL", "default")
        self.agent_api_key = os.environ.get("AGENT_API_KEY", "").strip() or None

    def _debug_log(self, msg: str) -> None:
        if self.debug:
            print(f"[MIDDLE_AGENT] {msg}", file=sys.stderr, flush=True)

    def _trace_log(self, session_id: str, message: str, project_path: Optional[str] = None) -> None:
        """Write detailed per-session trace logs to a file when debug is enabled."""
        if not self.debug:
            return
        try:
            if project_path and os.path.isdir(project_path):
                base_dir = os.path.join(project_path, ".terarchitect")
            else:
                base_dir = os.path.join(os.getcwd(), "middle_agent_logs")
            os.makedirs(base_dir, exist_ok=True)
            path = os.path.join(base_dir, f"middle_agent_{session_id}.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {datetime.utcnow().isoformat()}Z ===\n")
                f.write(message)
                f.write("\n")
        except Exception:
            # Don't let trace logging failures break the agent
            self._debug_log(f"Failed to write trace log for session {session_id}")

    def process_ticket(self, ticket_id: uuid.UUID) -> None:
        """Process a ticket from start to finish."""
        self._debug_log(f"process_ticket: {ticket_id}")
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            self._debug_log("Ticket not found, exiting")
            return

        session_id = str(uuid.uuid4())
        # Register active session so it can be cancelled externally.
        _active_sessions[ticket.id] = {
            "cancel": False,
            "session_id": session_id,
        }
        self._log(ticket.project_id, ticket_id, session_id, "session_started", f"Started Claude Code session {session_id}")
        self._debug_log("Session started, loading context...")

        try:
            # Step 1: Load context
            context = self._load_context(ticket)
            self._log(ticket.project_id, ticket_id, session_id, "context_loaded", "Loaded project context and graph")

            # Resolve and validate project_path. Hard fail if it's missing or invalid.
            project_path = (context.get("project_path") or "").strip() or None
            if not project_path or not os.path.isdir(project_path):
                msg = f"Invalid project_path for ticket: {project_path!r}. Please set a valid directory in the project settings."
                self._debug_log(msg)
                self._log(
                    ticket.project_id,
                    ticket_id,
                    session_id,
                    "invalid_project_path",
                    msg,
                )
                return

            # If user cancelled before we even start, bail out early.
            if _active_sessions.get(ticket.id, {}).get("cancel"):
                self._log(ticket.project_id, ticket_id, session_id, "cancelled", "Execution cancelled before first Claude turn")
                return

            # Step 2: Build initial prompt and send
            task_instruction = "Implement the following ticket.\n\nContext:\n" + json.dumps(context, indent=2)
            self._trace_log(
                session_id,
                f"Prompt to Claude Code (turn 0):\n{task_instruction}",
                project_path,
            )
            response = self._send_to_claude_code(
                task_instruction,
                session_id,
                project_path,
                resume=False,
            )
            self._trace_log(
                session_id,
                "Claude Code response (turn 0):\n"
                f"return_code={response.get('return_code')}\n"
                f"stdout:\n{response.get('output')}\n"
                f"stderr:\n{response.get('error')}",
                project_path,
            )
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                "claude_turn_0",
                "Initial prompt sent",
                raw_output=response.get("output"),
            )
            self._debug_log(f"Turn 0 output: {response.get('output', '')[:500]}...")

            # Step 3: Loop until task is complete
            conversation_history: list[str] = [response.get("output") or ""]
            max_turns = 50
            for turn in range(max_turns):
                # Check for user cancellation between turns.
                if _active_sessions.get(ticket.id, {}).get("cancel"):
                    self._log(
                        ticket.project_id,
                        ticket_id,
                        session_id,
                        "cancelled",
                        f"Execution cancelled by user during turn {turn}",
                    )
                    return

                agent_response = self._agent_assess(
                    context,
                    conversation_history,
                    session_id=session_id,
                    project_path=project_path,
                )
                if agent_response.get("complete"):
                    self._log(
                        ticket.project_id,
                        ticket_id,
                        session_id,
                        "task_complete",
                        agent_response.get("summary", "Task completed"),
                    )
                    break

                next_prompt = agent_response.get("next_prompt")
                if not next_prompt:
                    raise AgentAPIError("Agent API returned no next_prompt when task is incomplete")
                # Agent instructs Claude to work slowly - append for implementation prompts, not meta-assessment
                if "assess: is the ticket complete" not in next_prompt.lower():
                    if "one file at a time" not in next_prompt.lower() and "slowly" not in next_prompt.lower():
                        next_prompt = "Work VERY slowly: modify one file at a time, verify each change before proceeding.\n\n" + next_prompt

                self._trace_log(
                    session_id,
                    f"Prompt to Claude Code (turn {turn + 1}):\n{next_prompt}",
                    project_path,
                )
                response = self._send_to_claude_code(next_prompt, session_id, project_path, resume=True)
                conversation_history.append(response.get("output") or "")
                self._trace_log(
                    session_id,
                    f"Claude Code response (turn {turn + 1}):\n"
                    f"return_code={response.get('return_code')}\n"
                    f"stdout:\n{response.get('output')}\n"
                    f"stderr:\n{response.get('error')}",
                    project_path,
                )
                self._log(
                    ticket.project_id,
                    ticket_id,
                    session_id,
                    f"claude_turn_{turn + 1}",
                    f"Turn {turn + 1} completed",
                    raw_output=response.get("output"),
                )
                self._debug_log(f"Turn {turn + 1} output: {response.get('output', '')[:500]}...")

            # Step 4: Finalize
            self._finalize(ticket, session_id)
        finally:
            _active_sessions.pop(ticket.id, None)

    def _load_context(self, ticket: Ticket) -> dict:
        """Load relevant context for the ticket."""
        from models.db import Project, Graph, Note

        project = Project.query.get(ticket.project_id)

        context = {
            "project_name": project.name,
            "project_description": project.description,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "ticket": {
                "id": str(ticket.id),
                "title": ticket.title,
                "description": ticket.description,
                "priority": ticket.priority,
            },
            "graph": None,
            "notes": [],
        }

        graph = Graph.query.filter_by(project_id=ticket.project_id).first()
        if graph:
            nodes = graph.nodes if graph.nodes else []
            edges = graph.edges if graph.edges else []
            relevant_nodes, relevant_edges = self._filter_relevant_subgraph(nodes, edges, ticket)
            context["graph"] = {
                "nodes": relevant_nodes,
                "edges": relevant_edges,
            }

        notes = Note.query.filter_by(project_id=ticket.project_id).all()
        context["notes"] = [
            {"title": n.title, "content": n.content, "node_id": n.node_id}
            for n in notes
        ]

        return context

    def _filter_relevant_subgraph(
        self,
        nodes: list,
        edges: list,
        ticket: Ticket,
    ) -> Tuple[list, list]:
        """Get nodes referenced by ticket + all edges connecting those nodes."""
        node_ids = ticket.associated_node_ids or []
        if not node_ids:
            node_ids = [n.get("id") for n in nodes[:10] if n.get("id")]
        node_set = set(node_ids)

        relevant_nodes = [n for n in nodes if n.get("id") in node_set]

        # All edges that connect any of the relevant nodes
        relevant_edges = [
            e for e in edges
            if e.get("source") in node_set or e.get("target") in node_set
        ]

        return relevant_nodes, relevant_edges

    def _send_to_claude_code(
        self,
        prompt: str,
        session_id: str,
        project_path: Optional[str] = None,
        resume: bool = False,
    ) -> dict:
        """Send a prompt to Claude Code and get the response."""
        cmd = [
            *self.claude_code_cmd,
            "-p",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
        ]
        # Prefer explicit model from environment so we don't hit a default
        # like claude-haiku when routing through vLLM.
        model = os.environ.get("ANTHROPIC_MODEL")
        if model:
            cmd.extend(["--model", model])
        if resume:
            cmd.extend(["--resume", session_id, prompt])
        else:
            cmd.extend(["--session-id", session_id, prompt])

        run_kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": 300,
        }
        if project_path and os.path.isdir(project_path):
            run_kwargs["cwd"] = project_path
            self._debug_log(f"Running claude from cwd={project_path}")

        try:
            # Log exact command and options for debugging sessions and cwd
            self._trace_log(
                session_id,
                f"Running Claude Code command:\ncmd={' '.join(cmd)}\nrun_kwargs={run_kwargs}",
                project_path,
            )
            result = subprocess.run(cmd, **run_kwargs)
            return {
                "output": result.stdout,
                "error": result.stderr,
                "return_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "output": "",
                "error": "Timeout after 300 seconds",
                "return_code": -1,
            }

    def _agent_assess(
        self,
        context: dict,
        conversation_history: list[str],
        session_id: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> dict:
        """Call OpenAI-compatible API to assess completion and generate next prompt. Raises AgentAPIError on failure."""
        full_conversation = "\n\n---\n\n".join(
            f"### Claude response {i + 1}:\n{out}" for i, out in enumerate(conversation_history)
        )
        user_msg = f"""Context:
{json.dumps(context, indent=2)}

Full conversation with Claude Code:
{full_conversation}

Assess: Is the ticket complete? Respond in JSON only."""

        headers = {"Content-Type": "application/json"}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"

        # Trace the full agent prompt for debugging
        if session_id:
            self._trace_log(
                session_id,
                "Agent API request:\n"
                f"URL: {self.agent_api_url}\n"
                f"Model: {self.agent_model}\n"
                f"System prompt:\n{AGENT_SYSTEM_PROMPT}\n\n"
                f"User message:\n{user_msg}",
                project_path,
            )

        try:
            resp = requests.post(
                self.agent_api_url,
                json={
                    "model": self.agent_model,
                    "messages": [
                        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.2,
                },
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AgentAPIError(
                f"Agent API request failed: {self.agent_api_url} - {e}",
                cause=e,
            ) from e

        try:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise AgentAPIError(
                f"Agent API returned invalid response format: {e}",
                cause=e,
            ) from e

        self._debug_log(f"Agent API response: {content[:300]}...")
        if session_id:
            self._trace_log(
                session_id,
                f"Agent API raw response content:\n{content}",
                project_path,
            )

        # Parse JSON from response (may be wrapped in markdown)
        content = content.strip()
        if "```" in content:
            start = content.find("```") + 3
            if start > 2 and content[start : start + 4] == "json":
                start += 4
            end = content.find("```", start)
            content = content[start:end] if end > 0 else content[start:]

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            raise AgentAPIError(
                f"Agent API response is not valid JSON: {content[:200]}...",
                cause=e,
            ) from e

        if not isinstance(parsed, dict):
            raise AgentAPIError(f"Agent API response must be a JSON object, got: {type(parsed)}")

        return {
            "complete": parsed.get("complete", False),
            "summary": parsed.get("summary", ""),
            "next_prompt": parsed.get("next_prompt", ""),
        }

    def _finalize(self, ticket: Ticket, session_id: str) -> None:
        """Finalize the ticket."""
        self._log(
            ticket.project_id,
            ticket.id,
            session_id,
            "finalize",
            "Finalizing implementation: commit, push, PR creation",
        )
        ticket.status = "completed"
        db.session.commit()

    def _log(
        self,
        project_id: uuid.UUID,
        ticket_id: uuid.UUID,
        session_id: str,
        step: str,
        summary: str,
        raw_output: Optional[str] = None,
    ) -> None:
        """Log an execution step."""
        log_entry = ExecutionLog(
            project_id=project_id,
            ticket_id=ticket_id,
            session_id=session_id,
            step=step,
            summary=summary,
            raw_output=raw_output,
            success=True,
        )
        db.session.add(log_entry)
        db.session.commit()


def cancel_ticket_execution(ticket_id: uuid.UUID) -> bool:
    """
    Signal cancellation for an in-progress ticket.

    This does not forcibly kill subprocesses, but the MiddleAgent will
    check this flag between turns and stop scheduling further work.
    """
    session = _active_sessions.get(ticket_id)
    if not session:
        return False
    session["cancel"] = True
    return True
