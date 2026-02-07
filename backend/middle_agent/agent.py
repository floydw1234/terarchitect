"""
Middle Agent for Terarchitect
"""
import os
import requests
import json
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from ..models.db import db, Ticket, ExecutionLog


class MiddleAgent:
    """Agent that orchestrates Claude Code for implementation tasks."""

    def __init__(self):
        self.vllm_url = os.environ.get("VLLM_URL", "http://localhost:8000")
        self.vllm_proxy_url = os.environ.get("VLLM_PROXY_URL", "http://localhost:8080")
        self.claude_code_cmd = ["claude"]

    def process_ticket(self, ticket_id: uuid.UUID) -> None:
        """Process a ticket from start to finish."""
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return

        session_id = str(uuid.uuid4())
        log_entry = ExecutionLog(
            project_id=ticket.project_id,
            ticket_id=ticket_id,
            session_id=session_id,
            step="session_started",
            summary=f"Started Claude Code session {session_id}",
            success=True,
        )
        db.session.add(log_entry)
        db.session.commit()

        # Step 1: Load context
        context = self._load_context(ticket)
        self._log(ticket.project_id, ticket_id, session_id, "context_loaded", "Loaded project context and graph")

        # Step 2: Send initial prompt to Claude Code
        response = self._send_to_claude_code(context, session_id)
        self._log(ticket.project_id, ticket_id, session_id, "prompt_sent", "Sent initial task to Claude Code")

        # Step 3: Loop until task is complete
        max_turns = 50  # Prevent infinite loops
        for turn in range(max_turns):
            if self._is_complete(response):
                self._log(ticket.project_id, ticket_id, session_id, "task_complete", "Task completed successfully")
                break

            # Get next action
            next_prompt = self._generate_next_prompt(response, context)
            response = self._send_to_claude_code(next_prompt, session_id)
            self._log(ticket.project_id, ticket_id, session_id, "turn_complete", f"Turn {turn + 1} completed")

        # Step 4: Finalize
        self._finalize(ticket, session_id)

    def _load_context(self, ticket: Ticket) -> dict:
        """Load relevant context for the ticket."""
        from ..models.db import Project, Graph, Note

        project = Project.query.get(ticket.project_id)

        context = {
            "project_name": project.name,
            "project_description": project.description,
            "git_repo_path": project.git_repo_path,
            "ticket": {
                "id": str(ticket.id),
                "title": ticket.title,
                "description": ticket.description,
                "priority": ticket.priority,
            },
            "graph": None,
            "notes": [],
        }

        # Load graph if project has one
        graph = Graph.query.filter_by(project_id=ticket.project_id).first()
        if graph:
            # Filter to only relevant nodes/edges
            relevant_nodes = self._filter_relevant_nodes(graph.nodes, ticket)
            relevant_edges = self._filter_relevant_edges(graph.edges, ticket)

            context["graph"] = {
                "nodes": relevant_nodes,
                "edges": relevant_edges,
            }

        # Load related notes
        notes = Note.query.filter_by(project_id=ticket.project_id).all()
        context["notes"] = [
            {"title": n.title, "content": n.content, "node_id": n.node_id}
            for n in notes
        ]

        return context

    def _filter_relevant_nodes(self, nodes: list, ticket: Ticket) -> list:
        """Filter graph nodes to only those relevant to the ticket."""
        if not ticket.associated_node_ids:
            return nodes[:10]  # Return first 10 if no specific nodes

        return [n for n in nodes if n.get("id") in ticket.associated_node_ids]

    def _filter_relevant_edges(self, edges: list, ticket: Ticket) -> list:
        """Filter graph edges to only those relevant to the ticket."""
        if not ticket.associated_edge_ids:
            return edges[:20]  # Return first 20 if no specific edges

        return [e for e in edges if e.get("id") in ticket.associated_edge_ids]

    def _send_to_claude_code(self, prompt: str, session_id: str) -> dict:
        """Send a prompt to Claude Code and get the response."""
        # Use claude -p for print mode
        # Format the prompt with session context
        prompt_text = json.dumps({
            "session_id": session_id,
            "prompt": prompt,
        }, indent=2)

        cmd = [
            *self.claude_code_cmd,
            "-p",
            "--dangerously-skip-permissions",
            "--session-id", session_id,
            "--output-format", "json",
            prompt_text,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )
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

    def _is_complete(self, response: dict) -> bool:
        """Determine if the task is complete based on Claude Code's response."""
        # Check for completion markers in output
        if not response.get("output"):
            return False

        try:
            output = json.loads(response["output"])
            if isinstance(output, dict) and output.get("task_complete"):
                return True
        except json.JSONDecodeError:
            pass

        # Check for completion in raw output
        output_text = response.get("output", "").lower()
        completion_markers = [
            "task complete",
            "all done",
            "finished",
            "pr created",
            "committed changes",
        ]

        return any(marker in output_text for marker in completion_markers)

    def _generate_next_prompt(self, response: dict, context: dict) -> str:
        """Generate the next prompt based on the response and context."""
        # Use the vLLM instance to generate the next prompt
        # This is where the "大脑" (brain) decisions happen

        prompt = f"""
Based on the following context and Claude Code's response, what should be the next action?

Context:
{json.dumps(context, indent=2)}

Claude Code Response:
{response.get("output", "")[:1000]}

If the task is complete, respond with {{'complete': true, 'summary': '...'}}
If not, respond with {{'complete': false, 'next_action': '...', 'reason': '...'}}
"""
        return prompt

    def _finalize(self, ticket: Ticket, session_id: str) -> None:
        """Finalize the ticket: commit, push, create PR."""
        # This would:
        # 1. Check git status
        # 2. Add and commit changes
        # 3. Push to remote
        # 4. Create a GitHub PR
        # 5. Update ticket status

        log_entry = ExecutionLog(
            project_id=ticket.project_id,
            ticket_id=ticket.id,
            session_id=session_id,
            step="finalize",
            summary="Finalizing implementation: commit, push, PR creation",
            success=True,
        )
        db.session.add(log_entry)
        db.session.commit()

        # TODO: Implement git operations and PR creation
        ticket.status = "completed"
        db.session.commit()

    def _log(self, project_id: uuid.UUID, ticket_id: uuid.UUID, session_id: str,
             step: str, summary: str) -> None:
        """Log an execution step."""
        log_entry = ExecutionLog(
            project_id=project_id,
            ticket_id=ticket_id,
            session_id=session_id,
            step=step,
            summary=summary,
            success=True,
        )
        db.session.add(log_entry)
        db.session.commit()
