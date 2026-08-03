from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from tests.helpers.http_client import request_json

from hermes_cursor_sdk.bridge.server import BridgeHTTPServer
from hermes_cursor_sdk.config import Settings

SEEDED_USAGE = {
    "input_tokens": 1000,
    "cache_read_tokens": 200,
    "cache_write_tokens": 0,
    "output_tokens": 50,
    "total_tokens": 1250,
}


class BridgeFakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def list_models(self) -> list[dict[str, Any]]:
        self.calls.append(("list_models", {}))
        return [{"id": "composer-2.5"}, {"id": "gpt-5"}]

    def chat_completions(self, **kwargs: Any) -> Any:
        self.calls.append(("chat_completions", kwargs))
        if kwargs.get("stream"):
            return iter(
                [
                    {"result_text": "hello "},
                    {"result_text": "world", "usage": dict(SEEDED_USAGE)},
                ]
            )
        return {"ok": True, "result_text": "hello world", "usage": dict(SEEDED_USAGE)}


@pytest.fixture
def bridge_server(tmp_path: Path) -> Iterator[tuple[str, BridgeFakeClient]]:
    client = BridgeFakeClient()
    settings = Settings(
        api_key="cursor-key",
        bridge_token="bridge-token",
        bridge_port=0,
        bridge_cwd=tmp_path,
        store_dir=tmp_path / "store",
    )
    httpd = BridgeHTTPServer(("127.0.0.1", 0), settings, client_factory=lambda: client)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}", client
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def request_raw(
    base_url: str,
    body: bytes,
    *,
    token: str = "bridge-token",
) -> tuple[int, str]:
    request = Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def start_bridge(
    settings: Settings,
    client: BridgeFakeClient,
) -> Iterator[tuple[str, BridgeHTTPServer]]:
    httpd = BridgeHTTPServer(("127.0.0.1", 0), settings, client_factory=lambda: client)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    try:
        yield f"http://{host}:{port}", httpd
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


@pytest.mark.integration
def test_healthz_200(bridge_server: tuple[str, BridgeFakeClient]) -> None:
    base_url, _client = bridge_server

    status, _headers, body = request_json(base_url, "GET", "/healthz")

    assert status == 200
    assert '"status":"ok"' in body


@pytest.mark.integration
def test_models_requires_auth_and_returns_models(
    bridge_server: tuple[str, BridgeFakeClient],
) -> None:
    base_url, _client = bridge_server

    unauthorized, _headers, _body = request_json(base_url, "GET", "/v1/models")
    ok, _headers, body = request_json(base_url, "GET", "/v1/models", token="bridge-token")

    assert unauthorized == 401
    assert ok == 200
    assert "composer-2.5" in body
    payload = json.loads(body)
    assert payload["data"][0]["context_length"] == 200_000
    assert payload["data"][0]["context_source"] == "cursor_model_window"


