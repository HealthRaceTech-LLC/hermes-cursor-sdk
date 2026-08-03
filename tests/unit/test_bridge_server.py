from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hermes_cursor_sdk.bridge import server
from hermes_cursor_sdk.config import Settings


def test_session_runtime_snapshot_capacity_and_eviction(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100.0
    monkeypatch.setattr(server.time, "monotonic", lambda: now)
    runtime = server.SessionRuntime(max_sessions=1, idle_timeout_seconds=1)
    state, acquired, capacity = runtime.acquire("first")
    assert state is not None
    assert acquired is True
    assert capacity is False
    assert runtime.snapshot()["active_sessions"] == 1
    runtime.release(state)

    now = 103.0
    state, acquired, capacity = runtime.acquire("second")

    assert state is not None
    assert acquired is True
    assert capacity is False
    runtime.release(state)


@pytest.mark.parametrize(
    "cursor",
    [
        [],
        {"session_id": 123},
        {"cwd": 123},
        {"params": ["bad"]},
    ],
)
def test_parse_cursor_extension_rejects_invalid_shapes(cursor: Any) -> None:
    with pytest.raises(server.BridgeError):
        server.parse_cursor_extension(cursor)


def test_parse_cursor_extension_defaults_and_request_params() -> None:
    cursor = server.parse_cursor_extension(
        {"session_id": "", "cwd": "", "params": {"effort": "high"}}
    )
    params = server.request_params({"temperature": 0.2, "ignored": True}, cursor)

    assert cursor == {"session_id": None, "cwd": None, "params": {"effort": "high"}}
    assert params == {"temperature": 0.2, "effort": "high"}


def test_send_session_ensures_then_sends() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Client:
        def session_ensure_local(self, **kwargs: Any) -> str:
            calls.append(("ensure", kwargs))
            return "agent-1"

        def session_send(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("send", kwargs))
            return {"ok": True, "result_text": "sent"}

    result = server.send_session(
        Client(),
        "session-1",
        {"model": "composer-2.5"},
        {"cwd": "/tmp/work", "params": {"max_tokens": 1}},
        [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        False,
    )

    assert result == {"ok": True, "result_text": "sent"}
    assert [name for name, _kwargs in calls] == ["ensure", "send"]
    assert calls[1][1]["prompt"] == "user: hi"
    assert calls[1][1]["wait"] is True


def test_send_session_falls_back_to_bridge_cwd_for_streaming(tmp_path: Any) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Client:
        settings = Settings(api_key="cursor-key", bridge_cwd=tmp_path)

        def session_ensure_local(self, **kwargs: Any) -> str:
            calls.append(("ensure", kwargs))
            return "agent-1"

        def session_send(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(("send", kwargs))
            return {"ok": True, "result_text": "sent"}

    server.send_session(
        Client(),
        "session-1",
        {"model": "composer-2.5"},
        {"cwd": None, "params": {}},
        [{"role": "user", "content": "hi"}],
        True,
    )

    assert calls[0][1]["cwd"] == tmp_path
    assert calls[1][1]["cwd"] == tmp_path
    assert calls[1][1]["wait"] is True


def test_send_stateless_fallbacks() -> None:
    class LocalClient:
        settings = Settings(api_key="cursor-key", bridge_cwd="/tmp/work")

        def run_local(self, **kwargs: Any) -> str:
            return f"local:{kwargs['prompt']}:{kwargs['cwd']}"

    class RunnerClient:
        def run(self, **kwargs: Any) -> str:
            return f"run:{kwargs['prompt']}:{kwargs['model']}"

    payload = {"model": "composer-2.5", "messages": [{"role": "user", "content": "hi"}]}
    cursor = {"cwd": None, "params": {}}
    messages = payload["messages"]

    assert server.send_stateless(LocalClient(), payload, cursor, messages, False).startswith(
        "local:"
    )
    assert server.send_stateless(RunnerClient(), payload, cursor, messages, False) == (
        "run:user: hi:composer-2.5"
    )
    with pytest.raises(server.BridgeError, match="does not expose"):
        server.send_stateless(object(), payload, cursor, messages, False)


def test_models_payload_falls_back_to_default_model() -> None:
    class Client:
        def list_models(self) -> list[dict[str, Any]]:
            raise RuntimeError("offline")

    payload = server.build_models_payload(Client(), Settings(default_model="fallback-model"))

    assert payload["data"][0]["id"] == "fallback-model"


def test_completion_helpers_cover_text_and_metadata_shapes() -> None:
    assert server.extract_text(None) == ""
    assert server.extract_text(b"hello") == "hello"
    assert server.extract_text({"choices": [{"delta": {"content": "delta"}}]}) == "delta"
    assert server.extract_text({"choices": [{"message": {"content": "message"}}]}) == "message"
    assert server.extract_text({"content": "content"}) == "content"
    assert server.extract_text(SimpleNamespace(text="object text")) == "object text"
    assert server.extract_metadata(SimpleNamespace(metadata={"finish_reason": "length"})) == {
        "finish_reason": "length"
    }
    assert server.extract_metadata({"metadata": {}, "usage": {"total_tokens": 1}}) == {
        "usage": {"total_tokens": 1}
    }


def test_raise_for_result_error_maps_cursor_error() -> None:
    server.raise_for_result_error({"ok": True})

    with pytest.raises(server.BridgeError) as exc:
        server.raise_for_result_error(
            {"ok": False, "error": {"message": "bad gateway", "code": "cursor_error"}}
        )

    assert exc.value.status == 502
    assert exc.value.payload()["error"]["code"] == "cursor_error"


def test_settings_from_args_and_main_help(capsys: pytest.CaptureFixture[str]) -> None:
    parser = server.build_parser()
    args = parser.parse_args(
        [
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--token",
            "token",
            "--expose",
            "--max-sessions",
            "3",
            "--idle-timeout-seconds",
            "2.5",
            "--request-size-limit",
            "123",
            "--context-length",
            "456",
            "--max-completion-tokens",
            "78",
        ]
    )

    settings = server.settings_from_args(args)
    assert settings.bridge_host == "0.0.0.0"
    assert settings.bridge_port == 9999
    assert settings.bridge_token == "token"
    assert settings.gateway_max_sessions == 3
    assert settings.gateway_idle_seconds == 2
    assert settings.max_request_bytes == 123
    assert settings.bridge_context_length == 456
    assert settings.bridge_max_output_tokens == 78

    assert server.main([]) == 0
    assert "Run the Hermes Cursor OpenAI-compatible bridge" in capsys.readouterr().out


def test_serve_http_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers: dict[int, Any] = {}
    closed: list[str] = []

    class FakeHTTPD:
        server_address = ("127.0.0.1", 8787)

        def serve_forever(self) -> None:
            handlers[server.signal.SIGTERM](server.signal.SIGTERM, None)

        def shutdown(self) -> None:
            closed.append("shutdown")

        def server_close(self) -> None:
            closed.append("server_close")

    monkeypatch.setattr(server, "create_server", lambda *_args, **_kwargs: FakeHTTPD())
    monkeypatch.setattr(server.signal, "getsignal", lambda signum: f"old-{signum}")
    monkeypatch.setattr(
        server.signal, "signal", lambda signum, handler: handlers.setdefault(signum, handler)
    )

    assert server.serve_http(Settings(api_key="cursor-key")) == 0
    assert "server_close" in closed
