from __future__ import annotations

from importlib import metadata

import pytest


@pytest.mark.contract
def test_plugin_entry_point_loads_register() -> None:
    entry_points = metadata.entry_points(group="hermes_agent.plugins")
    cursor = next(entry_point for entry_point in entry_points if entry_point.name == "cursor")

    plugin_module = cursor.load()

    assert hasattr(plugin_module, "register")
    assert callable(plugin_module.register)


@pytest.mark.contract
def test_console_scripts_metadata() -> None:
    scripts = {
        entry_point.name: entry_point.value
        for entry_point in metadata.entry_points(group="console_scripts")
        if entry_point.name in {"hermes-cursor", "hermes-cursor-bridge"}
    }

    assert scripts == {
        "hermes-cursor": "hermes_cursor_sdk.cli:main",
        "hermes-cursor-bridge": "hermes_cursor_sdk.bridge.server:main",
    }
