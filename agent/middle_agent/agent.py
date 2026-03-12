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
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Protocol

from utils.app_settings import get_gh_env_for_agent, get_setting_or_env

# Invisible HTML comment appended to all agent-posted PR comments.
# Must match the constant in backend/api/routes.py — used by the PR polling loop
# to distinguish agent replies from human reviewer comments when both share the same token.
BOT_COMMENT_SIGNATURE = "<!-- terarchitect-bot -->"


class TicketLike(Protocol):
    """Minimal ticket interface for Director (no DB dependency). Used by process_ticket and process_ticket_review."""

    id: Any
    project_id: Any
    title: str
    description: Optional[str]
    priority: Optional[str]
    column_id: Optional[str]
    status: Optional[str]
    associated_node_ids: Optional[List[str]]

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
    # Hard constraints for the Director: enforce respectful tone and avoid over-verification.
    guidelines = (
        "\n\nGuidelines (must follow):\n"
        "- When the worker reports that they have created or updated files and their summary is specific and consistent,\n"
        "  generally accept that as true; you do NOT need to see full file contents unless something is clearly inconsistent\n"
        "  with earlier context or would change a critical behavior.\n"
        "- Focus your judgment on whether the described changes fit the ticket requirements and the overall project\n"
        "  architecture, not on micromanaging each keystroke.\n"
        "- Prefer targeted clarifying questions over demanding full-file verbatim pastes; ask for diffs or snippets only\n"
        "  when they are truly necessary to resolve ambiguity.\n"
    )
    if not style:
        return base + guidelines
    return (
        base
        + guidelines
        + "\n\n---\nCommunication style (use this tone when directing the worker; draw from these examples):\n\n"
        + style
    )


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
        "Create a file at {task_plan_path} with a detailed step-by-step execution plan for the ticket. Use a test-driven development (TDD) approach: for each change, plan to write or update a failing test first (unit or integration as appropriate), then implement the minimum code to pass it, then refactor if needed. For integration tests: use test data only; if the project has no test data, plan to create or add it (fixtures, seed data, test DB) or download/generate sample data that is sufficient to verify the important situations and scenarios we care about. Plan to start services with docker compose up -d (or docker compose run), run the integration test suite, then docker compose down when the repo has docker-compose. When the project has UI/E2E tests or the ticket touches the frontend, plan to run (and if needed add or update) UI automated tests (e.g. Playwright, Cypress). Include: order of work, which files to touch, which unit and integration tests to add or update (and when), and any dependencies between steps. Do not implement yet.",
    )
    if task_plan_path and "{task_plan_path}" in raw:
        return raw.format(task_plan_path=task_plan_path)
    return raw


