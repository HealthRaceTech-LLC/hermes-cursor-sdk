from __future__ import annotations

from pathlib import Path

import pytest
from tests.helpers.fake_cursor_sdk import FakeCursorSDK, FakeRun

from hermes_cursor_sdk import client as client_module
from hermes_cursor_sdk.client import CursorSDKClient
from hermes_cursor_sdk.config import Settings


def test_list_models(client: CursorSDKClient) -> None:
    models = client.list_models()

    assert models[0]["id"] == "composer-2.5"
    assert models[0]["bridge_context_length"] == client.settings.bridge_context_length


def test_run_local_ok(client: CursorSDKClient, tmp_path: Path, fake_sdk: FakeCursorSDK) -> None:
    result = client.run_local(prompt="say hello", cwd=tmp_path)

    assert result["ok"] is True
    assert result["runtime"] == "local"
    assert result["status"] == "finished"
    assert result["result_text"] == "fake response"
    assert result["run_id"] in fake_sdk.runs
    assert any(call["method"] == "CursorClient.launch_bridge" for call in fake_sdk.calls)


def test_start_cloud_returns_running_without_wait(client: CursorSDKClient) -> None:
    result = client.start_cloud(
        prompt="work in cloud",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=False,
    )

    assert result["ok"] is True
    assert result["runtime"] == "cloud"
    assert result["status"] == "running"
    assert result["agent_id"] == "bc-1"


def test_start_cloud_wait_finishes(client: CursorSDKClient) -> None:
    result = client.start_cloud(
        prompt="work in cloud",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=True,
    )

    assert result["ok"] is True
    assert result["status"] == "finished"


def test_start_cloud_idempotency_hit(client: CursorSDKClient, fake_sdk: FakeCursorSDK) -> None:
    first = client.start_cloud(
        prompt="work once",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=False,
        idempotency_key="same-operation",
    )
    second = client.start_cloud(
        prompt="work twice",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=False,
        idempotency_key="same-operation",
    )

    assert second["agent_id"] == first["agent_id"]
    assert second["run_id"] == first["run_id"]
    assert second["result_text"] == first["result_text"]
    assert [call["method"] for call in fake_sdk.calls].count("Agent.create") == 1


def test_start_cloud_rejects_unknown_env_name(tmp_path: Path, fake_sdk: FakeCursorSDK) -> None:
    client = CursorSDKClient(
        Settings(
            api_key="cursor-key",
            store_dir=tmp_path / "store",
            allowed_cloud_env_names=["SAFE_ENV"],
        ),
        sdk=fake_sdk,
    )

    result = client.start_cloud(
        prompt="env",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        env_names=["SECRET_ENV"],
    )

    assert result["ok"] is False
    assert result["code"] == "invalid_args"


def test_cancel_latest_run(client: CursorSDKClient) -> None:
    started = client.start_cloud(
        prompt="cancel me",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=False,
    )

    result = client.cancel(agent_id=str(started["agent_id"]))

    assert result["ok"] is True
    assert result["status"] == "cancelled"
    assert client.store.get_run(str(started["run_id"]))["status"] == "cancelled"  # type: ignore[index]


def test_session_ensure_and_send(client: CursorSDKClient, tmp_path: Path) -> None:
    agent_id = client.session_ensure_local(cwd=tmp_path, session_key="session-a")
    again = client.session_ensure_local(cwd=tmp_path, session_key="session-a")
    result = client.session_send(session_key="session-a", prompt="continue", cwd=tmp_path)

    assert agent_id == again
    assert result["ok"] is True
    assert result["agent_id"] == agent_id
    assert result["status"] == "finished"


def test_session_send_close_deletes_session(client: CursorSDKClient, tmp_path: Path) -> None:
    agent_id = client.session_ensure_local(cwd=tmp_path, session_key="session-close")

    result = client.session_send(
        session_key="session-close",
        prompt="finish",
        cwd=tmp_path,
        close=True,
    )

    assert result["ok"] is True
    assert result["agent_id"] == agent_id
    assert client.store.get_session("session-close") is None


