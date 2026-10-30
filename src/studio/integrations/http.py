"""Injectable authenticated provider HTTP; no inference API dependencies."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, Protocol
from .common import ProviderFailure, RemoteUnknown


class HttpPort(Protocol):
    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> tuple[int, dict[str, str], bytes]: ...


class UrllibHttp:
    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def request(self, method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as res:
                return res.status, dict(res.headers.items()), res.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read(4096)
        except (TimeoutError, urllib.error.URLError, ConnectionError, OSError) as exc:
            raise RemoteUnknown(f"provider transport failed: {type(exc).__name__}") from exc


class JsonProvider:
    def __init__(self, http: HttpPort, token_supplier: Callable[[], str], base_url: str):
        if not base_url.startswith("https://"):
            raise ValueError("HTTPS API URL required")
        self.http = http
        self.token_supplier = token_supplier
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, data: dict | None = None, *, expect: tuple[int, ...] = (200,)) -> dict | list | None:
        token = self.token_supplier()
        if not token:
            raise ProviderFailure("connector has no configured credential")
        headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "User-Agent": "nomad-studio-g/1"}
        payload = None
        if data is not None:
            payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, response_headers, body = self.http.request(method, self.base_url + path, headers, payload)
        if status not in expect:
            if status in (403, 429) and (response_headers.get("Retry-After") or response_headers.get("X-RateLimit-Remaining") == "0"):
                raw = response_headers.get("Retry-After")
                try:
                    seconds = float(raw) if raw is not None else None
                except ValueError:
                    seconds = None
                raise ProviderFailure("rate limited; retry after backoff", status, seconds)
            raise ProviderFailure("provider HTTP " + str(status), status)
        if not body:
            return None
        try:
            return json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise ProviderFailure("provider returned invalid JSON") from exc
