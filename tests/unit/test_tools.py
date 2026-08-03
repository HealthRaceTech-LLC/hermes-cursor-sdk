from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from hermes_cursor_sdk import tools
from hermes_cursor_sdk.config import Settings
from hermes_cursor_sdk.results import ok_result


class ToolClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((name, kwargs))
        return ok_result(status="finished", result_text=name, metadata={"kwargs": kwargs})

    def list_models(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("list_models", **kwargs)

    def list_repositories(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("list_repositories", **kwargs)

    def run_local(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("run_local", **kwargs)

    def start_cloud(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("start_cloud", **kwargs)

    def status(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("status", **kwargs)

    def resume_and_send(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("resume_and_send", **kwargs)

    def cancel(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("cancel", **kwargs)

    def session_send(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("session_send", **kwargs)

    def manage_agent(self, **kwargs: Any) -> dict[str, Any]:
        return self._record("manage_agent", **kwargs)


def parse(handler: Callable[..., str], args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    payload = json.loads(handler(args, **kwargs))
    assert isinstance(payload, dict)
    return payload


@pytest.fixture
def tool_client(monkeypatch: pytest.MonkeyPatch) -> ToolClient:
    client = ToolClient()
    monkeypatch.setattr(tools, "_CLIENT", client)
    return client


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        ("cursor_models", {}, {}),
        ("cursor_repositories", {}, {}),
        ("cursor_run", {"prompt": "hi", "cwd": "/tmp"}, {}),
        (
            "cursor_start",
            {"prompt": "hi", "repos": [{"url": "git@example.com:repo-1.git"}]},
            {"session_id": "s1"},
        ),
        ("cursor_status", {"agent_id": "agent-1"}, {}),
        ("cursor_resume", {"agent_id": "agent-1", "prompt": "continue"}, {}),
        ("cursor_cancel", {"agent_id": "agent-1"}, {}),
        ("cursor_session_send", {"prompt": "hi", "cwd": "/tmp", "session_tag": "tag"}, {}),
        ("cursor_agent", {"action": "list"}, {}),
    ],
)
def test_all_handlers_return_parseable_json(
    tool_client: ToolClient,
    name: str,
    args: dict[str, Any],
    kwargs: dict[str, Any],
) -> None:
    payload = parse(tools.HANDLERS[name], args, **kwargs)

    assert payload["ok"] is True
    assert payload["result_text"] == tool_client.calls[-1][0]


def test_missing_prompt_returns_invalid_args(tool_client: ToolClient) -> None:
    payload = parse(tools.cursor_run, {"cwd": "/tmp"})

    assert payload["ok"] is False
    assert payload["code"] == "invalid_args"
    assert payload["error"]["message"] == "prompt is required."


def test_session_send_without_session_ids_or_tag_returns_invalid_args(
    tool_client: ToolClient,
) -> None:
    payload = parse(tools.cursor_session_send, {"prompt": "hi", "cwd": "/tmp"})

    assert payload["ok"] is False
    assert payload["code"] == "invalid_args"
    assert "session id" in payload["error"]["message"]


def test_handlers_pass_payload_to_monkeypatched_client(tool_client: ToolClient) -> None:
    payload = parse(
        tools.cursor_start,
        {
            "prompt": "ship it",
            "repos": [{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
            "model": "composer-2.5",
            "params": {"reasoning_effort": "high"},
            "wait": False,
        },
        task_id="task-1",
    )

    assert payload["ok"] is True
    call_name, kwargs = tool_client.calls[-1]
    assert call_name == "start_cloud"
    assert kwargs["prompt"] == "ship it"
    assert "runtime" not in kwargs
    assert kwargs["idempotency_key"].startswith("cursor-start-")


def test_cursor_start_defaults_repo_starting_ref(tool_client: ToolClient) -> None:
    payload = parse(
        tools.cursor_start,
        {"prompt": "ship it", "repos": [{"url": "git@example.com:repo-1.git"}]},
    )

    assert payload["ok"] is True
    _call_name, kwargs = tool_client.calls[-1]
    assert kwargs["repos"] == [{"url": "git@example.com:repo-1.git", "starting_ref": "main"}]


def test_cursor_start_passes_all_optional_fields(tool_client: ToolClient) -> None:
    payload = parse(
        tools.cursor_start,
        {
            "prompt": "ship it",
            "repos": [{"url": "git@example.com:repo-1.git", "starting_ref": "dev"}],
            "mode": "plan",
            "model": "composer-2.5",
            "correlation_id": "corr-1",
            "params": {"reasoning_effort": "high"},
            "env_names": ["SAFE_ENV"],
            "auto_create_pr": True,
            "skip_reviewer_request": False,
            "wait": True,
            "idempotency_key": "idem-1",
        },
    )

    assert payload["ok"] is True
    _call_name, kwargs = tool_client.calls[-1]
    assert kwargs["mode"] == "plan"
    assert kwargs["metadata"] == {"correlation_id": "corr-1"}
    assert kwargs["env_names"] == ["SAFE_ENV"]
    assert kwargs["auto_create_pr"] is True
    assert kwargs["skip_reviewer_request"] is False
    assert kwargs["wait"] is True
    assert kwargs["idempotency_key"] == "idem-1"


def test_handlers_pass_optional_payloads(tool_client: ToolClient) -> None:
    assert parse(tools.cursor_models, {"refresh": True})["ok"] is True
    assert parse(tools.cursor_repositories, {"refresh": False})["ok"] is True
    assert (
        parse(
            tools.cursor_status,
            {"agent_id": "agent-1", "run_id": "run-1"},
        )["ok"]
        is True
    )
    assert (
        parse(
            tools.cursor_resume,
            {"agent_id": "agent-1", "prompt": "continue", "cwd": "/tmp", "force": True},
        )["ok"]
        is True
    )
    assert (
        parse(
            tools.cursor_cancel,
            {"agent_id": "agent-1", "run_id": "run-1"},
        )["ok"]
        is True
    )
    assert (
        parse(
            tools.cursor_session_send,
            {
                "agent_id": "agent-1",
                "prompt": "continue",
                "session_tag": "tag",
                "params": {"max_tokens": 1},
                "force": True,
                "close": True,
            },
        )["ok"]
        is True
    )


@pytest.mark.parametrize(
    ("handler", "args", "message"),
    [
        (
            tools.cursor_start,
            {"prompt": "hi", "repos": [{"url": "git@example.com:repo-1.git"}], "mode": "debug"},
            "mode must be one of",
        ),
        (tools.cursor_models, {"refresh": "true"}, "refresh must be a boolean"),
        (
            tools.cursor_run,
            {"prompt": "hi", "cwd": "/tmp", "params": "bad"},
            "params must be an object",
        ),
        (
            tools.cursor_start,
            {"prompt": "hi", "repos": [{"url": "git@example.com:repo-1.git", "pr_url": ""}]},
            "repo.pr_url must be a non-empty string",
        ),
        (tools.cursor_start, {"prompt": "hi", "repos": []}, "repos is required"),
        (tools.cursor_start, {"prompt": "hi", "repos": ["bad"]}, "Each repo must be an object"),
        (tools.cursor_start, {"prompt": "hi", "repos": [{"url": ""}]}, "repo.url is required"),
        (
            tools.cursor_start,
            {"prompt": "hi", "repos": [{"url": "git@example.com:repo.git"}], "env_names": [""]},
            "env_names must be a list",
        ),
        (tools.cursor_agent, {"action": "explode"}, "action is invalid"),
        (tools.cursor_agent, {"action": "archive"}, "agent_id is required"),
    ],
)
def test_handlers_validate_invalid_inputs(
    tool_client: ToolClient,
    handler: Callable[..., str],
    args: dict[str, Any],
    message: str,
) -> None:
    payload = parse(handler, args)

    assert payload["ok"] is False
    assert payload["code"] == "invalid_args"
    assert message in payload["error"]["message"]


def test_cursor_agent_delete_confirm_mismatch(tool_client: ToolClient) -> None:
    payload = parse(
        tools.cursor_agent,
        {"action": "delete", "agent_id": "agent-1", "confirm_agent_id": "other"},
    )

    assert payload["ok"] is False
    assert payload["code"] == "invalid_args"
    assert "confirm_agent_id" in payload["error"]["message"]


def test_cursor_agent_archive_requires_confirmation(tool_client: ToolClient) -> None:
    payload = parse(tools.cursor_agent, {"action": "archive", "agent_id": "agent-1"})

    assert payload["ok"] is False
    assert payload["code"] == "invalid_args"
    assert "confirm_agent_id" in payload["error"]["message"]


def test_cursor_agent_delete_confirm_match(tool_client: ToolClient) -> None:
    payload = parse(
        tools.cursor_agent,
        {
            "action": "delete",
            "agent_id": "agent-1",
            "confirm_agent_id": "agent-1",
            "runtime": "cloud",
        },
    )

    assert payload["ok"] is True
    _call_name, kwargs = tool_client.calls[-1]
    assert kwargs["confirm_agent_id"] == "agent-1"
    assert kwargs["runtime"] == "cloud"


def test_session_send_allows_stored_session_without_cwd(tool_client: ToolClient) -> None:
    class Store:
        def get_session(self, session_key: str) -> dict[str, str] | None:
            assert session_key == "hermes:s1:-"
            return {"agent_id": "agent-1"}

    tool_client.store = Store()  # type: ignore[attr-defined]

    payload = parse(tools.cursor_session_send, {"prompt": "continue"}, session_id="s1")

    assert payload["ok"] is True
    call_name, kwargs = tool_client.calls[-1]
    assert call_name == "session_send"
    assert "cwd" not in kwargs


def test_json_ok_error_compatibility_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tools._json('{"ok":true}') == '{"ok":true}'
    assert json.loads(tools._json("plain")) == "plain"

    def legacy_ok(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if args:
            raise TypeError("keyword only")
        return {"ok": True, **kwargs}

    def legacy_error(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if args or kwargs.get("details") is not None:
            raise TypeError("old signature")
        return {"ok": False, "error": dict(kwargs)}

    monkeypatch.setattr(tools, "ok_result", legacy_ok)
    monkeypatch.setattr(tools, "error_result", legacy_error)

    assert tools._ok("text") == {"ok": True, "result_text": "text"}
    assert tools._ok(["value"]) == {"ok": True, "metadata": {"data": ["value"]}}
    assert tools._error("bad", "message") == {
        "ok": False,
        "error": {"code": "bad", "message": "message", "details": None},
    }


def test_exception_error_handles_non_dict_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "map_exception", lambda exc: "mapped message")

    result = tools._exception_error(RuntimeError("boom"))

    assert result["ok"] is False
    assert result["error"]["code"] == "RuntimeError"


def test_client_factory_reports_missing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "CursorSDKClient", None)
    tools._CLIENT = None

    with pytest.raises(RuntimeError, match="not available"):
        tools._client()


def test_list_tools_attaches_handlers() -> None:
    listed = tools.list_tools()

    assert {tool["name"] for tool in listed} == set(tools.HANDLERS)
    assert all(callable(tool["handler"]) for tool in listed)


def test_client_factory_constructs_and_caches_with_positional_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []

    class PositionalClient:
        def __init__(self, settings: Settings | None = None, **kwargs: Any) -> None:
            if kwargs:
                raise TypeError("positional only")
            created.append(settings)

    monkeypatch.setattr(tools, "CursorSDKClient", PositionalClient)
    monkeypatch.setattr(tools, "load_settings", lambda: Settings(api_key="cursor-key"))
    tools._CLIENT = None

    first = tools._client()
    second = tools._client()

    assert first is second
    assert len(created) == 1


def test_cursor_api_key_available_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(tools, "load_settings", lambda: Settings(api_key="configured"))

    assert tools.cursor_api_key_available() is True


def test_cursor_api_key_available_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> Settings:
        raise RuntimeError("bad config")

    monkeypatch.setattr(tools, "load_settings", fail)
    monkeypatch.setenv("CURSOR_API_KEY", "env-key")

    assert tools.cursor_api_key_available() is True
