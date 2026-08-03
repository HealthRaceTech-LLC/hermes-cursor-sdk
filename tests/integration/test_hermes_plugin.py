from __future__ import annotations

import json

import pytest
from tests.helpers.fake_hermes import PluginContext

from hermes_cursor_sdk import tools
from hermes_cursor_sdk.plugin import register
from hermes_cursor_sdk.results import ok_result


class FakeToolClient:
    def list_models(self) -> dict[str, object]:
        return ok_result(metadata={"models": []})


@pytest.mark.integration
def test_register_with_fake_plugin_context(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = PluginContext()
    monkeypatch.setattr(tools, "_CLIENT", FakeToolClient())

    register(ctx)

    assert len(ctx.tools) == 9
    assert ctx.skills == [{"name": "cursor-sdk", "path": ctx.skills[0]["path"]}]

    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    first_tool = ctx.tools[0]
    assert first_tool["name"] == "cursor_models"
    assert first_tool["check_fn"]() is True
    assert json.loads(first_tool["handler"]({}))["ok"] is True
