from __future__ import annotations

import importlib

import pytest
from tests.helpers.fake_cursor_sdk import FakeCursorSDK


@pytest.mark.contract
def test_cursor_sdk_exposes_required_surface() -> None:
    cursor_sdk = pytest.importorskip("cursor_sdk")

    assert hasattr(cursor_sdk, "Agent")
    assert hasattr(cursor_sdk, "Cursor")


@pytest.mark.contract
def test_fake_cursor_sdk_has_required_methods() -> None:
    sdk = FakeCursorSDK()

    assert hasattr(sdk, "Agent")
    assert hasattr(sdk.Agent, "create")
    assert hasattr(sdk.Agent, "prompt")
    assert hasattr(sdk.Agent, "resume")
    assert hasattr(sdk.Cursor, "models")
    assert hasattr(sdk.Cursor.models, "list")
    assert hasattr(sdk.Cursor, "repositories")
    assert hasattr(sdk.Cursor.repositories, "list")
    assert hasattr(sdk.CursorClient, "launch_bridge")
    assert importlib.import_module("tests.helpers.fake_cursor_sdk").FakeCursorSDK is FakeCursorSDK
