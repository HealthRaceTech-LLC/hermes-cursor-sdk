from __future__ import annotations

from pathlib import Path

from tests.helpers.fake_hermes import PluginContext

from hermes_cursor_sdk.plugin import register


def test_register_captures_tools_and_skill() -> None:
    ctx = PluginContext()

    register(ctx)

    assert len(ctx.tools) == 9
    assert {tool["toolset"] for tool in ctx.tools} == {"cursor"}
    assert {tool["name"] for tool in ctx.tools} == {
        "cursor_models",
        "cursor_repositories",
        "cursor_run",
        "cursor_start",
        "cursor_status",
        "cursor_resume",
        "cursor_cancel",
        "cursor_session_send",
        "cursor_agent",
    }
    assert all(callable(tool["handler"]) for tool in ctx.tools)
    assert all(callable(tool["check_fn"]) for tool in ctx.tools)
    assert all("schema" in tool for tool in ctx.tools)
    assert ctx.skills[0]["name"] == "cursor-sdk"
    assert Path(ctx.skills[0]["path"]).exists()
