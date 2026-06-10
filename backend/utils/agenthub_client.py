"""
HTTP client for the agenthub API.

Used by the backend to query the DAG for UI display and by the coordinator
to provision agent keys. The agent containers use the `ah` CLI directly.

Usage:
    client = AgenthubClient.from_env()           # reads AGENTHUB_URL + AGENTHUB_API_KEY
    client = AgenthubClient(url, api_key)        # explicit
    admin  = AgenthubClient.admin_from_env()     # reads AGENTHUB_URL + AGENTHUB_ADMIN_KEY
"""

import os
from typing import Optional
import requests


class AgenthubError(Exception):
    pass


class AgenthubClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    @classmethod
    def from_env(cls) -> "AgenthubClient":
        """Build from AGENTHUB_URL + AGENTHUB_API_KEY env vars."""
        return cls(
            base_url=os.environ["AGENTHUB_URL"],
            api_key=os.environ["AGENTHUB_API_KEY"],
        )

    @classmethod
    def admin_from_env(cls) -> "AgenthubClient":
        """Build from AGENTHUB_URL + AGENTHUB_ADMIN_KEY env vars (for agent provisioning)."""
        return cls(
            base_url=os.environ["AGENTHUB_URL"],
            api_key=os.environ["AGENTHUB_ADMIN_KEY"],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, **params) -> any:
        resp = self._session.get(
            self.base_url + path, params=params or None, timeout=self.timeout
        )
        self._raise(resp)
        return resp.json()

    def _post(self, path: str, body: dict) -> any:
        resp = self._session.post(
            self.base_url + path, json=body, timeout=self.timeout
        )
        self._raise(resp)
        return resp.json()

    def _get_text(self, path: str) -> str:
        resp = self._session.get(self.base_url + path, timeout=self.timeout)
        self._raise(resp)
        return resp.text

    @staticmethod
    def _raise(resp: requests.Response):
        if resp.status_code >= 400:
            raise AgenthubError(f"agenthub {resp.status_code}: {resp.text[:200]}")

    # ------------------------------------------------------------------
    # Git DAG
    # ------------------------------------------------------------------

    def leaves(self) -> list[dict]:
        """Commits with no children — the current work frontier."""
        return self._get("/api/git/leaves")

    def log(self, agent_id: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Recent commits, optionally filtered by agent."""
        params = {"limit": limit}
        if agent_id:
            params["agent"] = agent_id
        return self._get("/api/git/commits", **params)

    def get_commit(self, hash: str) -> dict:
        return self._get(f"/api/git/commits/{hash}")

    def children(self, hash: str) -> list[dict]:
        """Direct children of a commit."""
        return self._get(f"/api/git/commits/{hash}/children")

    def lineage(self, hash: str) -> list[dict]:
        """Ancestry path from a commit back to the root."""
        return self._get(f"/api/git/commits/{hash}/lineage")

    def diff(self, hash_a: str, hash_b: str) -> str:
        """Unified diff between two commits."""
        return self._get_text(f"/api/git/diff/{hash_a}/{hash_b}")

    def receipt(self, hash: str) -> dict:
        """Agent-facing receipt for a commit, including mentions and fetchability."""
        return self._get(f"/api/git/receipts/{hash}")

    def doctor(self) -> dict:
        """Remote AgentHub diagnostics for auth, database, and repo plausibility."""
        return self._get("/api/doctor")

    def seed(self, repo_path: str, commit_hash: str) -> dict:
        """Scaffolded lineage seed surface. May return a not-supported error."""
        return self._post("/api/git/seed", {"repo_path": repo_path, "commit_hash": commit_hash})

    # ------------------------------------------------------------------
    # Message board
    # ------------------------------------------------------------------

    def channels(self) -> list[dict]:
        return self._get("/api/channels")

    def create_channel(self, name: str, description: str = "") -> dict:
        return self._post("/api/channels", {"name": name, "description": description})

    def posts(self, channel: str, limit: int = 50) -> list[dict]:
        """Posts in a channel, newest first."""
        return self._get(f"/api/channels/{channel}/posts", limit=limit)

    def post(self, channel: str, content: str, parent_id: Optional[int] = None) -> dict:
        """Create a post (or reply if parent_id is set)."""
        body: dict = {"content": content}
        if parent_id is not None:
            body["parent_id"] = parent_id
        return self._post(f"/api/channels/{channel}/posts", body)

    def get_post(self, post_id: int) -> dict:
        return self._get(f"/api/posts/{post_id}")

    def replies(self, post_id: int) -> list[dict]:
        return self._get(f"/api/posts/{post_id}/replies")

    def events(self, channel_prefix: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Recent normalized events, optionally filtered by channel prefix."""
        params = {"limit": limit}
        if channel_prefix:
            params["channel_prefix"] = channel_prefix
        return self._get("/api/events", **params)

    # ------------------------------------------------------------------
    # Admin (requires admin key)
    # ------------------------------------------------------------------

    def create_agent(self, agent_id: str) -> dict:
        """
        Provision a new agent and return its API key.
        Client must be constructed with the admin key (use admin_from_env()).
        """
        return self._post("/api/admin/agents", {"id": agent_id})

    # ------------------------------------------------------------------
    # Convenience: DAG summary for a ticket
    # ------------------------------------------------------------------

    def ticket_summary(self, ticket_id: str) -> dict:
        """
        Return a dict with recent commits and board posts for a ticket channel.
        Useful for injecting peer context into the Director prompt or displaying
        in the terarchitect UI.
        """
        channel = f"ticket-{ticket_id}"
        try:
            board_posts = self.posts(channel, limit=20)
        except AgenthubError:
            board_posts = []

        recent_leaves = self.leaves()

        return {
            "leaves": recent_leaves,
            "board_posts": board_posts,
        }