def test_denied_cwd_returns_invalid_args(client: CursorSDKClient) -> None:
    result = client.run_local(prompt="nope", cwd=Path.home() / ".ssh")

    assert result["ok"] is False
    assert result["code"] == "invalid_args"


def test_invalid_repo_returns_invalid_repository(client: CursorSDKClient) -> None:
    result = client.start_cloud(prompt="bad repo", repos=["missing"], wait=False)

    assert result["ok"] is False
    assert result["code"] == "invalid_repository"


def test_manage_agent_delete_requires_confirmation(client: CursorSDKClient) -> None:
    started = client.start_cloud(
        prompt="delete me",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=False,
    )
    agent_id = str(started["agent_id"])

    denied = client.manage_agent(action="delete", agent_id=agent_id)
    deleted = client.manage_agent(action="delete", agent_id=agent_id, confirm_agent_id=agent_id)

    assert denied["ok"] is False
    assert denied["code"] == "invalid_args"
    assert deleted["ok"] is True
    assert deleted["status"] == "delete"


def test_status_resume_and_manage_agent_actions(client: CursorSDKClient) -> None:
    started = client.start_cloud(
        prompt="inspect me",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=True,
    )
    agent_id = str(started["agent_id"])
    run_id = str(started["run_id"])

    agent_status = client.status(agent_id=agent_id)
    run_status = client.status(agent_id=agent_id, run_id=run_id)
    resumed = client.resume_and_send(agent_id=agent_id, prompt="continue")
    listed = client.manage_agent(action="list", runtime="cloud")
    fetched = client.manage_agent(action="get", agent_id=agent_id)
    archived = client.manage_agent(action="archive", agent_id=agent_id, confirm_agent_id=agent_id)

    assert agent_status["ok"] is True
    assert agent_status["metadata"]["agent"]["status"] == "ready"  # type: ignore[index]
    assert run_status["ok"] is True
    assert run_status["run_id"] == run_id
    assert resumed["ok"] is True
    assert listed["ok"] is True
    assert len(listed["metadata"]["agents"]) == 1  # type: ignore[index]
    assert fetched["ok"] is True
    assert archived["ok"] is True
    assert archived["status"] == "archive"


def test_cancel_unsupported_path(client: CursorSDKClient, fake_sdk: FakeCursorSDK) -> None:
    started = client.start_cloud(
        prompt="cannot cancel",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=False,
    )
    fake_sdk.runs[str(started["run_id"])].cancel = None

    result = client.cancel(agent_id=str(started["agent_id"]))

    assert result["ok"] is False
    assert result["code"] == "unsupported"


