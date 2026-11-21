"""A tiny fake ``requests.Session`` so tests run with no network and no extra dependencies."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import requests

from foundry_finops import FinOpsClient, RetryPolicy, StaticTokenCredential


class _Raw:
    """The bit of a urllib3 response that azure-core's RequestsTransport touches."""

    enforce_content_length = True
    closed = True

    def __init__(self, content: bytes = b"") -> None:
        self._content = content

    def read(self, *a: Any, **k: Any) -> bytes:
        content, self._content = self._content, b""
        return content

    def stream(self, *a: Any, **k: Any) -> Any:
        return iter([self._content] if self._content else [])

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class FakeSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self.routes: list[tuple[str, str, Callable[[dict[str, Any]], tuple[int, Any, dict[str, str]]]]] = []

    def add(self, method: str, url_part: str, status: int = 200, body: Any = None,
            headers: dict[str, str] | None = None, times: int | None = None) -> None:
        state = {"n": 0}

        def handler(call: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
            state["n"] += 1
            return status, body, headers or {}

        self.routes.insert(0, (method.upper(), url_part, handler))
        if times is not None:
            orig = self.routes[0]

            def limited(call: dict[str, Any], _h=handler, _orig=orig) -> tuple[int, Any, dict[str, str]]:
                if state["n"] >= times:
                    self.routes.remove(wrapped)
                    return self._dispatch(call)
                return _h(call)

            wrapped = (method.upper(), url_part, limited)
            self.routes[0] = wrapped

    def _dispatch(self, call: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
        for method, part, handler in self.routes:
            if method == call["method"] and part in call["url"]:
                return handler(call)
        return 404, {"error": {"code": "NotFound", "message": f"no fake route for {call['method']} {call['url']}"}}, {}

    def request(self, method: str, url: str, params: Any = None, data: Any = None, headers: Any = None,
                timeout: Any = None, **kw: Any) -> requests.Response:  # type: ignore[override]
        call = {"method": method.upper(), "url": url, "params": dict(params or {}), "headers": dict(headers or {}),
                "json": json.loads(data) if data else None}
        self.calls.append(call)
        status, body, hdrs = self._dispatch(call)
        resp = requests.Response()
        resp.status_code = status
        resp._content = (json.dumps(body) if not isinstance(body, str) else body).encode() if body is not None else b""
        resp.headers.update({"Content-Type": "application/json", **hdrs})
        resp.url = url
        resp.reason = {200: "OK", 201: "Created", 204: "No Content"}.get(status, "Error")
        resp.raw = _Raw(resp._content)
        return resp


def make_client(session: FakeSession, **kw: Any) -> FinOpsClient:
    client = FinOpsClient(StaticTokenCredential("t0k3n"), "sub-1", resource_group="rg", session=session,
                          retry=RetryPolicy(total=2, backoff_factor=0), **kw)
    client._transport.sleep = lambda s: None
    return client
