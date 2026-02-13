"""
Middle Agent for Terarchitect
"""
import os
import re
import sys
import json
import subprocess
import uuid
from datetime import datetime
import requests
from typing import Any, Dict, List, Optional

from flask import current_app
from models.db import db, Ticket, ExecutionLog, PR

# Track active agent sessions so they can be cancelled.
_active_sessions: Dict[uuid.UUID, Dict[str, Any]] = {}

# Prompts loaded from prompts.json (same dir as this module). Fails if file missing or invalid.
_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROMPTS_PATH = os.path.join(_PROMPTS_DIR, "prompts.json")
_FEEDBACK_STYLE_PATH = os.path.join(_PROMPTS_DIR, "feedback_example.txt")
_REQUIRED_PROMPT_KEYS = ("agent_system_prompt", "worker_first_prompt_prefix", "worker_review_prompt_prefix")


def _load_prompts() -> Dict[str, str]:
    """Load prompts from prompts.json. Raises if file missing, invalid JSON, or required key missing."""
    if not os.path.isfile(_PROMPTS_PATH):
        raise FileNotFoundError(f"Prompts file required but not found: {_PROMPTS_PATH}")
    with open(_PROMPTS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"prompts.json must be a JSON object, got {type(raw)}")
    out: Dict[str, str] = {}
    for key, val in raw.items():
        if isinstance(val, list):
            out[key] = "\n".join(str(line) for line in val)
        else:
            out[key] = str(val)
    for key in _REQUIRED_PROMPT_KEYS:
        if key not in out or not (out[key] or "").strip():
            raise ValueError(f"prompts.json must define a non-empty '{key}'")
    return out


def _load_feedback_style() -> str:
    """Load optional feedback_example.txt for communication style. Empty string if missing."""
    if not os.path.isfile(_FEEDBACK_STYLE_PATH):
        return ""
    try:
        with open(_FEEDBACK_STYLE_PATH, encoding="utf-8") as f:
            content = f.read().strip()
        return content
    except Exception:
        return ""


def get_agent_system_prompt() -> str:
    base = _load_prompts()["agent_system_prompt"]
    style = _load_feedback_style()
    if not style:
        return base
    return base + "\n\n---\nCommunication style (use this tone when directing the worker; draw from these examples):\n\n" + style


def get_worker_first_prompt_prefix() -> str:
    return _load_prompts()["worker_first_prompt_prefix"]


def get_worker_review_prompt_prefix() -> str:
    return _load_prompts()["worker_review_prompt_prefix"]


# Cap Director conversation context before summarization (model max often ~170k).
DIRECTOR_CONTEXT_TOKEN_LIMIT = 150_000

# Number of Director messages to summarize at once (2 user + 2 assistant = 2 full turns).
_DIRECTOR_COMPACT_CHUNK_SIZE = 4


