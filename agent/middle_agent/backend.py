"""
Phase 2: Agent backend abstraction. Flask backend uses DB/app; HTTP backend uses Phase 1 API.
"""
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID


class AgentBackend(Protocol):
    """Backend for context, logging, completion, memory, and cancel. Used by MiddleAgent."""

    def get_context(self, project_id: UUID, ticket_id: UUID) -> Optional[Dict[str, Any]]:
        """Return full context dict (project, graph, current_ticket, notes, backlog/in_progress/done). None if not found."""
        ...

    def log(
        self,
        project_id: UUID,
        ticket_id: UUID,
        session_id: str,
        step: str,
        summary: str,
        raw_output: Optional[str] = None,
    ) -> None:
        """Append an execution log entry."""
        ...

    def complete(
        self,
        ticket_id: UUID,
        project_id: UUID,
        pr_url: Optional[str] = None,
        pr_number: Optional[int] = None,
        summary: str = "",
        review_comment_body: Optional[str] = None,
    ) -> None:
        """Mark ticket complete (update column, PR record). Git/PR creation is done by the agent before calling this."""
        ...

    def retrieve_memory(
        self,
        project_id: UUID,
        queries: List[str],
        num_to_retrieve: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return list of {question, docs, doc_scores} per query."""
        ...

    def index_memory(self, project_id: UUID, docs: List[str]) -> None:
        """Index documents into project memory."""
        ...

    def cancel_requested(self, project_id: UUID, ticket_id: UUID) -> bool:
        """Return True if the user requested cancellation for this ticket."""
        ...


class HttpAgentBackend:
    """Backend that uses Phase 1 worker-facing API. No Flask/DB."""

    def __init__(
        self,
        base_url: str,
        auth_token: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = (auth_token or "").strip() or None
        self._headers: Dict[str, str] = {}
        if self.auth_token:
            self._headers["Authorization"] = f"Bearer {self.auth_token}"

    def get_context(self, project_id: UUID, ticket_id: UUID) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/api/projects/{project_id}/tickets/{ticket_id}/worker-context"
        try:
            import requests
            r = requests.get(url, headers=self._headers, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def log(
        self,
        project_id: UUID,
        ticket_id: UUID,
        session_id: str,
        step: str,
        summary: str,
        raw_output: Optional[str] = None,
    ) -> None:
        url = f"{self.base_url}/api/projects/{project_id}/tickets/{ticket_id}/logs"
        payload = {"session_id": session_id, "step": step[:100], "summary": summary}
        if raw_output is not None:
            payload["raw_output"] = raw_output
        try:
            import requests
            requests.post(url, json=payload, headers=self._headers, timeout=30)
        except Exception:
            pass

    def complete(
        self,
        ticket_id: UUID,
        project_id: UUID,
        pr_url: Optional[str] = None,
        pr_number: Optional[int] = None,
        summary: str = "",
        review_comment_body: Optional[str] = None,
    ) -> None:
        url = f"{self.base_url}/api/projects/{project_id}/tickets/{ticket_id}/complete"
        payload = {"summary": summary}
        if pr_url is not None:
            payload["pr_url"] = pr_url
        if pr_number is not None:
            payload["pr_number"] = pr_number
        if review_comment_body is not None:
            payload["review_comment_body"] = review_comment_body
        try:
            import requests
            requests.post(url, json=payload, headers=self._headers, timeout=30)
        except Exception:
            pass

    def retrieve_memory(
        self,
        project_id: UUID,
        queries: List[str],
        num_to_retrieve: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/api/projects/{project_id}/memory/retrieve"
        payload: Dict[str, Any] = {"queries": queries}
        if num_to_retrieve is not None:
            payload["num_to_retrieve"] = num_to_retrieve
        try:
            import requests
            r = requests.post(url, json=payload, headers=self._headers, timeout=60)
            r.raise_for_status()
            return (r.json() or {}).get("results") or []
        except Exception:
            return []

    def index_memory(self, project_id: UUID, docs: List[str]) -> None:
        url = f"{self.base_url}/api/projects/{project_id}/memory/index"
        try:
            import requests
            requests.post(url, json={"docs": docs}, headers=self._headers, timeout=60)
        except Exception:
            pass

    def cancel_requested(self, project_id: UUID, ticket_id: UUID) -> bool:
        url = f"{self.base_url}/api/projects/{project_id}/tickets/{ticket_id}/cancel-requested"
        try:
            import requests
            r = requests.get(url, headers=self._headers, timeout=10)
            r.raise_for_status()
            return (r.json() or {}).get("cancel_requested") is True
        except Exception:
            return False
