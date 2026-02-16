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
from utils.app_settings import get_gh_env_for_agent, get_setting_or_env

# Track active agent sessions so they can be cancelled.
_active_sessions: Dict[uuid.UUID, Dict[str, Any]] = {}

# Ticket title that triggers execution-only flow (no research/plan). Must match default_tickets.json "Project setup".
PROJECT_SETUP_TICKET_TITLE = "Project setup"

# Prompts loaded from prompts.json (same dir as this module). Fails if file missing or invalid.
_PROMPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROMPTS_PATH = os.path.join(_PROMPTS_DIR, "prompts.json")
_FEEDBACK_STYLE_PATH = os.path.join(_PROMPTS_DIR, "feedback_example.txt")
_REQUIRED_PROMPT_KEYS = ("agent_system_prompt", "worker_first_prompt_prefix", "worker_review_prompt_prefix")
# Optional keys for planning phase (fallbacks used if missing).
_OPTIONAL_PLANNING_KEYS = ("worker_research_prompt_prefix", "worker_plan_prompt_prefix", "agent_plan_review_instructions")


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


def get_worker_review_prompt_prefix() -> str:
    return _load_prompts()["worker_review_prompt_prefix"]


def _get_optional_prompt(key: str, fallback: str) -> str:
    """Return prompt from prompts.json if present and non-empty, else fallback."""
    try:
        prompts = _load_prompts()
        val = (prompts.get(key) or "").strip()
        return val if val else fallback
    except Exception:
        return fallback


def get_worker_research_prompt_prefix() -> str:
    return _get_optional_prompt(
        "worker_research_prompt_prefix",
        "Familiarize yourself with the codebase and the ticket/graph context below. Then do online research (if you have web search) for current best practices for this kind of change. Summarize what you found and how it applies to this ticket. Do not implement yet.",
    )


def get_worker_plan_prompt_prefix(task_plan_path: Optional[str] = None) -> str:
    raw = _get_optional_prompt(
        "worker_plan_prompt_prefix",
        "Create a file at {task_plan_path} with a detailed step-by-step execution plan for the ticket. Use a test-driven development (TDD) approach: for each change, plan to write or update a failing test first, then implement the minimum code to pass it, then refactor if needed. Include: order of work, which files to touch, which tests to add or update (and when), and any dependencies between steps. Do not implement yet.",
    )
    if task_plan_path and "{task_plan_path}" in raw:
        return raw.format(task_plan_path=task_plan_path)
    return raw


def get_agent_plan_review_instructions() -> str:
    return _get_optional_prompt(
        "agent_plan_review_instructions",
        "You are in plan-review mode. Evaluate the plan for consistency, concrete steps, achievability, and logical ordering. Be constructive and concise. Avoid hostile language and avoid repeatedly demanding full-file verbatim pastes unless absolutely necessary. Prefer targeted feedback (max 3 concrete fixes) and keep next_prompt under 180 words with no markdown code fences. If the plan is solid, respond with JSON: {\"plan_approved\": true, \"approved_plan_text\": \"<concise approved execution checklist>\"}. If not, respond with {\"plan_approved\": false, \"next_prompt\": \"<concise feedback and exact fixes>\"}. approved_plan_text should be a concise execution checklist, not a verbatim file dump.",
    )


def _get_task_plan_path(project_path: Optional[str], ticket_id: Optional[uuid.UUID]) -> str:
    """Path to ticket-specific plan file: plan/<ticket_id>_task_plan.md. Raises ValueError if ticket_id is None."""
    if ticket_id is None:
        raise ValueError("ticket_id is required for task plan path")
    if not project_path:
        raise ValueError("project_path is required for task plan path")
    return os.path.join(project_path, "plan", f"{ticket_id}_task_plan.md")


# Cap Director conversation context before summarization (model max often ~170k).
DIRECTOR_CONTEXT_TOKEN_LIMIT = 150_000
# Plan-review tends to get verbose quickly; compact earlier.
DIRECTOR_CONTEXT_TOKEN_LIMIT_PLAN_REVIEW = 80_000
# If first plan-review payload is too large, summarize planning history first.
PLAN_REVIEW_INITIAL_FULL_CONVERSATION_TOKEN_LIMIT = 12_000

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


# Supported worker types: opencode, aider, claude_code, gemini, codex. Only opencode is implemented today.
WORKER_TYPES = ("opencode", "aider", "claude_code", "gemini", "codex")