def get_agent_plan_review_instructions() -> str:
    return _get_optional_prompt(
        "agent_plan_review_instructions",
        "You are in plan-review mode. Evaluate the plan for consistency, concrete steps, achievability, and logical ordering.\n"
        "Be constructive and concise. Never use hostile language or profanity. Assume the worker's description of their\n"
        "own work is accurate unless it clearly contradicts earlier context; you are not required to see full file contents\n"
        "to believe that work was done. Prefer targeted feedback (max 3 concrete fixes) and keep next_prompt under 180 words\n"
        "with no markdown code fences. If the plan is solid, respond with JSON: {\"plan_approved\": true, \"approved_plan_text\":\n"
        "\"<concise approved execution checklist>\"}. If not, respond with {\"plan_approved\": false, \"next_prompt\": \"<concise\n"
        "feedback and exact fixes>\"}. approved_plan_text should be a concise execution checklist, not a verbatim file dump.",
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


def _director_prompt_is_stuck(prompt_history: list, next_prompt: str, threshold: int = 3) -> bool:
    """Return True if the last `threshold` prompts are all identical to `next_prompt`.
    Used to detect director feedback loops where the worker keeps ignoring instructions."""
    if len(prompt_history) < threshold:
        return False
    last_n = prompt_history[-threshold:]
    # Strip the "Work VERY slowly" prefix we add so we compare the real content.
    def _core(p: str) -> str:
        prefix = "Work VERY slowly: modify one file at a time, verify each change before proceeding.\n\n"
        return p[len(prefix):] if p.startswith(prefix) else p
    core_next = _core(next_prompt)
    return all(_core(p) == core_next for p in last_n)

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


class WorkerUnavailableError(Exception):
    """Raised when the worker (OpenCode) cannot be reached. Fail fast — no point continuing without the worker."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class MiddleAgent:
    """Agent that orchestrates OpenCode (HTTP API) for implementation tasks."""

    def __init__(self, backend: Any):
        """backend: AgentBackend (e.g. HttpAgentBackend). Required; no in-process default."""
        self._backend = backend

        # OpenCode: session id and turn count (summarize every 30 turns).
        self._worker_sessions: Dict[str, str] = {}
        self._worker_turn_count: Dict[str, int] = {}
        self._opencode_server_url: str = (
            (get_setting_or_env("OPENCODE_SERVER_URL") or "http://127.0.0.1:4096").strip().rstrip("/")
        )
        # Optional HTTP basic auth (per opencode.ai/docs/server: OPENCODE_SERVER_PASSWORD, OPENCODE_SERVER_USERNAME).
        _oc_user = (get_setting_or_env("OPENCODE_SERVER_USERNAME") or "opencode").strip()
        _oc_pass = (get_setting_or_env("OPENCODE_SERVER_PASSWORD") or "").strip()
        self._opencode_auth: Optional[tuple] = (_oc_user, _oc_pass) if _oc_pass else None
        # Verbose debug logs (stderr + trace file) default on; set MIDDLE_AGENT_DEBUG=0 to disable.
        self.debug = (get_setting_or_env("MIDDLE_AGENT_DEBUG") or "1").lower() not in ("0", "false", "no", "off")

        # Director API (LLM used to assess completion and decide next prompts).
        # DIRECTOR_LLM_URL can be omitted for known providers — it will be inferred from DIRECTOR_PROVIDER.
        self.director_provider = (get_setting_or_env("DIRECTOR_PROVIDER") or "custom").strip().lower()

        # Well-known provider base URLs (value is the chat-completions endpoint).
        _KNOWN_PROVIDER_URLS: dict[str, str] = {
            "openai": "https://api.openai.com/v1/chat/completions",
            "anthropic": "https://api.anthropic.com/v1/messages",
            "google": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "together": "https://api.together.xyz/v1/chat/completions",
            "togetherai": "https://api.together.xyz/v1/chat/completions",
            "mistral": "https://api.mistral.ai/v1/chat/completions",
            "perplexity": "https://api.perplexity.ai/chat/completions",
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "xai": "https://api.x.ai/v1/chat/completions",
            "fireworks": "https://api.fireworks.ai/inference/v1/chat/completions",
        }

        vllm_base = (get_setting_or_env("DIRECTOR_LLM_URL") or "").strip().rstrip("/")
        if vllm_base:
            self.director_api_url = f"{vllm_base}/v1/chat/completions"
        elif self.director_provider in _KNOWN_PROVIDER_URLS:
            self.director_api_url = _KNOWN_PROVIDER_URLS[self.director_provider]
        else:
            self.director_api_url = ""
        self.director_model = (get_setting_or_env("DIRECTOR_MODEL") or "").strip()
        self.director_api_key = (get_setting_or_env("DIRECTOR_API_KEY") or "").strip() or None

        # Worker mode: "claude-code" (default) or "opencode" (OpenCode CLI with HTTP LLM server).
        raw_worker_mode = (get_setting_or_env("WORKER_MODE") or "claude-code").strip().lower()
        self.worker_mode: str = raw_worker_mode if raw_worker_mode in ("opencode", "claude-code") else "claude-code"

        # OpenCode worker: model, LLM URL. No default URL — WORKER_LLM_URL must be configured.
        self.worker_provider_id = "terarchitect-proxy"
        worker_llm_url = (get_setting_or_env("WORKER_LLM_URL") or "").strip()
        self.worker_llm_url = worker_llm_url.rstrip("/") if worker_llm_url else ""
        raw_worker_model = (get_setting_or_env("WORKER_MODEL") or "").strip()
        self.worker_model = raw_worker_model  # no default — must be explicitly configured
        self.worker_api_key = (get_setting_or_env("WORKER_API_KEY") or "").strip() or None
        self.worker_timeout_sec: int = int(get_setting_or_env("WORKER_TIMEOUT_SEC") or "3600")
        # Extra tools appended to the claude-code --allowedTools list.
        # Comma-separated; supports mcp__ tool names e.g. "mcp__brave__search,mcp__github__create_issue"
        raw_extra = (get_setting_or_env("CLAUDE_CODE_EXTRA_TOOLS") or "").strip()
        self.worker_extra_tools: list[str] = [t.strip() for t in raw_extra.split(",") if t.strip()]

        # Active ticket context for intra-turn logging (OpenCode streaming). Set per process_ticket call.
        self._active_project_id: Optional[uuid.UUID] = None
        self._active_ticket_id: Optional[uuid.UUID] = None

    def _env_has_container_url(self, key: str) -> bool:
        """True if env has key with host.docker.internal (coordinator set container-safe URL; don't overwrite with backend localhost)."""
        return "host.docker.internal" in (os.environ.get(key) or "")

    def _reapply_container_urls_from_env(self) -> None:
        """When running in Docker, env has host.docker.internal URLs. Ensure we keep those container-safe URLs."""
        vllm = (os.environ.get("DIRECTOR_LLM_URL") or "").strip().rstrip("/")
        if vllm and "host.docker.internal" in vllm:
            self.director_api_url = f"{vllm}/v1/chat/completions"
        worker = (os.environ.get("WORKER_LLM_URL") or "").strip().rstrip("/")
        if worker and "host.docker.internal" in worker:
            self.worker_llm_url = worker if worker.endswith("/v1") else f"{worker}/v1"

    def _validate_config(self, project_id: uuid.UUID, ticket_id: uuid.UUID, session_id: str) -> bool:
        """Fail fast with a clear log message if required env (Director/Worker URLs and keys) is missing. Returns True if valid."""
        errors = []
        if ":8000" in (self.director_api_url or ""):
            self._debug_log(
                "DIRECTOR_LLM_URL points to port 8000 (old vLLM default). "
                "Set DIRECTOR_LLM_URL to your Ollama URL (e.g. http://localhost:11434) in coordinator/.env and restart."
            )
        if not self.director_api_url:
            errors.append("DIRECTOR_LLM_URL is not set — Director LLM has no URL. Set DIRECTOR_PROVIDER to a known provider (openai, anthropic, groq, etc.) or provide DIRECTOR_LLM_URL explicitly.")
        if not self.director_model:
            errors.append("DIRECTOR_MODEL is not set — Director LLM has no model to use.")
        if self.worker_mode == "claude-code":
            if not self.worker_api_key:
                errors.append("WORKER_API_KEY (Anthropic) is not set — required for Claude Code mode.")
        else:
            if not self.worker_llm_url:
                errors.append("WORKER_LLM_URL is not set — Worker LLM has no URL to connect to.")
            if not self.worker_model:
                errors.append("WORKER_MODEL is not set — required for OpenCode mode.")
            if not self.worker_api_key:
                errors.append("WORKER_API_KEY is not set — required for OpenCode mode (use 'dummy' for local LLMs that skip auth).")
        if errors:
            msg = "Director/Worker misconfigured — cannot start:\n" + "\n".join(f"  • {e}" for e in errors)
            self._debug_log(msg)
            self._log(project_id, ticket_id, session_id, "misconfigured", msg)
            return False
        return True

    # Models that use the newer /v1/responses API instead of /v1/chat/completions.
    _RESPONSES_API_MODELS = {"gpt-5", "o3", "o4-mini", "o3-mini"}

    def _director_request(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout: int = 300,
        json_mode: bool = False,
    ) -> str:
        """POST to the Director LLM and return the text content.

        Automatically uses the /v1/responses endpoint (with `input`) for models
        like gpt-5 that have dropped /v1/chat/completions, and falls back to the
        standard /v1/chat/completions (with `messages`) for everything else.

        json_mode=True enforces structured JSON output via the appropriate API field.
        """
        headers = {"Content-Type": "application/json"}
        if self.director_api_key:
            headers["Authorization"] = f"Bearer {self.director_api_key}"

        use_responses_api = (
            self.director_model in self._RESPONSES_API_MODELS
            or self.director_api_url.rstrip("/").endswith("/v1/responses")
        )

        if use_responses_api:
            # /v1/responses style: system message becomes a top-level `instructions` field.
            instructions = next(
                (m["content"] for m in messages if m.get("role") == "system"), ""
            )
            input_messages = [m for m in messages if m.get("role") != "system"]
            url = self.director_api_url.replace("/v1/chat/completions", "/v1/responses")
            if not url.rstrip("/").endswith("/v1/responses"):
                url = self.director_api_url.rsplit("/", 1)[0].rstrip("/") + "/responses"
            payload: dict = {
                "model": self.director_model,
                "input": input_messages,
                "max_output_tokens": max_tokens,
            }
            if instructions:
                payload["instructions"] = instructions
            if json_mode:
                payload["text"] = {"format": {"type": "json_object"}}
        else:
            url = self.director_api_url
            payload = {
                "model": self.director_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AgentAPIError(
                f"Director API request failed: {url} - {e}",
                cause=e,
            ) from e

        try:
            data = resp.json()
            if use_responses_api:
                # /v1/responses: output is a list of content blocks.
                # Structure: { output: [ { type: "message", content: [ { type: "output_text", text: "..." } ] } ] }

                # Check for incomplete responses (e.g. max_output_tokens hit) before parsing output.
                status = data.get("status", "")
                if status == "incomplete":
                    reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
                    raise AgentAPIError(
                        f"Director API response was incomplete (reason: {reason}). "
                        f"Increase max_output_tokens or reduce context size.",
                        cause=None,
                    )

                output_items = data.get("output") or []
                for item in output_items:
                    item_type = item.get("type")
                    if item_type == "message":
                        for block in item.get("content") or []:
                            if block.get("type") == "output_text":
                                text = block.get("text", "")
                                if isinstance(text, str):
                                    return text
                    elif item_type == "text":
                        text = item.get("text", "")
                        if isinstance(text, str) and text:
                            return text
                # Fallback: check top-level text or output_text fields.
                for key in ("output_text", "text"):
                    val = data.get(key)
                    if isinstance(val, str) and val:
                        return val
                # Nothing found — raise with full response for debugging.
                import json as _json
                raise AgentAPIError(
                    f"Director API (/v1/responses) returned unrecognised structure. Keys: {list(data.keys())}. "
                    f"Full response (truncated): {_json.dumps(data)[:800]}",
                    cause=None,
                )
            else:
                content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
                return content if isinstance(content, str) else str(content)
        except AgentAPIError:
            raise
        except (KeyError, IndexError, TypeError) as e:
            raise AgentAPIError(
                f"Director API returned invalid response format: {e}",
                cause=e,
            ) from e

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
            content = self._director_request(
                messages=[
                    {
                        "role": "system",
                        "content": "You generate a single-line commit message in imperative mood (e.g. 'Add user login', 'Fix null check in parser'). Output only the message, no quotes, no explanation.",
                    },
                    {"role": "user", "content": "Generate a commit message for these changes:\n\n" + diff},
                ],
                max_tokens=80,
                temperature=0.2,
                timeout=30,
            ).strip()
            if not content:
                return fallback
            first_line = content.split("\n")[0].strip()
            return first_line[:200] if first_line else fallback
        except Exception as e:
            self._debug_log(f"Commit message generation failed: {e}")
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
        except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
            print(f"[MIDDLE_AGENT] _commit_if_changes failed: {e}", file=sys.stderr)

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
        ticket: Any,
        queries: List[str],
        base_save_dir: Optional[str],
        memory_kwargs: Dict[str, str],
        session_id: str,
        ticket_id: uuid.UUID,
        step_name: str,
    ) -> List[str]:
        try:
            results = self._backend.retrieve_memory(ticket.project_id, queries, num_to_retrieve=5)
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
        ticket: Any,
        summary: str,
        base_save_dir: Optional[str],
        memory_kwargs: Dict[str, str],
        session_id: str,
        ticket_id: uuid.UUID,
    ) -> None:
        summary_text = (summary or "").strip()
        if not summary_text:
            return
        try:
            desc = (getattr(ticket, "description", None) or "").strip()
            title = getattr(ticket, "title", None) or ""
            if desc:
                doc = f"Ticket: {title}. {desc}. {summary_text}"
            else:
                doc = f"Ticket: {title}. {summary_text}"
            self._backend.index_memory(ticket.project_id, [doc])
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
        ticket: TicketLike,
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
            if self._backend.cancel_requested(ticket.project_id, ticket.id):
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
                self._cleanup_after_completion(session_id, project_path, ticket_id)
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
                raise AgentAPIError("Director API returned no next_prompt when task is incomplete")
            if _director_prompt_is_stuck(prompt_history, next_prompt):
                raise AgentAPIError(
                    "Director is stuck: same prompt sent 3 times in a row with no progress. "
                    "Aborting to avoid infinite loop."
                )
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
        ticket: TicketLike,
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

    def process_ticket(
        self,
        ticket_id: uuid.UUID,
        project_path: Optional[str] = None,
        project_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Process a ticket from start to finish. Requires project_id and project_path (caller: standalone runner or container)."""
        self._debug_log(f"process_ticket: {ticket_id}")
        if project_id is None:
            self._debug_log("project_id is required")
            sys.exit(1)
        context = self._backend.get_context(project_id, ticket_id)
        if not context:
            self._debug_log("Could not load context, exiting")
            sys.exit(1)

        class _TicketLike:
            def __init__(self, pid: uuid.UUID, tid: uuid.UUID, ctx: dict):
                self.project_id = pid
                self.id = tid
                cur = ctx.get("current_ticket") or {}
                self.title = cur.get("title") or ""
                self.description = cur.get("description") or ""
        ticket = _TicketLike(project_id, ticket_id, context)

        self._reapply_container_urls_from_env()

        # Store active IDs so _send_to_worker can post intra-turn logs (OpenCode streaming).
        self._active_project_id = project_id
        self._active_ticket_id = ticket_id

        session_id = str(uuid.uuid4())
        self._log(project_id, ticket_id, session_id, "session_started", f"Started worker session {session_id}")
        self._debug_log("Session started, loading context...")

        if not self._validate_config(project_id, ticket_id, session_id):
            sys.exit(1)

        self._log(project_id, ticket_id, session_id, "context_loaded", "Loaded project context and graph")

        # Resolve project_path: from arg (standalone) or from context (Flask has project_path in context)
        if project_path is None:
            project_path = (context.get("project_path") or "").strip() or None
        if not project_path or not os.path.isdir(project_path):
            msg = f"Invalid project_path for ticket: {project_path!r}. Pass a clone path (standalone) or set project path in project config (local execution mode)."
            self._debug_log(msg)
            self._log(project_id, ticket_id, session_id, "invalid_project_path", msg)
            sys.exit(1)

        if self._backend.cancel_requested(project_id, ticket_id):
            self._log(project_id, ticket_id, session_id, "cancelled", "Execution cancelled before first worker turn")
            return

        try:
            completion_summary: Optional[str] = None  # initialized here so _finalize always has a defined value
            base_save_dir = None  # Not used; memory via backend
            memory_kwargs = {}
            branch_name = self._ensure_ticket_branch(ticket, project_path, session_id, ticket_id)
            if branch_name:
                self._log(ticket.project_id, ticket_id, session_id, "branch_created", f"Branch {branch_name} checked out")

            # Worker context (same for all phases; worker session is never reset). project_path is the clone dir (from runner).
            worker_context = {
                "project_name": context.get("project_name"),
                "project_path": project_path,
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
                if self._backend.cancel_requested(ticket.project_id, ticket.id):
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
                    if self._backend.cancel_requested(ticket.project_id, ticket.id):
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
                            # File missing or empty; ask the worker for a concise checklist (not a full-file dump).
                            full_plan_prompt = (
                                "The plan has been approved. Please output a concise execution checklist (bullet points or short steps with file paths), "
                                "not the full plan file contents. Summarize the key steps only so the execution phase can follow them. No preamble."
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
                    else:
                        next_prompt = agent_response.get("next_prompt")
                        if not next_prompt:
                            raise AgentAPIError("Director API returned no next_prompt during plan review")
                        self._trace_log(session_id, f"[Director -> Worker] Plan-review turn {plan_turn + 1}:\n{next_prompt}", project_path)
                        self._debug_log(f"[Director -> Worker] Plan-review turn {plan_turn + 1}:\n" + (next_prompt[:800] + "..." if len(next_prompt) > 800 else next_prompt))
                        self._log(
                            ticket.project_id,
                            ticket_id,
                            session_id,
                            f"worker_plan_review_{plan_turn + 1}_prompt",
                            f"Plan review feedback (turn {plan_turn + 1})",
                            raw_output=next_prompt,
                        )
                        response = self._send_to_worker(next_prompt, session_id, project_path, resume=True)
                        plan_review_out = response.get("output") or ""
                        prompt_history.append(next_prompt)
                        conversation_history.append(plan_review_out)
                        self._trace_log(session_id, f"[Worker -> Director] Plan-review turn {plan_turn + 1} response:\n{plan_review_out}", project_path)
                        self._debug_log(
                            "[Worker -> Director] Plan-review turn {plan_turn_plus_one} response:\n".format(
                                plan_turn_plus_one=plan_turn + 1
                            )
                            + (plan_review_out[:800] + "..." if len(plan_review_out) > 800 else plan_review_out)
                        )
                        self._log(
                            ticket.project_id,
                            ticket_id,
                            session_id,
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
            self._active_project_id = None
            self._active_ticket_id = None

    def _run_pr_review_flow(
        self,
        ticket: TicketLike,
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
            "project_path": project_path,
            "current_ticket": context.get("current_ticket"),
            "graph_relevant_to_current_ticket": context.get("graph_relevant_to_current_ticket"),
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
        self._debug_log(f"[Director -> Worker] PR review turn 0:\n" + (task_instruction[:800] + "..." if len(task_instruction) > 800 else task_instruction))
        response = self._send_to_worker(task_instruction, session_id, project_path, resume=False)
        turn0_out = response.get("output") or ""
        self._debug_log(f"[Worker -> Director] PR review turn 0 response:\n" + (turn0_out[:800] + "..." if len(turn0_out) > 800 else turn0_out))
        self._log(ticket.project_id, ticket_id, session_id, "worker_turn_0", "Review prompt sent", raw_output=turn0_out)
        conversation_history: List[str] = [turn0_out]
        prompt_history: List[str] = [task_instruction]
        director_messages: List[Dict[str, str]] = []
        completion_summary: Optional[str] = None
        self._debug_log("[PR review] Loop until agent marks complete")
        max_turns = 50
        for turn in range(max_turns):
            self._debug_log(f"PR review turn {turn + 1}")
            if self._backend.cancel_requested(ticket.project_id, ticket.id):
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
                raise AgentAPIError("Director API returned no next_prompt when task is incomplete")
            if _director_prompt_is_stuck(prompt_history, next_prompt):
                raise AgentAPIError(
                    "Director is stuck: same prompt sent 3 times in a row with no progress. "
                    "Aborting to avoid infinite loop."
                )
            self._log(
                ticket.project_id, ticket_id, session_id,
                f"worker_turn_{turn + 1}_prompt", f"Director prompt (turn {turn + 1})", raw_output=next_prompt,
            )
            self._debug_log(f"[Director -> Worker] PR review turn {turn + 1}:\n" + (next_prompt[:800] + "..." if len(next_prompt) > 800 else next_prompt))
            response = self._send_to_worker(next_prompt, session_id, project_path, resume=True)
            worker_out = response.get("output") or ""
            prompt_history.append(next_prompt)
            conversation_history.append(worker_out)
            self._debug_log(f"[Worker -> Director] PR review turn {turn + 1} response:\n" + (worker_out[:800] + "..." if len(worker_out) > 800 else worker_out))
            self._log(ticket.project_id, ticket_id, session_id, f"worker_turn_{turn + 1}", "Turn completed", raw_output=worker_out)
        return completion_summary

    def process_ticket_review(
        self,
        ticket_id: uuid.UUID,
        comment_body: str,
        pr_number: int,
        project_id: uuid.UUID,
        project_path: str,
    ) -> None:
        """Run the agent to address PR review feedback. Requires project_id and project_path (caller: coordinator/container)."""
        context = self._backend.get_context(project_id, ticket_id)
        if not context:
            self._debug_log("Could not load context for review, exiting")
            sys.exit(1)
        self._reapply_container_urls_from_env()
        context["pr_review_comment"] = comment_body
        class _TicketLike:
            def __init__(self, pid: uuid.UUID, tid: uuid.UUID, ctx: dict):
                self.project_id = pid
                self.id = tid
                cur = ctx.get("current_ticket") or {}
                self.title = cur.get("title") or ""
                self.description = cur.get("description") or ""
        ticket = _TicketLike(project_id, ticket_id, context)
        session_id = str(uuid.uuid4())
        # Store active IDs so _send_to_worker can post intra-turn logs (OpenCode streaming).
        self._active_project_id = project_id
        self._active_ticket_id = ticket_id
        self._log(project_id, ticket_id, session_id, "review_started", "Started PR review feedback session")
        if not self._validate_config(project_id, ticket_id, session_id):
            sys.exit(1)
        self._debug_log("Flow: PR review (address comment → loop until complete)")
        self._log(project_id, ticket_id, session_id, "context_loaded", "Loaded context for PR review")
        if not project_path or not os.path.isdir(project_path):
            self._log(project_id, ticket_id, session_id, "invalid_project_path", "Invalid project_path for review")
            sys.exit(1)
        if self._backend.cancel_requested(project_id, ticket_id):
            return
        if not self._checkout_ticket_branch(ticket, project_path):
            self._log(project_id, ticket_id, session_id, "checkout_failed", "Could not checkout ticket branch for review")
            sys.exit(1)
        self._log(project_id, ticket_id, session_id, "branch_checked_out", "Checked out ticket branch for review")
        base_save_dir = None
        memory_kwargs = {}
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
        self._cleanup_after_completion(session_id, project_path, ticket.id)
        pr_comment_body = self._generate_pr_comment_reply(comment_body, completion_summary or "")
        try:
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
            self._active_project_id = None
            self._active_ticket_id = None
    @staticmethod
    def _ticket_summary(t: TicketLike, mark_current: bool = False) -> dict:
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
            # associated_edges_labeled (and associated_nodes_labeled) provide names + ids; no need to pass raw associated_edge_ids
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

    def _cleanup_after_completion(self, session_id: str, project_path: str, ticket_id) -> None:
        """Send a final worker prompt to delete the plan file and remove fluff tests.

        This runs after the director signals complete. Failures are logged but never
        propagate — we don't want to mark a successful run as failed over cleanup."""
        try:
            plan_rel = os.path.join("plan", f"{ticket_id}_task_plan.md")
            cleanup_prompt = (
                f"The ticket is done. Please do two quick cleanup tasks, then stop:\n\n"
                f"1. Delete the plan file `{plan_rel}` if it exists "
                f"(use Bash: `rm -f {plan_rel}`). No need to remove it from git history.\n\n"
                f"2. Review any test files that were added or modified during this ticket. "
                f"Delete any tests that are obviously useless — e.g. tests that only check "
                f"`assert True`, have no assertions at all, only test that a function returns "
                f"without any meaningful check, or are empty/placeholder tests. "
                f"Do NOT remove tests that assert real behavior. "
                f"If all tests look meaningful, leave them alone.\n\n"
                f"Do not make any other code changes."
            )
            self._debug_log("[Cleanup] Sending post-completion cleanup prompt")
            self._log(project_path, ticket_id, session_id, "cleanup_prompt", "Post-completion cleanup", raw_output=cleanup_prompt)
            response = self._send_to_worker(cleanup_prompt, session_id, project_path, resume=True)
            out = (response.get("output") or "")[:500]
            self._debug_log(f"[Cleanup] Done: {out}")
            self._log(project_path, ticket_id, session_id, "cleanup_done", "Cleanup complete", raw_output=out)
        except Exception as e:
            self._debug_log(f"[Cleanup] Cleanup step failed (non-fatal): {e}")

    def _call_claude_code_worker(
        self,
        prompt: str,
        session_id: str,
        project_path: Optional[str] = None,
        resume: bool = False,
    ) -> dict:
        """Invoke Claude Code CLI in headless mode (-p flag) as the worker.
        Uses WORKER_API_KEY as ANTHROPIC_API_KEY. Sessions are continued via --resume <session_id>."""
        base_tools = ["Bash", "Read", "Edit", "Write", "MultiEdit", "Glob", "Grep", "LS",
                      "TodoWrite", "TodoRead", "WebFetch"]
        allowed_tools = ",".join(base_tools + self.worker_extra_tools)
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", allowed_tools]
        if self.worker_model:
            cmd.extend(["--model", self.worker_model])
        worker_session_id = self._worker_sessions.get(session_id)
        if resume and worker_session_id:
            cmd.extend(["--resume", worker_session_id])
        env = dict(os.environ)
        if self.worker_api_key and self.worker_api_key != "dummy":
            env["ANTHROPIC_API_KEY"] = self.worker_api_key
        cwd = project_path if (project_path and os.path.isdir(project_path)) else None
        self._debug_log(f"Claude Code CLI: cwd={cwd!r}, resume={worker_session_id!r}")

        _pid = getattr(self, "_active_project_id", None)
        _tid = getattr(self, "_active_ticket_id", None)

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except FileNotFoundError as e:
            raise WorkerUnavailableError(
                "claude CLI not found. Install Claude Code (npm install -g @anthropic-ai/claude-code) in the agent image.",
                cause=e,
            ) from e

        stdout_lines: list = []
        stderr_lines: list = []

        import threading
        import time as _time

        def _read_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        deadline = _time.monotonic() + self.worker_timeout_sec
        last_log_time = _time.monotonic()
        LOG_INTERVAL = 10  # post an intermediate log every 10 seconds of activity

        for line in proc.stdout:
            if _time.monotonic() > deadline:
                proc.kill()
                raise WorkerUnavailableError(
                    f"Claude Code timed out after {self.worker_timeout_sec}s",
                    cause=None,
                )
            stdout_lines.append(line)
            # Post intermediate log roughly every LOG_INTERVAL seconds so the UI shows activity.
            now = _time.monotonic()
            if _pid and _tid and session_id and (now - last_log_time) >= LOG_INTERVAL:
                try:
                    # Try to extract a meaningful snippet from the latest JSON line.
                    snippet = ""
                    raw = line.strip()
                    if raw:
                        try:
                            obj = json.loads(raw)
                            if isinstance(obj, dict):
                                snippet = (
                                    obj.get("content") or
                                    obj.get("text") or
                                    obj.get("message") or ""
                                )
                                if isinstance(snippet, list):
                                    snippet = " ".join(
                                        p.get("text", "") for p in snippet
                                        if isinstance(p, dict) and p.get("type") == "text"
                                    )
                                snippet = str(snippet)[:200]
                        except (json.JSONDecodeError, TypeError):
                            snippet = raw[:200]
                    self._backend.log(
                        _pid, _tid, session_id,
                        "worker_activity",
                        f"Worker active…{(' — ' + snippet) if snippet else ''}",
                    )
                except Exception:
                    pass
                last_log_time = now

        proc.wait()
        stderr_thread.join(timeout=5)

        if proc.returncode != 0:
            err_detail = ("".join(stderr_lines) or "".join(stdout_lines) or "")[:1000]
            raise WorkerUnavailableError(
                f"Claude Code exited with code {proc.returncode}: {err_detail}",
                cause=None,
            )
        stdout = "".join(stdout_lines)
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return {"output": stdout.strip(), "error": "", "return_code": 0}
        new_session_id = (data.get("session_id") or "").strip()
        if new_session_id:
            self._worker_sessions[session_id] = new_session_id
            self._worker_turn_count[session_id] = self._worker_turn_count.get(session_id, 0) + 1
        output = (data.get("result") or "").strip()
        if not output and self.debug:
            self._debug_log(f"Claude Code response empty; keys={list(data.keys())!r}")
        if session_id and project_path:
            self._trace_log(session_id, f"Claude Code response len={len(output)}", project_path)
        return {"output": output, "error": "", "return_code": 0}

    def _send_to_worker(
        self,
        prompt: str,
        session_id: str,
        project_path: Optional[str] = None,
        resume: bool = False,
        # These are threaded through from process_ticket so intermediate logs
        # can be posted while the worker is running (OpenCode streaming mode).
        project_id: Optional["uuid.UUID"] = None,
        ticket_id: Optional["uuid.UUID"] = None,
    ) -> dict:
        """Send a prompt to the configured worker. Dispatches to OpenCode (HTTP) or Claude Code (CLI) based on worker_mode."""
        if self.worker_mode == "claude-code":
            return self._call_claude_code_worker(prompt, session_id, project_path, resume)
        # Use caller-supplied IDs or fall back to the instance-level active context
        # set by process_ticket / process_ticket_review.
        _pid = project_id or getattr(self, "_active_project_id", None)
        _tid = ticket_id or getattr(self, "_active_ticket_id", None)
        # --- OpenCode HTTP server ---
        # Routes per https://opencode.ai/docs/server:
        # POST /session (body: title), POST /session/:id/prompt_async (fire-and-forget),
        # GET /event (SSE stream with session.idle to detect completion).
        base = self._opencode_server_url.rstrip("/")
        timeout_sec = self.worker_timeout_sec
        local_model_name = self.worker_model
        if local_model_name.startswith(f"{self.worker_provider_id}/"):
            local_model_name = local_model_name[len(self.worker_provider_id) + 1 :]

        worker_session_id = self._worker_sessions.get(session_id)
        if not worker_session_id or not resume:
            try:
                r = requests.post(
                    f"{base}/session",
                    json={"title": f"terarchitect-{session_id}"},
                    params={"directory": project_path} if (project_path and os.path.isdir(project_path)) else None,
                    auth=self._opencode_auth,
                    timeout=30,
                )
                r.raise_for_status()
                data = r.json()
                worker_session_id = (data.get("id") or data.get("sessionID") or "").strip()
                if not worker_session_id:
                    raise WorkerUnavailableError(
                        "OpenCode session create returned no id; worker may be misconfigured or server version mismatch.",
                        cause=None,
                    )
                self._worker_sessions[session_id] = worker_session_id
                self._worker_turn_count[session_id] = 0
            except requests.RequestException as e:
                msg = f"OpenCode server unreachable (session create): {e}. Is opencode serve running at {base}?"
                if getattr(e, "response", None) is not None and e.response.text:
                    msg += f" Response: {e.response.text[:500]}"
                raise WorkerUnavailableError(msg, cause=e) from e

        turn_count = self._worker_turn_count.get(session_id, 0)
        if turn_count > 0 and turn_count % 30 == 0:
            try:
                r_sum = requests.post(
                    f"{base}/session/{worker_session_id}/summarize",
                    json={"providerID": self.worker_provider_id, "modelID": local_model_name},
                    auth=self._opencode_auth,
                    timeout=120,
                )
                r_sum.raise_for_status()
            except requests.RequestException as e:
                msg = f"OpenCode server unreachable (summarize): {e}. Is opencode serve running at {base}?"
                if getattr(e, "response", None) is not None and e.response.text:
                    msg += f" Response: {e.response.text[:500]}"
                raise WorkerUnavailableError(msg, cause=e) from e

        prompt_headers = {"Content-Type": "application/json"}
        if project_path and os.path.isdir(project_path):
            prompt_headers["x-opencode-directory"] = project_path
        model_obj = {"providerID": self.worker_provider_id, "modelID": local_model_name}
        prompt_body = {
            "parts": [{"type": "text", "text": prompt}],
            "model": model_obj,
        }

        # Fire the prompt asynchronously so we can stream progress via SSE.
        try:
            r_async = requests.post(
                f"{base}/session/{worker_session_id}/prompt_async",
                json=prompt_body,
                headers=prompt_headers,
                auth=self._opencode_auth,
                timeout=30,
            )
            # prompt_async returns 204 No Content on success.
            if r_async.status_code not in (200, 201, 204):
                # Fall back to synchronous /message if prompt_async is not supported.
                self._debug_log(f"prompt_async returned {r_async.status_code}; falling back to synchronous /message")
                raise requests.RequestException(f"prompt_async status {r_async.status_code}")
        except requests.RequestException:
            # Fallback: synchronous POST /message (no streaming logs, but still works).
            self._debug_log("prompt_async unavailable; using synchronous POST /message (no intra-turn logs)")
            try:
                r_sync = requests.post(
                    f"{base}/session/{worker_session_id}/message",
                    json=prompt_body,
                    headers=prompt_headers,
                    auth=self._opencode_auth,
                    timeout=timeout_sec,
                )
                r_sync.raise_for_status()
                output = self._extract_opencode_output(r_sync.json())
                self._worker_turn_count[session_id] = turn_count + 1
                if session_id and project_path:
                    self._trace_log(session_id, f"OpenCode sync response len={len(output)}", project_path)
                return {"output": output, "error": "", "return_code": 0}
            except requests.RequestException as e:
                msg = f"OpenCode server unreachable (message): {e}. Is opencode serve running at {base}?"
                if getattr(e, "response", None) is not None and e.response.text:
                    msg += f" Response: {e.response.text[:500]}"
                raise WorkerUnavailableError(msg, cause=e) from e

        # --- Stream SSE /event until session.idle for this worker session ---
        output = self._stream_opencode_until_idle(
            base=base,
            worker_session_id=worker_session_id,
            timeout_sec=timeout_sec,
            project_id=_pid,
            ticket_id=_tid,
            session_id=session_id,
            project_path=project_path,
        )
        self._worker_turn_count[session_id] = turn_count + 1
        if session_id and project_path:
            self._trace_log(session_id, f"OpenCode streaming response len={len(output)}", project_path)
        return {"output": output, "error": "", "return_code": 0}

    @staticmethod
    def _extract_opencode_output(data: Any) -> str:
        """Extract text output from an OpenCode message response dict."""
        if isinstance(data, list) and data:
            data = data[-1]
        if not isinstance(data, dict):
            return ""
        parts = data.get("parts") or []
        if not isinstance(parts, list):
            parts = []
        text_bits = []
        for p in parts:
            if not isinstance(p, dict):
                continue
            pt = p.get("type")
            if pt in ("text", "reasoning"):
                text_bits.append((p.get("text") or "").strip())
        return ("\n".join(t for t in text_bits if t)).strip()

    def _stream_opencode_until_idle(
        self,
        base: str,
        worker_session_id: str,
        timeout_sec: int,
        project_id: Optional["uuid.UUID"],
        ticket_id: Optional["uuid.UUID"],
        session_id: str,
        project_path: Optional[str],
    ) -> str:
        """Subscribe to GET /event SSE stream and wait for session.idle for worker_session_id.
        Posts intermediate tool-use log entries while the worker is active.
        Returns the final text output from the completed assistant message."""
        import time

        event_url = f"{base}/event"
        deadline = time.monotonic() + timeout_sec
        last_log_time = time.monotonic()
        log_interval = 15.0  # post a heartbeat log at most once per 15s
        tool_calls_seen: List[str] = []

        self._debug_log(f"SSE stream: waiting for session.idle on worker_session={worker_session_id}")
        try:
            with requests.get(
                event_url,
                stream=True,
                auth=self._opencode_auth,
                timeout=(10, timeout_sec),  # (connect, read)
            ) as resp:
                resp.raise_for_status()
                event_type = ""
                data_lines: List[str] = []

                for raw_line in resp.iter_lines(decode_unicode=True):
                    if time.monotonic() > deadline:
                        self._debug_log(f"SSE stream: timeout after {timeout_sec}s")
                        break

                    if raw_line is None:
                        continue
                    line = raw_line.strip()

                    if line.startswith("event:"):
                        event_type = line[len("event:"):].strip()
                        data_lines = []
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:"):].strip())
                    elif line == "":
                        # Blank line = end of one SSE event; dispatch it.
                        raw_data = "\n".join(data_lines)
                        data_lines = []

                        try:
                            payload = json.loads(raw_data) if raw_data else {}
                        except json.JSONDecodeError:
                            payload = {}

                        # All events carry a properties object; session events have sessionID inside.
                        props = payload.get("properties") or payload
                        evt_session_id = (
                            props.get("sessionID")
                            or props.get("session_id")
                            or (props.get("info") or {}).get("sessionID")
                            or ""
                        )

                        if evt_session_id and evt_session_id != worker_session_id:
                            # Event belongs to a different session; ignore.
                            event_type = ""
                            continue

                        # Detect tool-call activity for intermediate logging.
                        resolved_type = event_type or (payload.get("type") or "")
                        if "tool" in resolved_type.lower() or "part" in resolved_type.lower():
                            part = props.get("part") or props
                            tool_name = (
                                part.get("tool") or part.get("name")
                                or part.get("toolName") or part.get("call", {}).get("name")
                                or ""
                            )
                            if tool_name and tool_name not in tool_calls_seen:
                                tool_calls_seen.append(tool_name)

                        # Post a heartbeat log if tools have accumulated or enough time has passed.
                        now = time.monotonic()
                        if project_id and ticket_id and (now - last_log_time >= log_interval):
                            tools_summary = ", ".join(tool_calls_seen[-5:]) if tool_calls_seen else "working…"
                            self._log(
                                project_id, ticket_id, session_id,
                                "worker_activity",
                                f"Worker active — recent tools: {tools_summary}",
                            )
                            last_log_time = now

                        # session.idle = turn complete; fetch the final message and return.
                        if "idle" in resolved_type.lower() and (not evt_session_id or evt_session_id == worker_session_id):
                            self._debug_log(f"SSE stream: session.idle received for {worker_session_id}")
                            return self._fetch_opencode_last_message(base, worker_session_id)

                        event_type = ""

        except requests.RequestException as e:
            raise WorkerUnavailableError(
                f"OpenCode SSE stream error: {e}. Is opencode serve running at {base}?",
                cause=e,
            ) from e

        # Timed out or stream ended without idle; try to fetch whatever messages exist.
        self._debug_log(f"SSE stream: ended without session.idle; fetching messages as fallback")
        return self._fetch_opencode_last_message(base, worker_session_id)

    def _fetch_opencode_last_message(self, base: str, worker_session_id: str) -> str:
        """Fetch all messages for a session and return the text of the last assistant message."""
        try:
            r = requests.get(
                f"{base}/session/{worker_session_id}/message",
                auth=self._opencode_auth,
                timeout=30,
            )
            r.raise_for_status()
            messages = r.json()
            if not isinstance(messages, list):
                messages = [messages]
            # Find the last assistant message (role == "assistant").
            for msg in reversed(messages):
                info = msg.get("info") or {}
                role = info.get("role") or ""
                if role == "assistant":
                    return self._extract_opencode_output(msg)
            # No assistant message found; try extracting from last entry anyway.
            if messages:
                return self._extract_opencode_output(messages[-1])
        except requests.RequestException as e:
            self._debug_log(f"_fetch_opencode_last_message failed: {e}")
        return ""

    def _summarize_director_messages(self, messages: List[Dict[str, str]]) -> str:
        """Call the agent API to summarize a chunk of Director conversation. Returns summary text."""
        formatted = "\n\n".join(
            f"**{m.get('role', '')}**:\n{m.get('content') or ''}" for m in messages
        )
        system = """You are summarizing a conversation between the Director (an agent that assesses worker output and decides the next prompt) and the system.
Preserve: project/ticket context if present, completion decisions (complete vs not), key next prompts given to the worker, and worker outcomes.
Output a single concise narrative under 200 words. No JSON, no labels—just prose."""
        try:
            return self._director_request(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": formatted},
                ],
                max_tokens=2048,
                temperature=0.2,
                timeout=120,
            ).strip()
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
        # Cap iterations: at most ceil(len(out) / chunk_size) rounds needed to reduce to one summary.
        max_iterations = max(1, len(out) // _DIRECTOR_COMPACT_CHUNK_SIZE + 1) * 2
        for _ in range(max_iterations):
            full = [system_msg] + out + [new_user_msg]
            if _count_tokens_for_messages(full) <= token_limit:
                return out
            if len(out) < _DIRECTOR_COMPACT_CHUNK_SIZE:
                return out
            chunk = out[:_DIRECTOR_COMPACT_CHUNK_SIZE]
            summary = self._summarize_director_messages(chunk)
            summary_msg = {"role": "user", "content": "Previous conversation (summarized):\n\n" + summary}
            out = [summary_msg] + out[_DIRECTOR_COMPACT_CHUNK_SIZE:]
        return out

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
        is_review = bool((context or {}).get("pr_review_comment"))
        if is_plan_review:
            system_content = get_agent_system_prompt() + "\n\n" + get_agent_plan_review_instructions()
        elif is_review:
            system_content = get_agent_system_prompt() + (
                "\n\n--- PR REVIEW MODE ---\n"
                "You are assessing a PR review response, not a fresh ticket implementation. "
                "The worker has already done the work. Your job is to check whether the review issues are addressed and tests pass — not to micromanage process. "
                "If the worker shows pytest output with passing tests and describes the fixes, accept it and mark complete. "
                "Only push back if: (a) the test output is clearly missing or fabricated (e.g. no numbers, no test names), "
                "(b) a blocking issue from the review comment is explicitly not mentioned, or "
                "(c) tests are shown as failing. "
                "Do NOT demand step-by-step TDD re-runs if the worker already shows green tests. "
                "Trust passing pytest output. Be efficient — unnecessary back-and-forth wastes everyone's time."
            )
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

Judge the plan. Respond in JSON only with:
- plan_approved (true/false)
- feedback (2-4 sentences max: what is good or what is wrong — be specific, no fluff)

If false, also include next_prompt with the actionable fixes to send the worker (bullet points ok, no code fences, no preamble)."""
            else:
                pr_comment_first = (context.get("pr_review_comment") or "").strip()
                if pr_comment_first:
                    assess_first = (
                        "Assess: Has the worker addressed the review comment with real test evidence?\n"
                        "Mark complete if all issues are addressed AND the worker provided actual pytest output (pass counts). "
                        "Do NOT require step-by-step process. Only send next_prompt if something is genuinely missing or broken.\n"
                        "Respond in JSON only."
                    )
                else:
                    assess_first = "Assess: Is the ticket complete? Respond in JSON only."
                user_msg_content = f"""{setup_hint}{plan_block}Context:
{json.dumps(context, indent=2)}

{memory_block}Full conversation with Worker:
{full_conversation}

{assess_first}"""
        else:
            n = max(len(prompt_history), len(conversation_history))
            prompt = prompt_history[-1] if prompt_history else ""
            response = conversation_history[-1] if conversation_history else ""
            if is_plan_review:
                user_msg_content = f"""{memory_block}New worker turn:

### Turn {n} - Prompt to Worker:
{prompt}

### Turn {n} - Worker response:
{response}

Judge the plan. Respond in JSON only with:
- plan_approved (true/false)
- feedback (2-4 sentences max: what is good or what is wrong — be specific, no fluff)

If false, also include next_prompt with the actionable fixes to send the worker (bullet points ok, no code fences, no preamble)."""
            else:
                # Re-anchor key context on every subsequent turn so it survives compaction.
                pr_comment = (context.get("pr_review_comment") or "").strip()
                ticket_info = context.get("current_ticket") or {}
                is_review = bool(pr_comment)
                anchor = ""
                if pr_comment:
                    anchor = (
                        f"Reminder — original PR review comment to address:\n{pr_comment}\n\n"
                        f"Ticket: {ticket_info.get('title', '')}\n\n"
                    )
                if is_review:
                    assess_instruction = (
                        "Assess: Has the worker addressed the review comment with real test evidence?\n"
                        "Mark complete if: all issues from the review comment are addressed AND the worker provided actual pytest output (pass counts, not just prose). "
                        "Do NOT require step-by-step process — the worker may address everything in one shot. "
                        "Only send next_prompt if something is genuinely missing or broken (e.g. no test output, a review issue not addressed, tests failing). "
                        "If stuck on the same sub-step for 3+ turns, simplify the ask or accept it as done if it is a minor nit.\n"
                        "Respond in JSON only."
                    )
                else:
                    assess_instruction = (
                        "Assess: Is the ticket complete? Respond in JSON only.\n"
                        "If the worker has been stuck on the same sub-step for 3+ turns, escalate: either simplify the ask, skip it, or call it done if it is a minor nit."
                    )
                user_msg_content = f"""{setup_hint}{plan_block}{memory_block}{anchor}New worker turn (turn {n} of this session):

### Turn {n} - Prompt to Worker:
{prompt}

### Turn {n} - Worker response:
{response}

{assess_instruction}"""

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

        if session_id:
            self._trace_log(
                session_id,
                "Director API request (stateful):\n"
                f"URL: {self.director_api_url}\n"
                f"Model: {self.director_model}\n"
                f"Messages count: {len(messages_for_api)}\n"
                f"System prompt length: {len(system_content)} chars\n"
                f"Last user message:\n{user_msg_content[:1500]}...",
                project_path,
            )

        try:
            content = self._director_request(messages_for_api, timeout=300, json_mode=True, max_tokens=8192)
        except AgentAPIError:
            raise

        self._debug_log(f"Director API response ({len(content)} chars): {content[:500]}")
        if session_id:
            self._trace_log(
                session_id,
                f"Director API raw response content:\n{content}",
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
                f"Director API response is not valid JSON: {content[:200]}...",
                cause=None,
            )

        if not isinstance(parsed, dict):
            raise AgentAPIError(f"Director API response must be a JSON object, got: {type(parsed)}")

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
{comment_body[:3000]}
\"\"\"

The implementation work produced this summary:

\"\"\"
{(completion_summary or "(No summary)")[:1000]}
\"\"\"

Write a short direct reply to the reviewer (2–5 sentences) that answers their question or addresses their point. If they asked a specific question (e.g. "Do we update X on the backend?"), answer it directly (e.g. "Yes, we update X in ..." or "No; I've added that in ..."). Do not post a generic "ticket completed" summary. Output only the reply text, no preamble or labels. Maximum 100 words."""

        try:
            content = self._director_request(
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=512,
                temperature=0.2,
                timeout=60,
            ).strip()
            if content:
                return content
        except Exception as e:
            self._debug_log(f"PR comment reply generation failed: {e}, using completion summary")
        return completion_summary or "Addressed review feedback."

    def _ensure_ticket_branch(
        self,
        ticket: TicketLike,
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

    def _checkout_ticket_branch(self, ticket: TicketLike, project_path: str) -> bool:
        """Checkout existing branch ticket-{ticket.id}, fetching from remote first if needed."""
        branch_name = f"ticket-{ticket.id}"
        try:
            # First try local checkout (works if branch already exists locally).
            r = subprocess.run(
                ["git", "checkout", branch_name],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0:
                return True
            # Branch not found locally — fetch with explicit refspec so origin/<branch> tracking ref is created.
            # Plain `git fetch origin <branch>` only updates FETCH_HEAD, not origin/<branch>.
            self._debug_log(f"Branch {branch_name} not local, fetching from origin…")
            subprocess.run(
                ["git", "fetch", "origin", f"{branch_name}:refs/remotes/origin/{branch_name}"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            r2 = subprocess.run(
                ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r2.returncode == 0:
                return True
            self._debug_log(f"Checkout branch {branch_name} failed: {r2.stderr or r2.stdout}")
            return False
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

Write a clear, descriptive paragraph for the PR description explaining what was accomplished: files changed, behavior added or fixed, and any notable decisions. Plain text only, no markdown headers. Maximum 200 words."""
        try:
            content = self._director_request(
                messages=[
                    {"role": "system", "content": "You write concise, accurate PR descriptions for code changes. Output only the paragraph, no labels or prefixes."},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=2048,
                temperature=0.3,
                timeout=60,
            ).strip()
            return content if content else None
        except Exception as e:
            self._debug_log(f"PR description generation failed: {e}")
            return None

    def _finalize(
        self,
        ticket: TicketLike,
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
                    commit_r = subprocess.run(
                        ["git", "commit", "-m", commit_message],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        env=gh_env,
                    )
                    self._debug_log(f"git commit exit={commit_r.returncode} stdout={commit_r.stdout[:200]} stderr={commit_r.stderr[:200]}")
                else:
                    self._debug_log("git status: no changes to commit, skipping commit step")
                push_r = subprocess.run(
                    ["git", "push", "-u", "origin", branch_name],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    env=gh_env,
                )
                self._debug_log(f"git push exit={push_r.returncode} stdout={push_r.stdout[:200]} stderr={push_r.stderr[:200]}")
                if push_r.returncode != 0:
                    self._debug_log(f"WARNING: git push failed — PR comment will still be posted but code may not be pushed")
                if review_mode and pr_number_for_comment is not None:
                    body = (pr_comment_body or completion_summary or "Addressed review feedback.").strip()
                    if len(body) > 60000:
                        body = body[:59997] + "..."
                    body = "**[from terarchitect]** " + body + "\n\n" + BOT_COMMENT_SIGNATURE
                    comment_r = subprocess.run(
                        ["gh", "pr", "comment", str(pr_number_for_comment), "--body", body],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        env=gh_env,
                    )
                    self._debug_log(f"gh pr comment exit={comment_r.returncode} stderr={comment_r.stderr[:200]}")
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
        # Update ticket/PR via backend (DB or API)
        if review_mode:
            self._backend.complete(
                ticket.id,
                ticket.project_id,
                summary=(completion_summary or "Addressed review feedback.").strip()[:500],
                review_comment_body=pr_comment_body,
            )
        else:
            self._backend.complete(
                ticket.id,
                ticket.project_id,
                pr_url=pr_url,
                pr_number=pr_number,
                summary=(completion_summary or ticket.title or "Implementation").strip()[:500],
            )

    def _log(
        self,
        project_id: uuid.UUID,
        ticket_id: uuid.UUID,
        session_id: str,
        step: str,
        summary: str,
        raw_output: Optional[str] = None,
    ) -> None:
        """Log an execution step (delegates to backend)."""
        self._backend.log(project_id, ticket_id, session_id, step, summary, raw_output)


def build_worker_context(ticket: Any) -> dict:
    """Build worker-context dict from DB. Used by the worker-context API route. Requires Flask app context."""
    from models.db import Project, Graph, Note, Ticket as TicketModel

    project = Project.query.get(ticket.project_id)
    if project is None:
        raise ValueError(f"Project {ticket.project_id} not found for ticket {ticket.id}")
    current_id = ticket.id
    context = {
        "project_name": project.name,
        "project_description": project.description,
        "github_url": project.github_url,
        "current_ticket": MiddleAgent._ticket_summary(ticket, mark_current=True),
        "graph": None,
        "notes": [],
        "backlog_tickets": [],
        "in_progress_tickets": [],
        "done_tickets": [],
    }
    graph = Graph.query.filter_by(project_id=ticket.project_id).first()
    if graph:
        nodes = graph.nodes if graph.nodes else []
        edges = graph.edges if graph.edges else []
        full_enriched_edges = MiddleAgent._edges_with_readable_endpoints(nodes, edges)
        context["graph"] = {"nodes": nodes, "edges": full_enriched_edges}
        node_ids, edge_ids = MiddleAgent._expand_all_marker(
            nodes, edges, ticket.associated_node_ids or [], ticket.associated_edge_ids or []
        )
        rel_nodes, rel_edges = MiddleAgent._relevant_subgraph(nodes, edges, node_ids, edge_ids)
        rel_enriched_edges = MiddleAgent._edges_with_readable_endpoints(rel_nodes, rel_edges)
        for e in rel_enriched_edges:
            e["label_and_id"] = "{} → {}: {}".format(
                e.get("source_label", ""), e.get("target_label", ""), e.get("id", "")
            )
        rel_nodes_with_label_and_id = []
        for n in rel_nodes:
            copy = dict(n)
            data = copy.get("data") or {}
            label = data.get("label") or copy.get("id") or ""
            copy["label_and_id"] = "{}: {}".format(label, copy.get("id", ""))
            rel_nodes_with_label_and_id.append(copy)
        context["graph_relevant_to_current_ticket"] = {"nodes": rel_nodes_with_label_and_id, "edges": rel_enriched_edges}
        node_label_by_id = {n.get("id"): (n.get("data") or {}).get("label") or n.get("id") for n in nodes}
        edge_label_by_id = {
            e.get("id"): "{} → {}".format(e.get("source_label", ""), e.get("target_label", ""))
            for e in full_enriched_edges
        }
        exp_node_ids, exp_edge_ids = MiddleAgent._expand_all_marker(
            nodes, edges, ticket.associated_node_ids or [], ticket.associated_edge_ids or []
        )
        context["current_ticket"]["associated_nodes_labeled"] = [
            "{}: {}".format(node_label_by_id.get(nid, nid), nid) for nid in exp_node_ids
        ]
        context["current_ticket"]["associated_edges_labeled"] = [
            "{}: {}".format(edge_label_by_id.get(eid, eid), eid) for eid in exp_edge_ids
        ]
    else:
        context["graph_relevant_to_current_ticket"] = {"nodes": [], "edges": []}
        context["current_ticket"]["associated_nodes_labeled"] = []
        context["current_ticket"]["associated_edges_labeled"] = []

    notes = Note.query.filter_by(project_id=ticket.project_id).all()
    context["notes"] = [{"title": n.title, "content": n.content, "node_id": n.node_id} for n in notes]
    backlog = (
        TicketModel.query.filter_by(project_id=ticket.project_id, column_id="backlog")
        .order_by(TicketModel.updated_at.desc()).limit(10).all()
    )
    in_progress = (
        TicketModel.query.filter_by(project_id=ticket.project_id, column_id="in_progress")
        .order_by(TicketModel.updated_at.desc()).limit(6).all()
    )
    done = (
        TicketModel.query.filter_by(project_id=ticket.project_id, column_id="done")
        .order_by(TicketModel.updated_at.desc()).limit(6).all()
    )
    context["backlog_tickets"] = [MiddleAgent._ticket_summary(t) for t in backlog[:10]]
    in_progress_summaries = []
    for t in in_progress:
        if t.id == current_id:
            in_progress_summaries.insert(0, MiddleAgent._ticket_summary(t, mark_current=True))
        else:
            in_progress_summaries.append(MiddleAgent._ticket_summary(t))
    context["in_progress_tickets"] = in_progress_summaries[:5]
    context["done_tickets"] = [MiddleAgent._ticket_summary(t) for t in done[:5]]
    return context


