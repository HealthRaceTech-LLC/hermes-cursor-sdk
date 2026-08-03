from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cursor_sdk import cli, config


@pytest.mark.integration
def test_provider_install_via_cli_copies_shim_into_temp_hermes_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hermes_home = tmp_path / "hermes-home"
    destination = hermes_home / "plugins" / "model-providers" / "cursor"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(cli, "PLUGIN_DIR", destination)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    assert cli.main(["provider", "install"]) == 0

    assert (destination / "__init__.py").exists()
    assert (destination / "plugin.yaml").exists()
    assert (destination / cli.PLUGIN_MARKER).exists()
    assert "installed provider shim" in capsys.readouterr().out
