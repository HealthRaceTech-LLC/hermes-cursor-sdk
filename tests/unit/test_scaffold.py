from __future__ import annotations

import pytest

from hermes_cursor_sdk import __version__
from hermes_cursor_sdk.bridge.server import main as bridge_main
from hermes_cursor_sdk.cli import main as cli_main


def assert_help_exits_zero(main) -> None:
    try:
        result = main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        assert result == 0


def test_version_export_is_available() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_cli_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert_help_exits_zero(cli_main)
    assert "show this help message and exit" in capsys.readouterr().out


def test_bridge_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert_help_exits_zero(bridge_main)
    assert "show this help message and exit" in capsys.readouterr().out
