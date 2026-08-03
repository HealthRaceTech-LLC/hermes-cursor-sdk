"""HTTP test helper placeholders."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import httpx


def json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=payload)


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: Any | None = None,
    accept_sse: bool = False,
) -> tuple[int, dict[str, str], str]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "text/event-stream" if accept_sse else "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers.items()), response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read().decode("utf-8")