class MiddleAgent:
    """Agent that orchestrates a coding worker (OpenCode, Aider, Claude Code, Gemini, Codex, etc.) for implementation tasks."""

    def __init__(self):
        # Worker type: opencode | aider | claude_code | gemini | codex. Only opencode is implemented. Command derived from type.
        raw_worker_type = (get_setting_or_env("WORKER_TYPE") or "opencode").strip().lower()
        self.worker_type = raw_worker_type if raw_worker_type in WORKER_TYPES else "opencode"
        self.worker_cmd = [self.worker_type]
        # Map Terarchitect session_id -> concrete worker session id (worker-specific, e.g. ses_* for OpenCode).
        self._worker_sessions: Dict[str, str] = {}
        # Verbose debug logs (stderr + trace file) default on; set MIDDLE_AGENT_DEBUG=0 to disable.
        self.debug = (get_setting_or_env("MIDDLE_AGENT_DEBUG") or "1").lower() not in ("0", "false", "no", "off")

        # Director/agent API (LLM used to assess completion and decide next prompts)
        vllm_base = (get_setting_or_env("VLLM_URL") or "http://localhost:8000").rstrip("/")
        self.agent_api_url = f"{vllm_base}/v1/chat/completions"
        self.agent_model = (get_setting_or_env("AGENT_MODEL") or "Qwen/Qwen3-Coder-Next-FP8").strip()
        self.agent_api_key = (get_setting_or_env("AGENT_API_KEY") or "").strip() or None

        # Worker config (model, LLM URL). WORKER_LLM_URL defaults to http://localhost:8080/v1.
        self.worker_provider_id = "terarchitect-proxy"  # fixed; used in OpenCode provider config
        worker_llm_url = (get_setting_or_env("WORKER_LLM_URL") or "").strip()
        self.worker_llm_url = (worker_llm_url or "http://localhost:8080/v1").rstrip("/")
        raw_worker_model = (get_setting_or_env("WORKER_MODEL") or "").strip()
        self.worker_model = raw_worker_model or f"{self.worker_provider_id}/{self.agent_model}"
        self.worker_api_key = (get_setting_or_env("WORKER_API_KEY") or "dummy").strip() or "dummy"

    @staticmethod
    def _parse_worker_output(raw_output: str, worker_type: str) -> tuple[str, Optional[str]]:
        """
        Parse worker stdout and return (text_for_assessment, session_id_or_none).
        Behavior is worker-specific; currently only opencode format is implemented.
        """
        if worker_type != "opencode":
            return (raw_output or "").strip(), None
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
    def _read_task_plan(project_path: Optional[str], ticket_id: Optional[uuid.UUID]) -> str:
        """Read plan from plan/<ticket_id>_task_plan.md. Raises ValueError if ticket_id is None. Returns empty string if file missing or unreadable."""
        if ticket_id is None:
            raise ValueError("ticket_id is required to read task plan")
        path = _get_task_plan_path(project_path, ticket_id)
        if not os.path.isfile(path):
            return ""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _generate_commit_message(self, project_path: Optional[str], fallback: str) -> str:
        """Ask the LLM for a one-line imperative commit message based on current diff. Returns fallback on failure or empty diff."""
        if not project_path or not os.path.isdir(project_path):
            return fallback
        try:
            r1 = subprocess.run(
                ["git", "diff", "--no-color"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            r2 = subprocess.run(
                ["git", "diff", "--cached", "--no-color"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            diff = ((r1.stdout or "") + "\n" + (r2.stdout or "")).strip()
            if not diff or len(diff) > 6000:
                diff = diff[:6000] + "\n... (truncated)" if len(diff) > 6000 else diff
            if not diff:
                return fallback
            headers = {"Content-Type": "application/json"}
            if self.agent_api_key:
                headers["Authorization"] = f"Bearer {self.agent_api_key}"
            resp = requests.post(
                self.agent_api_url,
                json={
                    "model": self.agent_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You generate a single-line commit message in imperative mood (e.g. 'Add user login', 'Fix null check in parser'). Output only the message, no quotes, no explanation.",
                        },
                        {"role": "user", "content": "Generate a commit message for these changes:\n\n" + diff},
                    ],
                    "max_tokens": 80,
                    "temperature": 0.2,
                },
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
            if not content:
                return fallback
            first_line = content.split("\n")[0].strip()
            return first_line[:200] if first_line else fallback
        except Exception:
            return fallback

    @staticmethod
    def _commit_if_changes(project_path: Optional[str], message: str) -> None:
        """If there are staged or unstaged changes, add all and commit with message. No push."""
        if not project_path or not os.path.isdir(project_path):
            return
        if not (message or "").strip():
            return
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
                msg = message.strip()[:200]
                subprocess.run(
                    ["git", "commit", "-m", msg],
                    cwd=project_path,
                    capture_output=True,
                    timeout=10,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
            pass

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

    def _run_execution_loop(
        self,
        ticket: Ticket,
        session_id: str,
        context: dict,
        prompt_history: List[str],
        conversation_history: List[str],
        director_messages: List[Dict[str, str]],
        approved_plan_text: str,
        start_memory_passages: List[str],
        base_save_dir: Optional[str],
        memory_kwargs: dict,
        project_path: str,
        setup_ticket: bool = False,
        flow_label: Optional[str] = None,
    ) -> Optional[str]:
        """Run the execution loop until the agent marks the ticket complete. Returns completion_summary or None.
        flow_label: optional prefix for logs (e.g. 'Setup' or 'PR review') so shared logs are unambiguous."""
        ticket_id = ticket.id
        prefix = f"[{flow_label}] " if flow_label else ""
        completion_summary: Optional[str] = None
        max_turns = 1000
        for turn in range(max_turns):
            self._debug_log(f"{prefix}Execution turn {turn + 1}")
            if _active_sessions.get(ticket.id, {}).get("cancel"):
                self._log(
                    ticket.project_id,
                    ticket_id,
                    session_id,
                    "cancelled",
                    f"Execution cancelled by user during turn {turn}",
                )
                return None
            latest_output = conversation_history[-1] if conversation_history else ""
            last_prompt = prompt_history[-1] if prompt_history else ""
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
                phase="execution",
                approved_plan_text=approved_plan_text,
                setup_ticket=setup_ticket,
            )
            # Never treat as complete on turn 0: conversation so far is only research/planning. We must send at least one execution prompt so the worker actually implements the plan.
            is_first_execution_turn = turn == 0
            if agent_response.get("complete") and not is_first_execution_turn:
                self._debug_log(f"{prefix}Task complete")
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
                return completion_summary
            next_prompt = agent_response.get("next_prompt")
            if is_first_execution_turn and (not next_prompt or agent_response.get("complete")):
                next_prompt = (
                    "Implement the approved plan above. Start with the first step. "
                    "Do not report complete until you have made the required code changes (tests and implementation)."
                )
            if not next_prompt:
                raise AgentAPIError("Agent API returned no next_prompt when task is incomplete")
            if "assess: is the ticket complete" not in next_prompt.lower():
                if "one file at a time" not in next_prompt.lower() and "slowly" not in next_prompt.lower():
                    next_prompt = "Work VERY slowly: modify one file at a time, verify each change before proceeding.\n\n" + next_prompt
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                f"worker_turn_{turn + 1}_prompt",
                f"Director prompt (turn {turn + 1})",
                raw_output=next_prompt,
            )
            self._trace_log(session_id, f"[Director -> Worker] {prefix}Execution turn {turn + 1}:\n{next_prompt}", project_path)
            self._debug_log(f"[Director -> Worker] {prefix}Execution turn {turn + 1}:\n" + (next_prompt[:800] + "..." if len(next_prompt) > 800 else next_prompt))
            response = self._send_to_worker(next_prompt, session_id, project_path, resume=True)
            exec_out = response.get("output") or ""
            prompt_history.append(next_prompt)
            conversation_history.append(exec_out)
            self._trace_log(
                session_id,
                f"[Worker -> Director] {prefix}Execution turn {turn + 1} response (return_code={response.get('return_code')}):\n{exec_out}\n--- stderr:\n{response.get('error') or ''}",
                project_path,
            )
            self._debug_log(f"[Worker -> Director] {prefix}Execution turn {turn + 1} response:\n" + (exec_out[:800] + "..." if len(exec_out) > 800 else exec_out))
            self._log(
                ticket.project_id,
                ticket_id,
                session_id,
                f"worker_turn_{turn + 1}",
                f"Turn {turn + 1} completed",
                raw_output=response.get("output"),
            )
            commit_msg = self._generate_commit_message(project_path, f"Agent: step {turn + 1}")
            self._commit_if_changes(project_path, commit_msg)
        return completion_summary

    def _run_setup_ticket_flow(
        self,
        ticket: Ticket,
        session_id: str,
        context: dict,
        project_path: str,
        base_save_dir: Optional[str],
        memory_kwargs: dict,
        start_memory_passages: List[str],
        context_json: str,
    ) -> Optional[str]:
        """Run the execution-only flow for the Project setup ticket (no research, no plan, no tests required). Returns completion_summary."""
        ticket_id = ticket.id
        self._log(
            ticket.project_id, ticket_id, session_id,
            "project_setup_flow",
            "Project setup ticket: execution-only flow (no research/plan, no tests required)",
        )
        setup_instruction = (
            "This is the Project setup ticket. Do exactly what the description says: create folder structure and configuration only (e.g. .gitignore). "
            "Do not write application code. Output what you did.\n\n"
            f"Ticket: {ticket.title}\n\n"
            f"Description:\n{(ticket.description or '').strip()}\n\n"
            + context_json
        )
        self._trace_log(session_id, f"[Director -> Worker] Project setup (single turn):\n{setup_instruction}", project_path)
        self._log(
            ticket.project_id, ticket_id, session_id, "worker_setup_prompt",
            "Project setup prompt sent to worker", raw_output=setup_instruction,
        )
        response = self._send_to_worker(setup_instruction, session_id, project_path, resume=False)
        worker_out = response.get("output") or ""
        prompt_history = [setup_instruction]
        conversation_history = [worker_out]
        self._trace_log(session_id, f"[Worker -> Director] Project setup response:\n{worker_out}", project_path)
        self._log(
            ticket.project_id, ticket_id, session_id, "worker_setup_done",
            "Project setup turn completed", raw_output=response.get("output"),
        )
        return self._run_execution_loop(
            ticket=ticket,
            session_id=session_id,
            context=context,
            prompt_history=prompt_history,
            conversation_history=conversation_history,
            director_messages=[],
            approved_plan_text="",
            start_memory_passages=start_memory_passages,
            base_save_dir=base_save_dir,
            memory_kwargs=memory_kwargs,
            project_path=project_path,
            setup_ticket=True,
            flow_label="Setup",
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
        self._log(ticket.project_id, ticket_id, session_id, "session_started", f"Started worker session {session_id}")
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

            # Worker context (same for all phases; worker session is never reset)
            worker_context = {
                "project_name": context.get("project_name"),
                "project_path": context.get("project_path"),
                "current_ticket": context.get("current_ticket"),
                "graph_relevant_to_current_ticket": context.get("graph_relevant_to_current_ticket"),
            }
            context_json = "\nContext:\n" + json.dumps(worker_context, indent=2)
            start_query = f"{ticket.title}. {(ticket.description or '').strip()}".strip()
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

            # Match "Project setup" case-insensitively so edited or legacy tickets still get the light flow
            _title = (ticket.title or "").strip()
            is_setup_ticket = _title.lower() == PROJECT_SETUP_TICKET_TITLE.lower()
            self._debug_log(f"Ticket title={_title!r}, is_setup_ticket={is_setup_ticket}")
            if is_setup_ticket:
                self._debug_log("Flow: Setup (execution-only, no research/plan)")
                completion_summary = self._run_setup_ticket_flow(
                    ticket=ticket,
                    session_id=session_id,
                    context=context,
                    project_path=project_path,
                    base_save_dir=base_save_dir,
                    memory_kwargs=memory_kwargs,
                    start_memory_passages=start_memory_passages,
                    context_json=context_json,
                )
            else:
                # --- Normal flow: research → plan → plan-review → execution ---
                self._debug_log("Flow: Normal (research → plan → plan-review → execution)")
                # --- Phase: Research (one worker turn) ---
                self._debug_log("Phase: Research (1 worker turn)")
                research_instruction = get_worker_research_prompt_prefix() + context_json
                self._trace_log(session_id, f"[Director -> Worker] Research:\n{research_instruction}", project_path)
                self._debug_log("[Director -> Worker] Research prompt:\n" + (research_instruction[:800] + "..." if len(research_instruction) > 800 else research_instruction))
                self._log(
                    ticket.project_id, ticket_id, session_id, "worker_research_prompt",
                    "Research prompt sent to worker", raw_output=research_instruction,
                )
                response = self._send_to_worker(research_instruction, session_id, project_path, resume=False)
                worker_out = response.get("output") or ""
                prompt_history = [research_instruction]
                conversation_history = [worker_out]
                self._trace_log(session_id, f"[Worker -> Director] Research response:\n{worker_out}", project_path)
                self._debug_log("[Worker -> Director] Research response:\n" + (worker_out[:800] + "..." if len(worker_out) > 800 else worker_out))
                self._log(
                    ticket.project_id, ticket_id, session_id, "worker_research_done",
                    "Research turn completed", raw_output=response.get("output"),
                )
                self._debug_log("Phase: Planning (1 worker turn)")

                # --- Phase: Planning (one worker turn) ---
                if _active_sessions.get(ticket.id, {}).get("cancel"):
                    self._log(ticket.project_id, ticket_id, session_id, "cancelled", "Execution cancelled before planning")
                    return
                plan_path = _get_task_plan_path(project_path, ticket_id)
                plan_instruction = get_worker_plan_prompt_prefix(task_plan_path=plan_path) + context_json
                self._trace_log(session_id, f"[Director -> Worker] Planning:\n{plan_instruction}", project_path)
                self._debug_log("[Director -> Worker] Plan prompt:\n" + (plan_instruction[:800] + "..." if len(plan_instruction) > 800 else plan_instruction))
                self._log(
                    ticket.project_id, ticket_id, session_id, "worker_plan_prompt",
                    "Plan prompt sent to worker", raw_output=plan_instruction,
                )
                response = self._send_to_worker(plan_instruction, session_id, project_path, resume=True)
                plan_out = response.get("output") or ""
                prompt_history.append(plan_instruction)
                conversation_history.append(plan_out)
                self._trace_log(session_id, f"[Worker -> Director] Plan response:\n{plan_out}", project_path)
                self._debug_log("[Worker -> Director] Plan response:\n" + (plan_out[:800] + "..." if len(plan_out) > 800 else plan_out))
                self._log(
                    ticket.project_id, ticket_id, session_id, "worker_plan_done",
                    "Plan turn completed", raw_output=response.get("output"),
                )
                self._debug_log("Phase: Plan-review (agent judges plan; loop until approved)")

                # --- Phase: Plan-review loop ---
                director_messages_plan = []
                approved_plan_text = ""
                max_plan_review_turns = 50
                for plan_turn in range(max_plan_review_turns):
                    self._debug_log(f"Plan-review turn {plan_turn + 1}")
                    if _active_sessions.get(ticket.id, {}).get("cancel"):
                        self._log(ticket.project_id, ticket_id, session_id, "cancelled", "Execution cancelled during plan review")
                        return
                    latest_output = conversation_history[-1] if conversation_history else ""
                    last_prompt = prompt_history[-1] if prompt_history else ""
                    combined_query = f"{last_prompt[:500]}\n{latest_output[:500]}".strip()
                    turn_memory_passages = self._retrieve_memory_passages(
                        ticket=ticket,
                        queries=[combined_query],
                        base_save_dir=base_save_dir,
                        memory_kwargs=memory_kwargs,
                        session_id=session_id,
                        ticket_id=ticket_id,
                        step_name=f"memory_retrieve_plan_review_{plan_turn}",
                    )
                    memory_passages = list(start_memory_passages)
                    for passage in turn_memory_passages:
                        if passage not in memory_passages:
                            memory_passages.append(passage)
                    memories = self._format_memories(memory_passages)
                    agent_response, director_messages_plan = self._agent_assess(
                        context,
                        prompt_history,
                        conversation_history,
                        memories=memories,
                        director_messages=director_messages_plan,
                        session_id=session_id,
                        project_path=project_path,
                        phase="plan_review",
                    )
                    if agent_response.get("plan_approved"):
                        approved_plan_text = self._read_task_plan(project_path, ticket_id)
                        if not approved_plan_text:
                            # File missing or empty; ask the worker to output the full plan.
                            full_plan_prompt = (
                                "The plan has been approved. Please output the complete plan text (full contents of your execution plan file or your execution plan) "
                                "so it can be saved for the execution phase. Output only the plan, no preamble."
                            )
                            self._trace_log(session_id, f"[Director -> Worker] Request full plan:\n{full_plan_prompt}", project_path)
                            self._debug_log("[Director -> Worker] Request full plan (plan file missing)")
                            response = self._send_to_worker(full_plan_prompt, session_id, project_path, resume=True)
                            full_plan_out = (response.get("output") or "").strip()
                            approved_plan_text = full_plan_out
                            prompt_history.append(full_plan_prompt)
                            conversation_history.append(response.get("output") or "")
                            self._trace_log(session_id, f"[Worker -> Director] Full plan response:\n{full_plan_out}", project_path)
                            self._debug_log("[Worker -> Director] Full plan response:\n" + (full_plan_out[:800] + "..." if len(full_plan_out) > 800 else full_plan_out))
                    if not approved_plan_text:
                        approved_plan_text = (agent_response.get("approved_plan_text") or "").strip() or latest_output[:8000]
                    self._debug_log("Plan approved, entering execution")
                    self._log(ticket.project_id, ticket_id, session_id, "plan_approved", "Plan approved, entering execution")
                    break
                    next_prompt = agent_response.get("next_prompt")
                    if not next_prompt:
                        raise AgentAPIError("Agent API returned no next_prompt during plan review")
                    self._trace_log(session_id, f"[Director -> Worker] Plan-review turn {plan_turn + 1}:\n{next_prompt}", project_path)
                    self._debug_log(f"[Director -> Worker] Plan-review turn {plan_turn + 1}:\n" + (next_prompt[:800] + "..." if len(next_prompt) > 800 else next_prompt))
                    self._log(
                        ticket.project_id, ticket_id, session_id,
                        f"worker_plan_review_{plan_turn + 1}_prompt",
                        f"Plan review feedback (turn {plan_turn + 1})",
                        raw_output=next_prompt,
                    )
                    response = self._send_to_worker(next_prompt, session_id, project_path, resume=True)
                    plan_review_out = response.get("output") or ""
                    prompt_history.append(next_prompt)
                    conversation_history.append(plan_review_out)
                    self._trace_log(session_id, f"[Worker -> Director] Plan-review turn {plan_turn + 1} response:\n{plan_review_out}", project_path)
                    self._debug_log(f"[Worker -> Director] Plan-review turn {plan_turn + 1} response:\n" + (plan_review_out[:800] + "..." if len(plan_review_out) > 800 else plan_review_out))
                    self._log(
                        ticket.project_id, ticket_id, session_id,
                        f"worker_plan_review_{plan_turn + 1}",
                        f"Plan review turn {plan_turn + 1} completed",
                        raw_output=response.get("output"),
                    )

                # If plan was never approved (e.g. max_plan_review_turns exhausted), use plan file as fallback so execution still has a plan to follow.
                if not approved_plan_text:
                    approved_plan_text = self._read_task_plan(project_path, ticket_id)
                if not approved_plan_text:
                    approved_plan_text = (conversation_history[-1][:8000] if conversation_history else "")
                if not approved_plan_text:
                    self._log(
                        ticket.project_id, ticket_id, session_id,
                        "plan_review_exhausted",
                        "Plan review ended without approval and no plan text; proceeding with empty plan context",
                    )

                # Agent context reset: clear planning-phase director messages. Execution phase gets fresh director_messages with plan always injected.
                director_messages = []

                self._debug_log("Phase: Execution (worker follows plan; loop until ticket complete)")
                completion_summary = self._run_execution_loop(
                    ticket=ticket,
                    session_id=session_id,
                    context=context,
                    prompt_history=prompt_history,
                    conversation_history=conversation_history,
                    director_messages=director_messages,
                    approved_plan_text=approved_plan_text,
                    start_memory_passages=start_memory_passages,
                    base_save_dir=base_save_dir,
                    memory_kwargs=memory_kwargs,
                    project_path=project_path,
                    setup_ticket=False,
                    flow_label=None,
                )

            self._debug_log("Finalizing: commit, push, PR")
            # Step 4: Finalize (commit, push, PR, move to In Review)
            self._finalize(
                ticket,
                session_id,
                project_path=project_path,
                completion_summary=completion_summary,
            )
        finally:
            _active_sessions.pop(ticket.id, None)

    def _run_pr_review_flow(
        self,
        ticket: Ticket,
        session_id: str,
        context: dict,
        comment_body: str,
        project_path: str,
        base_save_dir: Optional[str],
        memory_kwargs: dict,
    ) -> Optional[str]:
        """Run the simplified PR review flow: one worker call with review comment, then loop until agent marks complete. Returns completion_summary."""
        ticket_id = ticket.id
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
        start_memory_passages = self._retrieve_memory_passages(
            ticket=ticket,
            queries=[f"PR review: {comment_body[:200]}"],
            base_save_dir=base_save_dir,
            memory_kwargs=memory_kwargs,
            session_id=session_id,
            ticket_id=ticket_id,
            step_name="memory_retrieve_review",
        )
        self._trace_log(session_id, f"Prompt to worker (review turn 0):\n{task_instruction}", project_path)
        self._log(
            ticket.project_id, ticket_id, session_id,
            "worker_turn_0_prompt", "Review prompt sent to worker", raw_output=task_instruction,
        )
        response = self._send_to_worker(task_instruction, session_id, project_path, resume=False)
        self._log(ticket.project_id, ticket_id, session_id, "worker_turn_0", "Review prompt sent", raw_output=response.get("output"))
        conversation_history: List[str] = [response.get("output") or ""]
        prompt_history: List[str] = [task_instruction]
        director_messages: List[Dict[str, str]] = []
        completion_summary: Optional[str] = None
        self._debug_log("[PR review] Loop until agent marks complete")
        max_turns = 50
        for turn in range(max_turns):
            self._debug_log(f"PR review turn {turn + 1}")
            if _active_sessions.get(ticket.id, {}).get("cancel"):
                return completion_summary
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
                self._debug_log("PR review complete")
                completion_summary = agent_response.get("summary", "Addressed review feedback.")
                self._log(ticket.project_id, ticket_id, session_id, "review_complete", completion_summary)
                return completion_summary
            next_prompt = agent_response.get("next_prompt")
            if not next_prompt:
                raise AgentAPIError("Agent API returned no next_prompt when task is incomplete")
            if "assess: is the ticket complete" not in next_prompt.lower():
                if "one file at a time" not in next_prompt.lower() and "slowly" not in next_prompt.lower():
                    next_prompt = "Work VERY slowly: modify one file at a time, verify each change before proceeding.\n\n" + next_prompt
            self._log(
                ticket.project_id, ticket_id, session_id,
                f"worker_turn_{turn + 1}_prompt", f"Director prompt (turn {turn + 1})", raw_output=next_prompt,
            )
            response = self._send_to_worker(next_prompt, session_id, project_path, resume=True)
            prompt_history.append(next_prompt)
            conversation_history.append(response.get("output") or "")
            self._log(ticket.project_id, ticket_id, session_id, f"worker_turn_{turn + 1}", "Turn completed", raw_output=response.get("output"))
        return completion_summary

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
        self._debug_log("Flow: PR review (address comment → loop until complete)")
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
            completion_summary = self._run_pr_review_flow(
                ticket=ticket,
                session_id=session_id,
                context=context,
                comment_body=comment_body,
                project_path=project_path,
                base_save_dir=base_save_dir,
                memory_kwargs=memory_kwargs,
            )
            self._debug_log("Posting reply to PR comment, then finalizing")
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
        """Return (nodes, edges) that are relevant to the given node/edge IDs. Includes edges connecting the nodes.
        Pass node_ids/edge_ids from _expand_all_marker so '*' is already expanded to full id lists."""
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

    @staticmethod
    def _expand_all_marker(nodes: list, edges: list, node_ids: list, edge_ids: list) -> tuple:
        """If node_ids or edge_ids is the 'all' sentinel ['*'], replace with full id lists."""
        _ALL = ["*"]
        nids = list(node_ids or [])
        eids = list(edge_ids or [])
        if nids == _ALL or (len(nids) == 1 and nids[0] == "*"):
            nids = [n.get("id") for n in (nodes or []) if n.get("id") is not None]
        if eids == _ALL or (len(eids) == 1 and eids[0] == "*"):
            eids = [e.get("id") for e in (edges or []) if e.get("id") is not None]
        return nids, eids

    @staticmethod
    def _edges_with_readable_endpoints(nodes: list, edges: list) -> list:
        """Return a copy of edges with source_label and target_label from node data."""
        node_label_by_id = {}
        for n in nodes or []:
            nid = n.get("id")
            if nid is not None:
                data = n.get("data") or {}
                node_label_by_id[nid] = data.get("label") or nid
        out = []
        for e in edges or []:
            copy = dict(e)
            copy["source_label"] = node_label_by_id.get(e.get("source"), e.get("source") or "")
            copy["target_label"] = node_label_by_id.get(e.get("target"), e.get("target") or "")
            out.append(copy)
        return out

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

        # Full architecture graph and the slice relevant to the current ticket (edges include source_label, target_label, label_and_id)
        graph = Graph.query.filter_by(project_id=ticket.project_id).first()
        if graph:
            nodes = graph.nodes if graph.nodes else []
            edges = graph.edges if graph.edges else []
            full_enriched_edges = self._edges_with_readable_endpoints(nodes, edges)
            context["graph"] = {
                "nodes": nodes,
                "edges": full_enriched_edges,
            }
            node_ids, edge_ids = self._expand_all_marker(
                nodes,
                edges,
                ticket.associated_node_ids or [],
                ticket.associated_edge_ids or [],
            )
            rel_nodes, rel_edges = self._relevant_subgraph(nodes, edges, node_ids, edge_ids)
            rel_enriched_edges = self._edges_with_readable_endpoints(rel_nodes, rel_edges)
            for e in rel_enriched_edges:
                e["label_and_id"] = "{} → {}: {}".format(
                    e.get("source_label", ""),
                    e.get("target_label", ""),
                    e.get("id", ""),
                )
            rel_nodes_with_label_and_id = []
            for n in rel_nodes:
                copy = dict(n)
                data = copy.get("data") or {}
                label = data.get("label") or copy.get("id") or ""
                copy["label_and_id"] = "{}: {}".format(label, copy.get("id", ""))
                rel_nodes_with_label_and_id.append(copy)
            context["graph_relevant_to_current_ticket"] = {
                "nodes": rel_nodes_with_label_and_id,
                "edges": rel_enriched_edges,
            }
            # Add "name: id" and "source → target: id" for current_ticket's associated nodes/edges
            node_label_by_id = {
                n.get("id"): (n.get("data") or {}).get("label") or n.get("id")
                for n in nodes
            }
            edge_label_by_id = {
                e.get("id"): "{} → {}".format(e.get("source_label", ""), e.get("target_label", ""))
                for e in full_enriched_edges
            }
            # Use expanded ids so '*' shows as full list of labeled nodes/edges
            exp_node_ids, exp_edge_ids = self._expand_all_marker(
                nodes, edges, ticket.associated_node_ids or [], ticket.associated_edge_ids or []
            )
            context["current_ticket"]["associated_nodes_labeled"] = [
                "{}: {}".format(node_label_by_id.get(nid, nid), nid)
                for nid in exp_node_ids
            ]
            context["current_ticket"]["associated_edges_labeled"] = [
                "{}: {}".format(edge_label_by_id.get(eid, eid), eid)
                for eid in exp_edge_ids
            ]
        else:
            context["graph_relevant_to_current_ticket"] = {"nodes": [], "edges": []}
            context["current_ticket"]["associated_nodes_labeled"] = []
            context["current_ticket"]["associated_edges_labeled"] = []

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

    def _send_to_worker(
        self,
        prompt: str,
        session_id: str,
        project_path: Optional[str] = None,
        resume: bool = False,
    ) -> dict:
        """Send a prompt to the configured worker and get the response. Dispatches by worker_type."""
        if self.worker_type == "opencode":
            return self._send_to_worker_opencode(prompt, session_id, project_path, resume)
        if self.worker_type in ("aider", "claude_code", "gemini", "codex"):
            raise NotImplementedError(f"Worker type {self.worker_type!r} is not implemented yet")
        raise ValueError(f"Unknown worker_type: {self.worker_type!r}")

    def _send_to_worker_opencode(
        self,
        prompt: str,
        session_id: str,
        project_path: Optional[str] = None,
        resume: bool = False,
    ) -> dict:
        """Send a prompt to OpenCode and get the response."""
        worker_session_id = self._worker_sessions.get(session_id)
        cmd = [
            *self.worker_cmd,
            "run",
            "--format",
            "json",
            "--model",
            self.worker_model,
        ]
        if resume:
            if worker_session_id:
                cmd.extend(["--session", worker_session_id])
            else:
                self._debug_log(f"No worker session recorded for {session_id}; starting fresh run.")
                cmd.extend(["--title", f"terarchitect-{session_id}"])
        else:
            cmd.extend(["--title", f"terarchitect-{session_id}"])
        cmd.append(prompt)

        timeout_sec = int(get_setting_or_env("WORKER_TIMEOUT_SEC") or "3600")
        run_kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": timeout_sec,
        }
        if project_path and os.path.isdir(project_path):
            run_kwargs["cwd"] = project_path
            self._debug_log(f"Running worker ({self.worker_type}) from cwd={project_path}")

        env = dict(os.environ)
        # Build worker config from WORKER_* attrs (OpenCode CLI expects OPENCODE_CONFIG_CONTENT).
        local_model_name = self.worker_model
        provider_prefix = f"{self.worker_provider_id}/"
        if local_model_name.startswith(provider_prefix):
            local_model_name = local_model_name[len(provider_prefix) :]
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {
                "model": f"{self.worker_provider_id}/{local_model_name}",
                "provider": {
                    self.worker_provider_id: {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Terarchitect Proxy",
                        "options": {
                            "baseURL": self.worker_llm_url,
                            "apiKey": self.worker_api_key,
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
            self._trace_log(
                session_id,
                f"Running worker ({self.worker_type}) command:\ncmd={' '.join(cmd)}\nrun_kwargs={run_kwargs}",
                project_path,
            )
            result = subprocess.run(cmd, **run_kwargs)
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            parsed_text, discovered_session = self._parse_worker_output(out, self.worker_type)
            if discovered_session:
                self._worker_sessions[session_id] = discovered_session
            if session_id and project_path:
                self._trace_log(
                    session_id,
                    f"Worker subprocess result: return_code={result.returncode} len(stdout)={len(out)} len(stderr)={len(err)}\n"
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
        token_limit: int = DIRECTOR_CONTEXT_TOKEN_LIMIT,
    ) -> List[Dict[str, str]]:
        """If token count of [system, *director_messages, new_user] exceeds limit, summarize oldest chunks until under limit."""
        out = list(director_messages)
        new_user_msg = {"role": "user", "content": new_user_content}
        system_msg = {"role": "system", "content": system_content}
        while True:
            full = [system_msg] + out + [new_user_msg]
            if _count_tokens_for_messages(full) <= token_limit:
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
        phase: Optional[str] = None,
        approved_plan_text: str = "",
        setup_ticket: bool = False,
    ) -> tuple[Dict[str, Any], List[Dict[str, str]]]:
        """Call OpenAI-compatible API to assess completion and generate next prompt. Returns (response_dict, updated director_messages).
        phase: None (default) = normal ticket/PR review; 'plan_review' = judge plan; 'execution' = inject approved_plan_text and assess completion.
        setup_ticket: when True with phase='execution', do not require tests; judge completion against ticket description only (structure/config)."""
        director_messages = director_messages or []
        is_plan_review = phase == "plan_review"
        is_execution = phase == "execution"
        if is_plan_review:
            system_content = get_agent_system_prompt() + "\n\n" + get_agent_plan_review_instructions()
        else:
            system_content = get_agent_system_prompt()
        memory_block = f"{memories}\n\n" if memories else ""
        plan_block = ""
        if is_execution and approved_plan_text:
            plan_block = f"Approved plan (worker must follow this; it is never summarized away):\n\n{approved_plan_text}\n\n---\n\n"
        setup_hint = ""
        if is_execution and setup_ticket:
            setup_hint = "This is the Project setup ticket (structure/config only). Do not require tests; judge completion only against the ticket description (folder structure, .gitignore, minimal config).\n\n"

        if not director_messages:
            turns = []
            for i in range(max(len(prompt_history), len(conversation_history))):
                prompt = prompt_history[i] if i < len(prompt_history) else ""
                response = conversation_history[i] if i < len(conversation_history) else ""
                turns.append(
                    f"### Turn {i + 1} - Prompt to Worker:\n{prompt}\n\n"
                    f"### Turn {i + 1} - Worker response:\n{response}"
                )
            full_conversation = "\n\n---\n\n".join(turns)
            if is_plan_review:
                convo_for_review = full_conversation
                convo_token_count = _count_tokens_for_messages([{"role": "user", "content": full_conversation}])
                if convo_token_count > PLAN_REVIEW_INITIAL_FULL_CONVERSATION_TOKEN_LIMIT:
                    # First plan-review turn can be huge; summarize earlier planning turns and keep recent raw turns.
                    summary = self._summarize_director_messages(
                        [{"role": "user", "content": "Planning conversation:\n\n" + full_conversation}]
                    )
                    recent_raw_turns = "\n\n---\n\n".join(turns[-2:]) if turns else ""
                    convo_for_review = (
                        "Planning conversation (summarized):\n"
                        + summary
                        + (
                            ("\n\nRecent raw planning turns:\n" + recent_raw_turns)
                            if recent_raw_turns
                            else ""
                        )
                    )
                user_msg_content = f"""Context:
{json.dumps(context, indent=2)}

{memory_block}Conversation for plan review:
{convo_for_review}

Judge the plan. Respond in JSON only: plan_approved (true/false). If true, include approved_plan_text as a concise execution checklist for the next phase (not a verbatim full-file dump). If false, include next_prompt with concise, actionable fixes (no code fences)."""
            else:
                user_msg_content = f"""{setup_hint}{plan_block}Context:
{json.dumps(context, indent=2)}

{memory_block}Full conversation with Worker:
{full_conversation}

Assess: Is the ticket complete? Respond in JSON only."""
        else:
            n = max(len(prompt_history), len(conversation_history))
            prompt = prompt_history[n - 1] if n and n <= len(prompt_history) else ""
            response = conversation_history[n - 1] if n and n <= len(conversation_history) else ""
            if is_plan_review:
                user_msg_content = f"""{memory_block}New worker turn:

### Turn {n} - Prompt to Worker:
{prompt}

### Turn {n} - Worker response:
{response}

Judge the plan. Respond in JSON only: plan_approved (true/false). If true, include approved_plan_text as a concise execution checklist. If false, include next_prompt with concise actionable fixes (no code fences)."""
            else:
                user_msg_content = f"""{setup_hint}{plan_block}{memory_block}New worker turn:

### Turn {n} - Prompt to Worker:
{prompt}

### Turn {n} - Worker response:
{response}

Assess: Is the ticket complete? Respond in JSON only."""

        new_user_msg = {"role": "user", "content": user_msg_content}
        token_limit = (
            DIRECTOR_CONTEXT_TOKEN_LIMIT_PLAN_REVIEW
            if is_plan_review
            else DIRECTOR_CONTEXT_TOKEN_LIMIT
        )
        compacted = self._compact_director_messages(
            director_messages,
            user_msg_content,
            system_content,
            token_limit=token_limit,
        )
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
        # Try raw parse first (LLM may return bare JSON).
        parsed = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            pass
        if parsed is None and "```" in content:
            # Extract JSON from markdown: prefer ```json ... ```; do not use first ``` (LLM may output other code blocks first).
            extract = content
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.rfind("```")
                if end > start:
                    extract = content[start:end].strip()
                else:
                    extract = content[start:].strip()
            else:
                start = content.find("```") + 3
                if start < len(content) and content[start : start + 4] == "json":
                    start += 4
                end = content.find("```", start)
                extract = content[start:end].strip() if end > 0 else content[start:].strip()
            try:
                parsed = json.loads(extract)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                content = extract
        if parsed is None:
            raise AgentAPIError(
                f"Agent API response is not valid JSON: {content[:200]}...",
                cause=None,
            )

        if not isinstance(parsed, dict):
            raise AgentAPIError(f"Agent API response must be a JSON object, got: {type(parsed)}")

        response_dict: Dict[str, Any] = {
            "complete": parsed.get("complete", False),
            "summary": parsed.get("summary", ""),
            "next_prompt": parsed.get("next_prompt", ""),
        }
        if is_plan_review:
            raw_approved = parsed.get("plan_approved", False)
            response_dict["plan_approved"] = raw_approved is True or (isinstance(raw_approved, str) and raw_approved.strip().lower() == "true")
            response_dict["approved_plan_text"] = parsed.get("approved_plan_text", "") or ""
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
        """Commit, push. If review_mode: post pr_comment_body (direct reply to reviewer) as PR comment. Else: create PR, move ticket to In Review."""
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
                gh_env = {**os.environ, **get_gh_env_for_agent()}
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=project_path,
                    capture_output=True,
                    timeout=10,
                    env=gh_env,
                )
                r = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=gh_env,
                )
                if (r.stdout or "").strip():
                    subprocess.run(
                        ["git", "commit", "-m", commit_message],
                        cwd=project_path,
                        capture_output=True,
                        timeout=10,
                        env=gh_env,
                    )
                subprocess.run(
                    ["git", "push", "-u", "origin", branch_name],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=gh_env,
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
                        env=gh_env,
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
                        env=gh_env,
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
