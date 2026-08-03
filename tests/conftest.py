"""Shared pytest fixtures for the scaffold and shared-client branches."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.helpers.fake_cursor_sdk import FakeCursorSDK  # noqa: E402

from hermes_cursor_sdk.client import CursorSDKClient  # noqa: E402
from hermes_cursor_sdk.config import Settings  # noqa: E402


@pytest.fixture
def tmp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key")
    monkeypatch.setenv("HERMES_CURSOR_API_KEY", "cursor_test_key")
    monkeypatch.setenv("HERMES_CURSOR_BRIDGE_TOKEN", "test-bridge-token")
    monkeypatch.setattr("hermes_cursor_sdk.config.CONFIG_PATH", tmp_path / "missing.toml")
    return Settings(
        api_key="cursor_test_key",
        store_dir=tmp_path / "store",
        bridge_token="test-bridge-token",
        bridge_cwd=tmp_path,
        bridge_port=0,
    )


@pytest.fixture
def fake_sdk() -> FakeCursorSDK:
    return FakeCursorSDK(response_text="fake response")


@pytest.fixture
def client(tmp_settings: Settings, fake_sdk: FakeCursorSDK) -> CursorSDKClient:
    return CursorSDKClient(settings=tmp_settings, sdk=fake_sdk)