@pytest.mark.integration
def test_chat_completions(bridge_server: tuple[str, BridgeFakeClient]) -> None:
    base_url, client = bridge_server

    status, _headers, body = request_json(
        base_url,
        "POST",
        "/v1/chat/completions",
        token="bridge-token",
        payload={"model": "composer-2.5", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert status == 200
    assert "hello world" in body
    assert client.calls[-1][0] == "chat_completions"
    payload = json.loads(body)
    assert payload["usage"]["prompt_tokens"] == 1200
    assert payload["usage"]["completion_tokens"] == 50
    assert payload["usage"]["total_tokens"] == 1250


@pytest.mark.integration
def test_chat_completions_strips_tools(bridge_server: tuple[str, BridgeFakeClient]) -> None:
    base_url, client = bridge_server

    status, _headers, body = request_json(
        base_url,
        "POST",
        "/v1/chat/completions",
        token="bridge-token",
        payload={
            "model": "composer-2.5",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
        },
    )

    assert status == 200
    assert "hello world" in body
    # Tools must not be forwarded into the Cursor client call.
    assert "tools" not in client.calls[-1][1]
    assert "tool_choice" not in client.calls[-1][1]


@pytest.mark.integration
def test_stream_sse(bridge_server: tuple[str, BridgeFakeClient]) -> None:
    base_url, _client = bridge_server

    status, headers, body = request_json(
        base_url,
        "POST",
        "/v1/chat/completions",
        token="bridge-token",
        payload={
            "model": "composer-2.5",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        accept_sse=True,
    )

    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert "data: " in body
    assert "hello " in body
    assert "world" in body
    assert "data: [DONE]" in body
    usage_events = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        event = json.loads(line.removeprefix("data: "))
        if isinstance(event, dict) and event.get("usage"):
            usage_events.append(event)
    assert usage_events, "expected trailing SSE usage chunk for Hermes meters"
    assert usage_events[-1]["choices"] == []
    assert usage_events[-1]["usage"]["prompt_tokens"] == 1200
    assert usage_events[-1]["usage"]["completion_tokens"] == 50


@pytest.mark.integration
def test_chat_completions_rejects_invalid_json(bridge_server: tuple[str, BridgeFakeClient]) -> None:
    base_url, _client = bridge_server

    status, body = request_raw(base_url, b"{")

    assert status == 400
    assert "valid JSON" in body


@pytest.mark.integration
def test_chat_completions_rejects_too_large_body(tmp_path: Path) -> None:
    client = BridgeFakeClient()
    settings = Settings(
        api_key="cursor-key",
        bridge_token="bridge-token",
        bridge_port=0,
        bridge_cwd=tmp_path,
        store_dir=tmp_path / "store",
        max_request_bytes=5,
    )

    for base_url, _httpd in start_bridge(settings, client):
        status, body = request_raw(base_url, json.dumps({"messages": []}).encode("utf-8"))

    assert status == 413
    assert "request_too_large" in body


@pytest.mark.integration
def test_session_capacity_returns_503(tmp_path: Path) -> None:
    client = BridgeFakeClient()
    settings = Settings(
        api_key="cursor-key",
        bridge_token="bridge-token",
        bridge_port=0,
        bridge_cwd=tmp_path,
        store_dir=tmp_path / "store",
        gateway_max_sessions=1,
    )

    for base_url, httpd in start_bridge(settings, client):
        held, acquired, exceeded = httpd.sessions.acquire("held")
        assert held is not None
        assert acquired is True
        assert exceeded is False
        try:
            status, _headers, body = request_json(
                base_url,
                "POST",
                "/v1/chat/completions",
                token="bridge-token",
                payload={
                    "model": "composer-2.5",
                    "messages": [{"role": "user", "content": "hi"}],
                    "cursor": {"session_id": "other", "cwd": str(tmp_path)},
                },
            )
        finally:
            httpd.sessions.release(held)

    assert status == 503
    assert "session_capacity_exceeded" in body


@pytest.mark.integration
def test_concurrent_session_returns_409(tmp_path: Path) -> None:
    client = BridgeFakeClient()
    settings = Settings(
        api_key="cursor-key",
        bridge_token="bridge-token",
        bridge_port=0,
        bridge_cwd=tmp_path,
        store_dir=tmp_path / "store",
        gateway_max_sessions=2,
    )

    for base_url, httpd in start_bridge(settings, client):
        held, acquired, exceeded = httpd.sessions.acquire("same")
        assert held is not None
        assert acquired is True
        assert exceeded is False
        try:
            status, _headers, body = request_json(
                base_url,
                "POST",
                "/v1/chat/completions",
                token="bridge-token",
                payload={
                    "model": "composer-2.5",
                    "messages": [{"role": "user", "content": "hi"}],
                    "cursor": {"session_id": "same", "cwd": str(tmp_path)},
                },
            )
        finally:
            httpd.sessions.release(held)

    assert status == 409
    assert "session_concurrent_request" in body