def test_wait_falls_back_when_timeout_argument_is_unsupported(
    client: CursorSDKClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def wait_without_timeout(self: FakeRun) -> FakeRun:
        self.sdk.calls.append({"method": "run.wait.no_timeout", "run_id": self.id})
        self.status = "finished"
        return self

    monkeypatch.setattr(FakeRun, "wait", wait_without_timeout)

    result = client.start_cloud(
        prompt="wait without timeout",
        repos=[{"url": "git@example.com:repo-1.git", "starting_ref": "main"}],
        wait=True,
    )

    assert result["ok"] is True
    assert result["status"] == "finished"


def test_missing_api_key_maps_to_auth_missing(tmp_path: Path, fake_sdk: FakeCursorSDK) -> None:
    client = CursorSDKClient(Settings(api_key=None, store_dir=tmp_path / "store"), sdk=fake_sdk)

    result = client.run_local(prompt="hello", cwd=tmp_path)

    assert result["ok"] is False
    assert result["code"] == "auth_missing"


def test_client_helper_item_and_list_fallbacks() -> None:
    class PositionalResource:
        def list(self, *args: object, **kwargs: object) -> list[str]:
            if kwargs:
                raise TypeError("no kwargs")
            assert args == ({"api_key": "cursor-key"},)
            return ["positional"]

    class NoArgResource:
        def list(self, *args: object, **kwargs: object) -> list[str]:
            if args or kwargs:
                raise TypeError("no args")
            return ["none"]

    assert client_module._items(None) == []
    assert client_module._items(("a", "b")) == ["a", "b"]
    assert client_module._items({"items": ["nested"]}) == ["nested"]
    assert client_module._items("single") == ["single"]
    assert client_module._call_list(PositionalResource(), "cursor-key") == ["positional"]
    assert client_module._call_list(NoArgResource(), "cursor-key") == ["none"]


def test_sdk_constructor_and_method_fallbacks(
    tmp_path: Path,
    fake_sdk: FakeCursorSDK,
) -> None:
    settings = Settings(
        api_key="cursor-key",
        store_dir=tmp_path / "store",
        bridge_cwd=tmp_path,
        local_setting_sources=["user"],
    )
    client = CursorSDKClient(settings=settings, sdk=fake_sdk)

    assert client._construct("MissingClass", {"a": 1}) == {"a": 1}

    class KeywordFiltered:
        def __init__(self, keep: str) -> None:
            self.keep = keep

    fake_sdk.KeywordFiltered = KeywordFiltered
    filtered = client._construct("KeywordFiltered", {"keep": "yes", "drop": "no"})
    assert filtered.keep == "yes"

    local_options = client._agent_options(
        api_key="cursor-key",
        model={"id": "composer-2.5"},
        runtime="local",
        cwd=tmp_path,
    )
    assert local_options.local.setting_sources == ["user"]

    cloud_options = client._agent_options(
        api_key="cursor-key",
        model={"id": "composer-2.5"},
        runtime="cloud",
        repos=[{"url": "git@example.com:repo-1.git"}],
        env_names=["SAFE_ENV"],
    )
    assert cloud_options.cloud.env_names == ["SAFE_ENV"]


def test_launch_bridge_and_agent_typeerror_fallbacks(tmp_path: Path) -> None:
    calls: list[str] = []

    class Bridge:
        def close(self) -> None:
            calls.append("bridge.close")

    class CursorClientNamespace:
        def launch_bridge(self, workspace: str) -> Bridge:
            calls.append(f"launch:{workspace}")
            return Bridge()

    class Run:
        id = "run-1"
        agent_id = "agent-1"
        status = "running"

    class AgentInstance:
        id = "agent-1"

        def send(self, prompt: str) -> Run:
            calls.append(f"send:{prompt}")
            return Run()

        def close(self) -> None:
            calls.append("agent.close")

    class AgentNamespace:
        def prompt(self, prompt: str, options: object) -> Run:
            calls.append(f"prompt:{prompt}")
            return Run()

        def create(self, options: object) -> AgentInstance:
            calls.append("create")
            return AgentInstance()

        def resume(self, agent_id: str, options: object) -> AgentInstance:
            calls.append(f"resume:{agent_id}")
            return AgentInstance()

    sdk = type(
        "SDK",
        (),
        {"CursorClient": CursorClientNamespace(), "Agent": AgentNamespace()},
    )()
    client = CursorSDKClient(
        settings=Settings(api_key="cursor-key", store_dir=tmp_path / "store"),
        sdk=sdk,
    )

    with client._launch_bridge(tmp_path, "cursor-key") as bridge:
        assert isinstance(bridge, Bridge)
    assert "bridge.close" in calls
    assert client._agent_prompt("hi", {}, None).id == "run-1"
    agent = client._agent_create({})
    assert agent.id == "agent-1"
    assert client._agent_resume("agent-1", {}).id == "agent-1"
    assert client._agent_send(agent, "hello", mode="agent").id == "run-1"


def test_wait_and_result_error_fallbacks(tmp_path: Path, fake_sdk: FakeCursorSDK) -> None:
    client = CursorSDKClient(
        settings=Settings(api_key="cursor-key", store_dir=tmp_path / "store"),
        sdk=fake_sdk,
    )
    run = type("Run", (), {"id": "run-1", "agent_id": "agent-1", "status": "failed"})()

    assert client._wait(object()) is not None
    result = client._result_from_run(run, runtime="cloud")

    assert result["ok"] is False
    assert result["code"] == "run_failed"
