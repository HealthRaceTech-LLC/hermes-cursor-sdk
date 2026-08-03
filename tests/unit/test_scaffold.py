from __future__ import annotations

from hermes_cursor_sdk import __version__
from hermes_cursor_sdk.bridge.server import main as bridge_main
from hermes_cursor_sdk.cli import main as cli_main


def test_version_export_is_available() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_cli_help_exits_zero(capsys) -> None:
    assert cli_main(["--help"]) == 0
    assert "Hermes Cursor SDK CLI placeholder" in capsys.readouterr().out


def test_bridge_help_exits_zero(capsys) -> None:
    assert bridge_main(["--help"]) == 0
    assert "Hermes Cursor SDK bridge placeholder" in capsys.readouterr().out
