"""Low-level HTTP client for the Terarchitect API. No external dependencies."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class APIError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        detail: Optional[str] = None,
        hint: Optional[str] = None,
        request_id: Optional[str] = None,
        phase: Optional[str] = None,
        next_commands: Optional[list[str]] = None,
    ):
        self.status = status
        self.message = message
        self.detail = detail
        self.hint = hint
        self.request_id = request_id
        self.phase = phase
        self.next_commands = list(next_commands or [])
        super().__init__(f"API {status}: {message}")

    def with_context(
        self,
        *,
        detail: Optional[str] = None,
        hint: Optional[str] = None,
        request_id: Optional[str] = None,
        phase: Optional[str] = None,
        next_commands: Optional[list[str]] = None,
    ) -> "APIError":
        return APIError(
            self.status,
            self.message,
            detail=detail if detail is not None else self.detail,
            hint=hint if hint is not None else self.hint,
            request_id=request_id if request_id is not None else self.request_id,
            phase=phase if phase is not None else self.phase,
            next_commands=next_commands if next_commands is not None else self.next_commands,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "message": self.message,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.hint:
            payload["hint"] = self.hint
        if self.request_id:
            payload["request_id"] = self.request_id
        if self.phase:
            payload["phase"] = self.phase
        if self.next_commands:
            payload["next_commands"] = self.next_commands
        return payload


class API:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: Optional[dict] = None) -> Any:
        return self._request_with_accept(method, path, body=body)

    def _request_with_accept(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        *,
        accept: str = "application/json",
        parse_json: bool = True,
    ) -> Any:
        url = self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": accept}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                text = resp.read().decode()
                if not text.strip():
                    return None
                return json.loads(text) if parse_json else text
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            try:
                parsed = json.loads(text)
                msg = parsed.get("error") or parsed.get("message") or text
                raise APIError(
                    e.code,
                    msg,
                    detail=parsed.get("detail"),
                    hint=parsed.get("hint"),
                    request_id=parsed.get("request_id"),
                    phase=parsed.get("phase"),
                    next_commands=parsed.get("next_commands"),
                )
            except APIError:
                raise
            except Exception:
                msg = text or e.reason
            raise APIError(e.code, msg)
        except urllib.error.URLError as e:
            print(f"Cannot reach {self.base_url}: {e.reason}", file=sys.stderr)
            print("Is the backend running? Set TERARCHITECT_API_URL if needed.", file=sys.stderr)
            sys.exit(1)

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def get_text(self, path: str, accept: str = "text/plain") -> str:
        return self._request_with_accept(
            "GET", path, accept=accept, parse_json=False
        )

    def post(self, path: str, body: Optional[dict] = None) -> Any:
        return self._request("POST", path, body if body is not None else {})

    def put(self, path: str, body: dict) -> Any:
        return self._request("PUT", path, body)

    def patch(self, path: str, body: dict) -> Any:
        return self._request("PATCH", path, body)

    def delete(self, path: str, body: Optional[dict] = None) -> Any:
        return self._request("DELETE", path, body)
