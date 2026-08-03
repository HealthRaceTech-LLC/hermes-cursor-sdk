from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.helpers.http_client import request_json

from hermes_cursor_sdk.bridge.server import BridgeHTTPServer
from hermes_cursor_sdk.config import Settings


class SessionFakeClient:
    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.sent: list[str] = []

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "composer-2.5"}]

    def session_ensure_local(self, session_key: str, **_kwargs: Any) -> str:
        self.ensured.append(session_key)
        return f"agent-{session_key}"

    def session_send(self, session_key: str, prompt: str, **_kwargs: Any) -> dict[str, Any]:
        self.sent.append(session_key)
        return {
            "ok": True,
            "agent_id": f"agent-{session_key}",
            "result_text": f"{session_key}:{prompt}",
        }


@pytest.fixture
def session_bridge(tmp_path: Path) -> Iterator[tuple[str, SessionFakeClient]]:
    client = SessionFakeClient()
    settings = Settings(
        api_key="cursor-key",
        bridge_token="bridge-token",
        bridge_port=0,
        bridge_cwd=tmp_path,
        store_dir=tmp_path / "store",
        gateway_max_sessions=2,
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


@pytest.mark.integration
def test_session_isolation(session_bridge: tuple[str, SessionFakeClient]) -> None:
    base_url, client = session_bridge

    for session_id in ("session-a", "session-b"):
        status, _headers, body = request_json(
            base_url,
            "POST",
            "/v1/chat/completions",
            token="bridge-token",
            payload={
                "model": "composer-2.5",
                "messages": [{"role": "user", "content": f"hello {session_id}"}],
                "cursor": {"session_id": session_id, "cwd": "/tmp/work"},
            },
        )
        assert status == 200
        assert session_id in body

    assert client.ensured == ["session-a", "session-b"]
    assert client.sent == ["session-a", "session-b"]
