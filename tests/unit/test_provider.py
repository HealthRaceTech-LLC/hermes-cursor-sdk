from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from hermes_cursor_sdk import provider
from hermes_cursor_sdk.provider import CursorProfile, register_cursor_provider


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_build_extra_body_shape() -> None:
    profile = CursorProfile()

    extra = profile.build_extra_body(
        session_id="session-1",
        cwd="/tmp/work",
        params={"reasoning_effort": "high"},
        temperature=0.2,
    )

    assert extra == {
        "cursor": {
            "session_id": "session-1",
            "cwd": "/tmp/work",
            "params": {"reasoning_effort": "high", "temperature": 0.2},
        }
    }


def test_build_extra_body_rejects_non_mapping_params() -> None:
    with pytest.raises(TypeError, match="params"):
        CursorProfile().build_extra_body(params=["bad"])


def test_fetch_models_with_urllib_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return FakeResponse({"data": [{"id": "composer-2.5"}, {"id": "gpt-5"}]})

    monkeypatch.setattr(provider, "urlopen", fake_urlopen)

    models = CursorProfile().fetch_models("http://bridge.test/v1", token="bridge-token", timeout=3)

    assert models == [{"id": "composer-2.5"}, {"id": "gpt-5"}]
    assert captured["url"] == "http://bridge.test/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer bridge-token"
    assert captured["timeout"] == 3


def test_fetch_models_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BRIDGE_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="BRIDGE_TOKEN"):
        CursorProfile().fetch_models("http://bridge.test/v1", token=None)


def test_fetch_models_rejects_bad_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provider, "urlopen", lambda *_args, **_kwargs: FakeResponse({"data": "bad"})
    )

    with pytest.raises(RuntimeError, match="data array"):
        CursorProfile().fetch_models("http://bridge.test/v1", token="token")


@pytest.mark.parametrize(
    ("exc", "message"),
    [
        (HTTPError("http://bridge.test/v1/models", 500, "boom", {}, None), "HTTP 500"),
        (URLError("offline"), "model fetch failed"),
        (json.JSONDecodeError("bad", "not-json", 0), "model fetch failed"),
    ],
)
def test_fetch_models_maps_url_errors(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    message: str,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(provider, "urlopen", fail)

    with pytest.raises(RuntimeError, match=message):
        CursorProfile().fetch_models("http://bridge.test/v1", token="token")


def test_register_cursor_provider_returns_cursor_profile() -> None:
    registered = register_cursor_provider()

    assert registered.name == "cursor"
    assert "cursor-sdk" in registered.aliases
    assert registered.display_name == "Cursor (SDK bridge)"


def test_resolve_bridge_base_url_respects_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BASE_URL", raising=False)
    monkeypatch.setenv("HERMES_CURSOR_BRIDGE_HOST", "127.0.0.1")
    monkeypatch.setenv("HERMES_CURSOR_BRIDGE_PORT", "9999")

    assert provider.resolve_bridge_base_url() == "http://127.0.0.1:9999/v1"
    assert CursorProfile().base_url == "http://127.0.0.1:9999/v1"