def _count_tokens_for_messages(messages: List[Dict[str, str]]) -> int:
    """Return total token count for a list of message dicts with 'role' and 'content'. Fallback: ~4 chars per token."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            total += len(enc.encode(m.get("content") or ""))
        return total
    except Exception:
        total_chars = sum(len(m.get("content") or "") for m in messages)
        return total_chars // 4


class AgentAPIError(Exception):
    """Raised when the agent's LLM API is unavailable or returns invalid data."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class MiddleAgent:
    """Agent that orchestrates a coding worker CLI for implementation tasks."""

    def __init__(self):
        self.vllm_proxy_url = os.environ.get("VLLM_PROXY_URL", "http://localhost:8080")
        cmd = os.environ.get("OPENCODE_CMD", "opencode")
        self.worker_cmd = cmd.split() if isinstance(cmd, str) else [cmd]
        # Map Terarchitect session_id -> concrete OpenCode session id (ses_*).
        self._opencode_sessions: Dict[str, str] = {}
        # Verbose debug logs (stderr + trace file) default on; set MIDDLE_AGENT_DEBUG=0 to disable.
        self.debug = os.environ.get("MIDDLE_AGENT_DEBUG", "1").lower() not in ("0", "false", "no", "off")

        # OpenAI-compatible API for agent decisions (vLLM, OpenAI, or any compatible endpoint)
        vllm_base = os.environ.get("VLLM_URL", "http://localhost:8000").rstrip("/")
        agent_url = os.environ.get("AGENT_API_URL", "").strip()
        self.agent_api_url = agent_url or f"{vllm_base}/v1/chat/completions"
        self.agent_model = os.environ.get("AGENT_MODEL", "default")
        self.agent_api_key = os.environ.get("AGENT_API_KEY", "").strip() or None

        # OpenCode worker config: use OpenAI-compatible proxy endpoint.
        self.opencode_provider_id = os.environ.get("OPENCODE_PROVIDER_ID", "terarchitect-proxy").strip() or "terarchitect-proxy"
        self.opencode_base_url = os.environ.get("OPENCODE_BASE_URL", "").strip() or f"{self.vllm_proxy_url.rstrip('/')}/v1"
        raw_worker_model = os.environ.get("OPENCODE_MODEL", "").strip()
        self.opencode_model = raw_worker_model or f"{self.opencode_provider_id}/{self.agent_model}"
        self.opencode_api_key = os.environ.get("OPENCODE_API_KEY", "").strip() or "dummy"

    @staticmethod
    def _parse_opencode_json_output(raw_output: str) -> tuple[str, Optional[str]]:
        """
        Parse OpenCode JSON event output and return:
        - joined text parts for assessment
        - discovered OpenCode sessionID (ses_*) if present
        """
        text_parts: List[str] = []
        discovered_session: Optional[str] = None
        for line in (raw_output or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not discovered_session:
                sid = evt.get("sessionID")
                if isinstance(sid, str) and sid.strip():
                    discovered_session = sid.strip()
            if evt.get("type") == "text":
                part = evt.get("part") or {}
                txt = (part.get("text") or "").strip()
                if txt:
                    text_parts.append(txt)
        return ("\n".join(text_parts).strip(), discovered_session)

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

    @staticmethod
    def _extract_memory_passages(results: List[dict]) -> List[str]:
        passages: List[str] = []
        seen = set()
        for result in results:
            for doc in (result.get("docs") or []):
                if not doc or doc in seen:
                    continue
                seen.add(doc)
                passages.append(doc)
        return passages

    @staticmethod
    def _format_memories(passages: List[str]) -> str:
        if not passages:
            return ""
        return "Memories invoked:\n" + "\n".join(passages)

    def _retrieve_memory_passages(
        self,
        ticket: Ticket,
        queries: List[str],
        base_save_dir: Optional[str],
        memory_kwargs: Dict[str, str],
        session_id: str,
        ticket_id: uuid.UUID,
        step_name: str,
    ) -> List[str]:
        if not base_save_dir:
            return []
        try:
            from utils.memory import retrieve as memory_retrieve_fn
            results = memory_retrieve_fn(
                ticket.project_id,
                queries=queries,
                base_save_dir=base_save_dir,
                num_to_retrieve=5,
                **memory_kwargs,
            )
            return self._extract_memory_passages(results)
        except Exception as e:
            self._debug_log(f"{step_name} memory retrieve failed: {e}")
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                step_name,
                f"Memory retrieve failed; continuing without memories: {e}",
            )
            return []

    def _index_completion_memory(
        self,
        ticket: Ticket,
        summary: str,
        base_save_dir: Optional[str],
        memory_kwargs: Dict[str, str],
        session_id: str,
        ticket_id: uuid.UUID,
    ) -> None:
        if not base_save_dir:
            return
        summary_text = (summary or "").strip()
        if not summary_text:
            return
        try:
            from utils.memory import index as memory_index_fn
            desc = (ticket.description or "").strip()
            if desc:
                doc = f"Ticket: {ticket.title}. {desc}. {summary_text}"
            else:
                doc = f"Ticket: {ticket.title}. {summary_text}"
            memory_index_fn(ticket.project_id, [doc], base_save_dir, **memory_kwargs)
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                "memory_indexed",
                "Indexed completion summary into project memory",
            )
        except Exception as e:
            self._debug_log(f"Completion memory index failed: {e}")
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                "memory_index_failed",
                f"Memory index failed; continuing finalization: {e}",
            )

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
        self._log(ticket.project_id, ticket_id, session_id, "session_started", f"Started OpenCode session {session_id}")
        self._debug_log("Session started, loading context...")

        try:
            # Step 1: Load context
            context = self._load_context(ticket)
            self._log(ticket.project_id, ticket_id, session_id, "context_loaded", "Loaded project context and graph")
            from utils.memory import get_hipporag_kwargs

            base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
            memory_kwargs = get_hipporag_kwargs()

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
                self._log(ticket.project_id, ticket_id, session_id, "cancelled", "Execution cancelled before first worker turn")
                return

            # Create and checkout ticket branch so multiple agents can work in parallel.
            branch_name = self._ensure_ticket_branch(ticket, project_path, session_id, ticket_id)
            if branch_name:
                self._log(ticket.project_id, ticket_id, session_id, "branch_created", f"Branch {branch_name} checked out")

            # Step 2: Build initial prompt and send (slim context for worker: only this ticket + relevant graph)
            worker_context = {
                "project_name": context.get("project_name"),
                "project_path": context.get("project_path"),
                "current_ticket": context.get("current_ticket"),
                "graph_relevant_to_current_ticket": context.get("graph_relevant_to_current_ticket"),
            }
            task_instruction = (
                get_worker_first_prompt_prefix() + "\n\nContext:\n" + json.dumps(worker_context, indent=2)
            )
            start_query = f"{ticket.title}. {(ticket.description or '').strip()}".strip()
            # Project-wide query so the agent can use what was learned from other tickets
            project_context_query = "What has been done in this project? Completed work and summaries."
            start_memory_passages = self._retrieve_memory_passages(
                ticket=ticket,
                queries=[start_query, project_context_query],
                base_save_dir=base_save_dir,
                memory_kwargs=memory_kwargs,
                session_id=session_id,
                ticket_id=ticket_id,
                step_name="memory_retrieve_start",
            )
            self._trace_log(
                session_id,
                f"Prompt to OpenCode (turn 0):\n{task_instruction}",
                project_path,
            )
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                "worker_turn_0_prompt",
                "Initial prompt sent to OpenCode",
                raw_output=task_instruction,
            )
            response = self._send_to_opencode(
                task_instruction,
                session_id,
                project_path,
                resume=False,
            )
            self._trace_log(
                session_id,
                "OpenCode response (turn 0):\n"
                f"return_code={response.get('return_code')}\n"
                f"stdout:\n{response.get('output')}\n"
                f"stderr:\n{response.get('error')}",
                project_path,
            )
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                "worker_turn_0",
                "Initial prompt sent",
                raw_output=response.get("output"),
            )
            self._debug_log(f"Turn 0 output: {response.get('output', '')[:500]}...")

            # Step 3: Loop until task is complete
            conversation_history: list[str] = [response.get("output") or ""]
            prompt_history: list[str] = [task_instruction]
            director_messages: List[Dict[str, str]] = []
            completion_summary: Optional[str] = None
            max_turns = 1000
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

                latest_output = conversation_history[-1] if conversation_history else ""
                last_prompt = prompt_history[-1] if prompt_history else task_instruction
                combined_query = f"{last_prompt[:500]}\n{latest_output[:500]}".strip()
                turn_memory_passages = self._retrieve_memory_passages(
                    ticket=ticket,
                    queries=[combined_query],
                    base_save_dir=base_save_dir,
                    memory_kwargs=memory_kwargs,
                    session_id=session_id,
                    ticket_id=ticket_id,
                    step_name=f"memory_retrieve_turn_{turn}",
                )
                memory_passages = list(start_memory_passages if turn == 0 else [])
                for passage in turn_memory_passages:
                    if passage not in memory_passages:
                        memory_passages.append(passage)
                memories = self._format_memories(memory_passages)

                agent_response, director_messages = self._agent_assess(
                    context,
                    prompt_history,
                    conversation_history,
                    memories=memories,
                    director_messages=director_messages,
                    session_id=session_id,
                    project_path=project_path,
                )
                if agent_response.get("complete"):
                    completion_summary = agent_response.get("summary", "Task completed")
                    self._index_completion_memory(
                        ticket=ticket,
                        summary=completion_summary,
                        base_save_dir=base_save_dir,
                        memory_kwargs=memory_kwargs,
                        session_id=session_id,
                        ticket_id=ticket_id,
                    )
                    self._log(
                        ticket.project_id,
                        ticket_id,
                        session_id,
                        "task_complete",
                        completion_summary,
                    )
                    break

                next_prompt = agent_response.get("next_prompt")
                if not next_prompt:
                    raise AgentAPIError("Agent API returned no next_prompt when task is incomplete")
                # Encourage careful execution for implementation prompts, not meta-assessment.
                if "assess: is the ticket complete" not in next_prompt.lower():
                    if "one file at a time" not in next_prompt.lower() and "slowly" not in next_prompt.lower():
                        next_prompt = "Work VERY slowly: modify one file at a time, verify each change before proceeding.\n\n" + next_prompt

                self._trace_log(
                    session_id,
                    f"Prompt to OpenCode (turn {turn + 1}):\n{next_prompt}",
                    project_path,
                )
                response = self._send_to_opencode(next_prompt, session_id, project_path, resume=True)
                prompt_history.append(next_prompt)
                conversation_history.append(response.get("output") or "")
                self._trace_log(
                    session_id,
                    f"OpenCode response (turn {turn + 1}):\n"
                    f"return_code={response.get('return_code')}\n"
                    f"stdout:\n{response.get('output')}\n"
                    f"stderr:\n{response.get('error')}",
                    project_path,
                )
                self._log(
                    ticket.project_id,
                    ticket_id,
                    session_id,
                    f"worker_turn_{turn + 1}",
                    f"Turn {turn + 1} completed",
                    raw_output=response.get("output"),
                )
                self._debug_log(f"Turn {turn + 1} output: {response.get('output', '')[:500]}...")

            # Step 4: Finalize (commit, push, PR, move to In Review)
            self._finalize(
                ticket,
                session_id,
                project_path=project_path,
                completion_summary=completion_summary,
            )
        finally:
            _active_sessions.pop(ticket.id, None)

    def process_ticket_review(
        self,
        ticket_id: uuid.UUID,
        comment_body: str,
        pr_number: int,
    ) -> None:
        """Run the agent to address PR review feedback, then post summary as a new PR comment."""
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            self._debug_log("Ticket not found for review, exiting")
            return
        if ticket.column_id != "in_review":
            self._debug_log(f"Ticket not in_review (column_id={ticket.column_id}), skipping review run")
            return
        session_id = str(uuid.uuid4())
        _active_sessions[ticket.id] = {"cancel": False, "session_id": session_id}
        self._log(ticket.project_id, ticket_id, session_id, "review_started", "Started PR review feedback session")
        try:
            context = self._load_context(ticket)
            context["pr_review_comment"] = comment_body
            self._log(ticket.project_id, ticket_id, session_id, "context_loaded", "Loaded context for PR review")
            from utils.memory import get_hipporag_kwargs
            base_save_dir = current_app.config.get("MEMORY_SAVE_DIR")
            memory_kwargs = get_hipporag_kwargs()
            project_path = (context.get("project_path") or "").strip() or None
            if not project_path or not os.path.isdir(project_path):
                self._log(ticket.project_id, ticket_id, session_id, "invalid_project_path", "Invalid project_path for review")
                return
            if _active_sessions.get(ticket.id, {}).get("cancel"):
                return
            if not self._checkout_ticket_branch(ticket, project_path):
                self._log(ticket.project_id, ticket_id, session_id, "checkout_failed", "Could not checkout ticket branch for review")
                return
            self._log(ticket.project_id, ticket_id, session_id, "branch_checked_out", "Checked out ticket branch for review")
            worker_context = {
                "project_name": context.get("project_name"),
                "project_path": context.get("project_path"),
                "current_ticket": context.get("current_ticket"),
                "graph_relevant_to_current_ticket": context.get("graph_relevant_to_current_ticket"),
                "pr_review_comment": comment_body,
            }
            task_instruction = (
                get_worker_review_prompt_prefix()
                + "\n\nReview comment:\n"
                + comment_body
                + "\n\nContext:\n"
                + json.dumps(worker_context, indent=2)
            )
            start_query = f"PR review: {comment_body[:200]}"
            start_memory_passages = self._retrieve_memory_passages(
                ticket=ticket,
                queries=[start_query],
                base_save_dir=base_save_dir,
                memory_kwargs=memory_kwargs,
                session_id=session_id,
                ticket_id=ticket_id,
                step_name="memory_retrieve_review",
            )
            self._trace_log(
                session_id,
                f"Prompt to OpenCode (review turn 0):\n{task_instruction}",
                project_path,
            )
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                "worker_turn_0_prompt",
                "Review prompt sent to OpenCode",
                raw_output=task_instruction,
            )
            response = self._send_to_opencode(task_instruction, session_id, project_path, resume=False)
            self._log(ticket.project_id, ticket_id, session_id, "worker_turn_0", "Review prompt sent", raw_output=response.get("output"))
            conversation_history: list[str] = [response.get("output") or ""]
            prompt_history: list[str] = [task_instruction]
            director_messages: List[Dict[str, str]] = []
            completion_summary: Optional[str] = None
            max_turns = 50
            for turn in range(max_turns):
                if _active_sessions.get(ticket.id, {}).get("cancel"):
                    return
                latest_output = conversation_history[-1] if conversation_history else ""
                last_prompt = prompt_history[-1] if prompt_history else task_instruction
                combined_query = f"{last_prompt[:500]}\n{latest_output[:500]}".strip()
                turn_memory_passages = self._retrieve_memory_passages(
                    ticket=ticket,
                    queries=[combined_query],
                    base_save_dir=base_save_dir,
                    memory_kwargs=memory_kwargs,
                    session_id=session_id,
                    ticket_id=ticket_id,
                    step_name=f"memory_retrieve_review_turn_{turn}",
                )
                memory_passages = list(start_memory_passages)
                for passage in turn_memory_passages:
                    if passage not in memory_passages:
                        memory_passages.append(passage)
                memories = self._format_memories(memory_passages)
                agent_response, director_messages = self._agent_assess(
                    context,
                    prompt_history,
                    conversation_history,
                    memories=memories,
                    director_messages=director_messages,
                    session_id=session_id,
                    project_path=project_path,
                )
                if agent_response.get("complete"):
                    completion_summary = agent_response.get("summary", "Addressed review feedback.")
                    self._log(ticket.project_id, ticket_id, session_id, "review_complete", completion_summary)
                    break
                next_prompt = agent_response.get("next_prompt")
                if not next_prompt:
                    raise AgentAPIError("Agent API returned no next_prompt when task is incomplete")
                if "assess: is the ticket complete" not in next_prompt.lower():
                    if "one file at a time" not in next_prompt.lower() and "slowly" not in next_prompt.lower():
                        next_prompt = "Work VERY slowly: modify one file at a time, verify each change before proceeding.\n\n" + next_prompt
                response = self._send_to_opencode(next_prompt, session_id, project_path, resume=True)
                prompt_history.append(next_prompt)
                conversation_history.append(response.get("output") or "")
                self._log(ticket.project_id, ticket_id, session_id, f"worker_turn_{turn + 1}", "Turn completed", raw_output=response.get("output"))
            # Generate a direct reply to the reviewer's comment (not a generic task summary).
            pr_comment_body = self._generate_pr_comment_reply(comment_body, completion_summary or "")
            self._finalize(
                ticket,
                session_id,
                project_path=project_path,
                completion_summary=completion_summary,
                review_mode=True,
                pr_number_for_comment=pr_number,
                pr_comment_body=pr_comment_body,
            )
        finally:
            _active_sessions.pop(ticket.id, None)

    @staticmethod
    def _ticket_summary(t: Ticket, mark_current: bool = False) -> dict:
        """Minimal ticket payload for context (id, title, description, priority, column_id, status)."""
        out = {
            "id": str(t.id),
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "column_id": t.column_id,
            "status": t.status,
        }
        if mark_current:
            out["_current_ticket"] = True  # Ticket the agent is supposed to implement
            out["associated_node_ids"] = t.associated_node_ids or []
            out["associated_edge_ids"] = t.associated_edge_ids or []
        return out

    @staticmethod
    def _relevant_subgraph(
        nodes: list,
        edges: list,
        node_ids: list,
        edge_ids: list,
    ) -> tuple:
        """Return (nodes, edges) that are relevant to the given node/edge IDs. Includes edges connecting the nodes."""
        node_set = set(node_ids or [])
        edge_set = set(edge_ids or [])
        if not node_set and not edge_set:
            return [], []
        relevant_nodes = [n for n in nodes if n.get("id") in node_set]
        # Edges: explicitly associated or that connect any of the relevant nodes
        relevant_edges = [
            e for e in edges
            if e.get("id") in edge_set
            or e.get("source") in node_set
            or e.get("target") in node_set
        ]
        return relevant_nodes, relevant_edges

    def _load_context(self, ticket: Ticket) -> dict:
        """Load full context: project, whole graph, notes, backlog/in-progress/done tickets, and current ticket."""
        from models.db import Project, Graph, Note, Ticket as TicketModel

        project = Project.query.get(ticket.project_id)
        current_id = ticket.id

        # Current ticket is the one the agent must implement (always first and explicitly marked)
        context = {
            "project_name": project.name,
            "project_description": project.description,
            "project_path": project.project_path,
            "github_url": project.github_url,
            "current_ticket": self._ticket_summary(ticket, mark_current=True),
            "graph": None,
            "notes": [],
            "backlog_tickets": [],
            "in_progress_tickets": [],
            "done_tickets": [],
        }

        # Full architecture graph and the slice relevant to the current ticket
        graph = Graph.query.filter_by(project_id=ticket.project_id).first()
        if graph:
            nodes = graph.nodes if graph.nodes else []
            edges = graph.edges if graph.edges else []
            context["graph"] = {"nodes": nodes, "edges": edges}
            rel_nodes, rel_edges = self._relevant_subgraph(
                nodes,
                edges,
                ticket.associated_node_ids or [],
                ticket.associated_edge_ids or [],
            )
            context["graph_relevant_to_current_ticket"] = {
                "nodes": rel_nodes,
                "edges": rel_edges,
            }
        else:
            context["graph_relevant_to_current_ticket"] = {"nodes": [], "edges": []}

        notes = Note.query.filter_by(project_id=ticket.project_id).all()
        context["notes"] = [
            {"title": n.title, "content": n.content, "node_id": n.node_id}
            for n in notes
        ]

        # Backlog: up to 10; In progress: 5; Done: 5 (excluding current from counts where it appears)
        backlog = (
            TicketModel.query.filter_by(project_id=ticket.project_id, column_id="backlog")
            .order_by(TicketModel.updated_at.desc())
            .limit(10)
            .all()
        )
        in_progress = (
            TicketModel.query.filter_by(project_id=ticket.project_id, column_id="in_progress")
            .order_by(TicketModel.updated_at.desc())
            .limit(6)
            .all()
        )
        done = (
            TicketModel.query.filter_by(project_id=ticket.project_id, column_id="done")
            .order_by(TicketModel.updated_at.desc())
            .limit(6)
            .all()
        )

        context["backlog_tickets"] = [self._ticket_summary(t) for t in backlog[:10]]
        # In progress: include current ticket, mark it; then fill to 5 total
        in_progress_summaries = []
        for t in in_progress:
            if t.id == current_id:
                in_progress_summaries.insert(0, self._ticket_summary(t, mark_current=True))
            else:
                in_progress_summaries.append(self._ticket_summary(t))
        context["in_progress_tickets"] = in_progress_summaries[:5]
        context["done_tickets"] = [self._ticket_summary(t) for t in done[:5]]

        return context

    def _send_to_opencode(
        self,
        prompt: str,
        session_id: str,
        project_path: Optional[str] = None,
        resume: bool = False,
    ) -> dict:
        """Send a prompt to OpenCode and get the response."""
        worker_session_id = self._opencode_sessions.get(session_id)
        cmd = [
            *self.worker_cmd,
            "run",
            "--format",
            "json",
            "--model",
            self.opencode_model,
        ]
        # Use explicit OpenCode session IDs for safe multi-run concurrency.
        if resume:
            if worker_session_id:
                cmd.extend(["--session", worker_session_id])
            else:
                # No known session yet; fall back to fresh run for robustness.
                self._debug_log(f"No OpenCode session recorded for {session_id}; starting fresh run.")
                cmd.extend(["--title", f"terarchitect-{session_id}"])
        else:
            cmd.extend(["--title", f"terarchitect-{session_id}"])
        cmd.append(prompt)

        timeout_sec = int(os.environ.get("OPENCODE_TIMEOUT_SEC", "3600"))  # 20 min default for long tool-call runs
        run_kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": timeout_sec,
        }
        if project_path and os.path.isdir(project_path):
            run_kwargs["cwd"] = project_path
            self._debug_log(f"Running opencode from cwd={project_path}")

        # Ensure OpenCode uses an OpenAI-compatible provider pointing at the proxy.
        env = dict(os.environ)
        if not env.get("OPENCODE_CONFIG_CONTENT"):
            local_model_name = self.opencode_model
            provider_prefix = f"{self.opencode_provider_id}/"
            if local_model_name.startswith(provider_prefix):
                local_model_name = local_model_name[len(provider_prefix) :]
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
                {
                    "model": f"{self.opencode_provider_id}/{local_model_name}",
                    "provider": {
                        self.opencode_provider_id: {
                            "npm": "@ai-sdk/openai-compatible",
                            "name": "Terarchitect Proxy",
                            "options": {
                                "baseURL": self.opencode_base_url,
                                "apiKey": self.opencode_api_key,
                            },
                            "models": {
                                local_model_name: {
                                    "name": local_model_name,
                                    "tool_call": True,
                                }
                            },
                        }
                    }
                }
            )
        run_kwargs["env"] = env

        try:
            # Log exact command and options for debugging sessions and cwd
            self._trace_log(
                session_id,
                f"Running OpenCode command:\ncmd={' '.join(cmd)}\nrun_kwargs={run_kwargs}",
                project_path,
            )
            result = subprocess.run(cmd, **run_kwargs)
            # OpenCode emits JSON events on stdout in --format json mode.
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            parsed_text, discovered_session = self._parse_opencode_json_output(out)
            if discovered_session:
                self._opencode_sessions[session_id] = discovered_session
            # Trace raw capture so we can debug empty responses (e.g. CLI writing elsewhere or failing silently)
            if session_id and project_path:
                self._trace_log(
                    session_id,
                    f"OpenCode subprocess result: return_code={result.returncode} len(stdout)={len(out)} len(stderr)={len(err)}\n"
                    f"parsed_text_len={len(parsed_text)} discovered_session={discovered_session!r}\n"
                    f"stdout preview: {out[:500]!r}\nstderr preview: {err[:500]!r}",
                    project_path,
                )
            return {
                "output": parsed_text or out or err,
                "error": result.stderr,
                "return_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            timeout_msg = f"Worker run timed out after {timeout_sec} seconds. The run was killed before returning any output."
            return {
                "output": timeout_msg,
                "error": "Timeout after 300 seconds",
                "return_code": -1,
            }

    def _summarize_director_messages(self, messages: List[Dict[str, str]]) -> str:
        """Call the agent API to summarize a chunk of Director conversation. Returns summary text."""
        formatted = "\n\n".join(
            f"**{m.get('role', '')}**:\n{m.get('content') or ''}" for m in messages
        )
        system = """You are summarizing a conversation between the Director (an agent that assesses worker output and decides the next prompt) and the system.
Preserve: project/ticket context if present, completion decisions (complete vs not), key next prompts given to the worker, and worker outcomes.
Output a single concise narrative. No JSON, no labels—just prose."""
        headers = {"Content-Type": "application/json"}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"
        try:
            resp = requests.post(
                self.agent_api_url,
                json={
                    "model": self.agent_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": formatted},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.2,
                },
                headers=headers,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            return (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        except Exception as e:
            self._debug_log(f"Summarization API call failed: {e}, using truncation")
            return formatted[:4000] + "\n\n[... truncated ...]" if len(formatted) > 4000 else formatted

    def _compact_director_messages(
        self,
        director_messages: List[Dict[str, str]],
        new_user_content: str,
        system_content: str,
    ) -> List[Dict[str, str]]:
        """If token count of [system, *director_messages, new_user] exceeds limit, summarize oldest chunks until under limit."""
        out = list(director_messages)
        new_user_msg = {"role": "user", "content": new_user_content}
        system_msg = {"role": "system", "content": system_content}
        while True:
            full = [system_msg] + out + [new_user_msg]
            if _count_tokens_for_messages(full) <= DIRECTOR_CONTEXT_TOKEN_LIMIT:
                return out
            if len(out) < _DIRECTOR_COMPACT_CHUNK_SIZE:
                return out
            chunk = out[:_DIRECTOR_COMPACT_CHUNK_SIZE]
            summary = self._summarize_director_messages(chunk)
            summary_msg = {"role": "user", "content": "Previous conversation (summarized):\n\n" + summary}
            out = [summary_msg] + out[_DIRECTOR_COMPACT_CHUNK_SIZE:]

    def _agent_assess(
        self,
        context: dict,
        prompt_history: List[str],
        conversation_history: List[str],
        memories: str = "",
        director_messages: Optional[List[Dict[str, str]]] = None,
        session_id: Optional[str] = None,
        project_path: Optional[str] = None,
    ) -> tuple[Dict[str, Any], List[Dict[str, str]]]:
        """Call OpenAI-compatible API to assess completion and generate next prompt. Returns (response_dict, updated director_messages)."""
        director_messages = director_messages or []
        system_content = get_agent_system_prompt()
        memory_block = f"{memories}\n\n" if memories else ""

        if not director_messages:
            # First turn: send full context and full worker conversation so far.
            turns = []
            for i in range(max(len(prompt_history), len(conversation_history))):
                prompt = prompt_history[i] if i < len(prompt_history) else ""
                response = conversation_history[i] if i < len(conversation_history) else ""
                turns.append(
                    f"### Turn {i + 1} - Prompt to Worker:\n{prompt}\n\n"
                    f"### Turn {i + 1} - Worker response:\n{response}"
                )
            full_conversation = "\n\n---\n\n".join(turns)
            user_msg_content = f"""Context:
{json.dumps(context, indent=2)}

{memory_block}Full conversation with Worker:
{full_conversation}

Assess: Is the ticket complete? Respond in JSON only."""
        else:
            # Later turns: only the new worker turn (history is in director_messages).
            n = max(len(prompt_history), len(conversation_history))
            prompt = prompt_history[n - 1] if n and n <= len(prompt_history) else ""
            response = conversation_history[n - 1] if n and n <= len(conversation_history) else ""
            user_msg_content = f"""{memory_block}New worker turn:

### Turn {n} - Prompt to Worker:
{prompt}

### Turn {n} - Worker response:
{response}

Assess: Is the ticket complete? Respond in JSON only."""

        new_user_msg = {"role": "user", "content": user_msg_content}
        compacted = self._compact_director_messages(director_messages, user_msg_content, system_content)
        messages_for_api = [{"role": "system", "content": system_content}] + compacted + [new_user_msg]

        headers = {"Content-Type": "application/json"}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"

        if session_id:
            self._trace_log(
                session_id,
                "Agent API request (stateful):\n"
                f"URL: {self.agent_api_url}\n"
                f"Model: {self.agent_model}\n"
                f"Messages count: {len(messages_for_api)}\n"
                f"System prompt length: {len(system_content)} chars\n"
                f"Last user message:\n{user_msg_content[:1500]}...",
                project_path,
            )

        try:
            resp = requests.post(
                self.agent_api_url,
                json={
                    "model": self.agent_model,
                    "messages": messages_for_api,
                    "max_tokens": 1024,
                    "temperature": 0.2,
                },
                headers=headers,
                timeout=300,
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

        response_dict = {
            "complete": parsed.get("complete", False),
            "summary": parsed.get("summary", ""),
            "next_prompt": parsed.get("next_prompt", ""),
        }
        assistant_msg = {"role": "assistant", "content": content.strip()}
        updated_director = compacted + [new_user_msg, assistant_msg]
        return response_dict, updated_director

    def _generate_pr_comment_reply(self, comment_body: str, completion_summary: str) -> str:
        """
        Ask the LLM for a short direct reply to the reviewer's comment. This reply will be
        posted as the PR comment so the human sees an answer to their question, not a generic
        task summary. On failure returns completion_summary.
        """
        user_msg = f"""A reviewer left this comment on a PR:

\"\"\"
{comment_body}
\"\"\"

The implementation work produced this summary:

\"\"\"
{completion_summary or "(No summary)"}
\"\"\"

Write a short direct reply to the reviewer (2–5 sentences) that answers their question or addresses their point. If they asked a specific question (e.g. "Do we update X on the backend?"), answer it directly (e.g. "Yes, we update X in ..." or "No; I've added that in ..."). Do not post a generic "ticket completed" summary. Output only the reply text, no preamble or labels."""

        headers = {"Content-Type": "application/json"}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"
        try:
            resp = requests.post(
                self.agent_api_url,
                json={
                    "model": self.agent_model,
                    "messages": [
                        {"role": "user", "content": user_msg},
                    ],
                    "max_tokens": 512,
                    "temperature": 0.2,
                },
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            if content:
                return content
        except Exception as e:
            self._debug_log(f"PR comment reply generation failed: {e}, using completion summary")
        return completion_summary or "Addressed review feedback."

    def _ensure_ticket_branch(
        self,
        ticket: Ticket,
        project_path: str,
        session_id: str,
        ticket_id: uuid.UUID,
    ) -> Optional[str]:
        """Create and checkout branch ticket-{ticket_id} from default branch. Returns branch name or None on failure."""
        branch_name = f"ticket-{ticket_id}"
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                self._debug_log(f"Not a git repo at {project_path}, skipping branch creation")
                return None
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=project_path,
                capture_output=True,
                timeout=30,
            )
            default = None
            for candidate in ("main", "master"):
                r = subprocess.run(
                    ["git", "rev-parse", f"origin/{candidate}"],
                    cwd=project_path,
                    capture_output=True,
                    timeout=5,
                )
                if r.returncode == 0:
                    default = candidate
                    break
            if not default:
                self._debug_log("Could not determine default branch (main/master), skipping branch creation")
                return None
            # Update local default branch so the ticket branch is based on latest main.
            subprocess.run(
                ["git", "checkout", default],
                cwd=project_path,
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                ["git", "pull", "origin", default],
                cwd=project_path,
                capture_output=True,
                timeout=60,
            )
            r = subprocess.run(
                ["git", "checkout", "-b", branch_name],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode != 0:
                if "already exists" in (r.stderr or ""):
                    subprocess.run(
                        ["git", "checkout", branch_name],
                        cwd=project_path,
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    self._debug_log(f"Branch create/checkout failed: {r.stderr}")
                    return None
            return branch_name
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            self._debug_log(f"Branch creation error: {e}")
            return None

    def _checkout_ticket_branch(self, ticket: Ticket, project_path: str) -> bool:
        """Checkout existing branch ticket-{ticket.id}. Returns True if successful."""
        branch_name = f"ticket-{ticket.id}"
        try:
            r = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            self._debug_log(f"Checkout branch error: {e}")
            return False

    def _generate_pr_description(
        self,
        ticket_title: str,
        ticket_description: Optional[str],
        completion_summary: Optional[str],
    ) -> Optional[str]:
        """Ask the LLM to generate a descriptive PR body for what was accomplished. Returns None on failure."""
        if not (completion_summary or "").strip():
            return None
        user_content = f"""Ticket title: {ticket_title}
Ticket description: {ticket_description or "(none)"}

Summary of what was done: {completion_summary}

Write a clear, descriptive paragraph for the PR description explaining what was accomplished: files changed, behavior added or fixed, and any notable decisions. Plain text only, no markdown headers. Keep it under 400 words."""
        headers = {"Content-Type": "application/json"}
        if self.agent_api_key:
            headers["Authorization"] = f"Bearer {self.agent_api_key}"
        try:
            resp = requests.post(
                self.agent_api_url,
                json={
                    "model": self.agent_model,
                    "messages": [
                        {"role": "system", "content": "You write concise, accurate PR descriptions for code changes. Output only the paragraph, no labels or prefixes."},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": 512,
                    "temperature": 0.3,
                },
                headers=headers,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            return content if content else None
        except Exception as e:
            self._debug_log(f"PR description generation failed: {e}")
            return None

    def _finalize(
        self,
        ticket: Ticket,
        session_id: str,
        project_path: Optional[str] = None,
        completion_summary: Optional[str] = None,
        review_mode: bool = False,
        pr_number_for_comment: Optional[int] = None,
        pr_comment_body: Optional[str] = None,
    ) -> None:
        """Commit, push. If review_mode: post pr_comment_body (direct reply to reviewer) as PR comment. Else: create PR and move ticket to In Review."""
        self._log(
            ticket.project_id,
            ticket.id,
            session_id,
            "finalize",
            "Finalizing: commit, push" + (", PR comment" if review_mode else ", PR creation, move to In Review"),
        )
        branch_name = f"ticket-{ticket.id}"
        commit_message = (completion_summary or ticket.title or "Implementation").strip()
        if len(commit_message) > 200:
            commit_message = commit_message[:197] + "..."
        pr_url = None
        pr_number = None
        if project_path and os.path.isdir(project_path):
            try:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=project_path,
                    capture_output=True,
                    timeout=10,
                )
                r = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if (r.stdout or "").strip():
                    subprocess.run(
                        ["git", "commit", "-m", commit_message],
                        cwd=project_path,
                        capture_output=True,
                        timeout=10,
                    )
                subprocess.run(
                    ["git", "push", "-u", "origin", branch_name],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if review_mode and pr_number_for_comment is not None:
                    body = (pr_comment_body or completion_summary or "Addressed review feedback.").strip()
                    if len(body) > 60000:
                        body = body[:59997] + "..."
                    subprocess.run(
                        ["gh", "pr", "comment", str(pr_number_for_comment), "--body", body],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                elif not review_mode:
                    body = f"Ticket: {ticket.title}\n\n{(ticket.description or '')[:500]}"
                    pr_desc = self._generate_pr_description(
                        ticket.title or "",
                        ticket.description,
                        completion_summary,
                    )
                    if pr_desc:
                        body = body + "\n\n---\n\n## What was accomplished\n\n" + pr_desc
                    if len(body) > 60000:
                        body = body[:59997] + "..."
                    pr_create = subprocess.run(
                        [
                            "gh", "pr", "create",
                            "--title", (ticket.title or "Implementation")[:256],
                            "--body", body,
                            "--head", branch_name,
                        ],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if pr_create.returncode == 0 and pr_create.stdout:
                        pr_url = pr_create.stdout.strip()
                        try:
                            m = re.search(r"/pull/(\d+)", pr_url)
                            if m:
                                pr_number = int(m.group(1))
                        except (ValueError, AttributeError):
                            pass
            except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
                self._debug_log(f"Finalize git/PR error: {e}")
        if not review_mode:
            ticket.status = "completed"
            ticket.column_id = "in_review"
            if pr_url is not None:
                existing = PR.query.filter_by(ticket_id=ticket.id).first()
                if existing:
                    existing.pr_url = pr_url
                    existing.pr_number = pr_number
                else:
                    db.session.add(PR(
                        project_id=ticket.project_id,
                        ticket_id=ticket.id,
                        pr_url=pr_url,
                        pr_number=pr_number,
                    ))
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
