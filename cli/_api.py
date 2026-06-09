"""Low-level HTTP client for the Terarchitect API. No external dependencies."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"API {status}: {message}")


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
