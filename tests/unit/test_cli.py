from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cursor_sdk import cli, config


@pytest.fixture
def isolated_cli_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plugin = tmp_path / ".hermes" / "plugins" / "model-providers" / "cursor"
    monkeypatch.setattr(cli, "PLUGIN_DIR", plugin)
    monkeypatch.setattr(cli, "service_path", lambda: tmp_path / "service.file")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(config, "DEFAULT_BRIDGE_ENV_PATH", tmp_path / "cursor-sdk" / "bridge.env")
    monkeypatch.setattr(cli, "DEFAULT_BRIDGE_ENV_PATH", tmp_path / "cursor-sdk" / "bridge.env")
    monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / "cursor-sdk" / "config.toml")
    monkeypatch.setattr(cli, "resolve_hermes_home", lambda profile=None: tmp_path / ".hermes")
    monkeypatch.setenv("HERMES_CURSOR_BRIDGE_TOKEN", "bridge-token")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-key")
    monkeypatch.setenv("HERMES_CURSOR_BRIDGE_CWD", str(tmp_path / "project"))
    (tmp_path / "project").mkdir()
    return tmp_path


def test_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    assert "Manage the Hermes Cursor SDK bridge" in capsys.readouterr().out


def test_status_exits_sensibly(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["status"]) == 0

    output = capsys.readouterr().out
    assert "bridge_url:" in output
    assert "hermes_home:" in output
    assert "provider: not-installed" in output
    assert "service: not-installed" in output


def test_doctor_exits_sensibly_with_env_set(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["provider", "install"]) == 0
    assert cli.main(["doctor", "--provider-mode"]) == 0

    output = capsys.readouterr().out
    assert "Hermes Cursor SDK doctor" in output
    assert "provider_name: cursor" in output
    assert "provider_display_name: Cursor (SDK bridge)" in output
    assert "status: ok" in output


def test_provider_status_exits_sensibly(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["provider", "status"]) == 0

    output = capsys.readouterr().out
    assert '"state": "not-installed"' in output


def test_provider_uninstall_not_installed(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["provider", "uninstall"]) == 0

    assert "provider shim not installed" in capsys.readouterr().out


def test_provider_uninstall_managed_provider(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.PLUGIN_DIR is not None
    cli.PLUGIN_DIR.mkdir(parents=True)
    (cli.PLUGIN_DIR / cli.PLUGIN_MARKER).write_text('{"version":"test"}', encoding="utf-8")

    assert cli.main(["provider", "uninstall"]) == 0

    assert not cli.PLUGIN_DIR.exists()
    assert "removed provider shim" in capsys.readouterr().out


def test_provider_install_refuses_unmanaged_directory(isolated_cli_paths: Path) -> None:
    assert cli.PLUGIN_DIR is not None
    cli.PLUGIN_DIR.mkdir(parents=True)
    (cli.PLUGIN_DIR / "plugin.yaml").write_text("name: other\n", encoding="utf-8")

    assert cli.main(["provider", "install"]) == 1


def test_provider_install_and_status(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["provider", "install"]) == 0
    assert cli.provider_status()["installed"] is True

    output = capsys.readouterr().out
    assert "installed provider shim" in output


def test_read_provider_version_handles_missing_or_bad_marker(tmp_path: Path) -> None:
    assert cli.read_provider_version(tmp_path) is None
    (tmp_path / cli.PLUGIN_MARKER).write_text("not json", encoding="utf-8")

    assert cli.read_provider_version(tmp_path) is None


def test_is_managed_provider_accepts_legacy_plugin_yaml(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "plugin.yaml").write_text(
        "name: cursor-provider\nkind: model-provider\n",
        encoding="utf-8",
    )

    assert cli.is_managed_provider(tmp_path) is True


def test_service_commands(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["service", "status"]) == 0
    assert '"state": "not-installed"' in capsys.readouterr().out

    assert cli.main(["service", "install"]) == 0
    assert "wrote service file" in capsys.readouterr().out
    assert cli.service_status()["installed"] is True
    content = (isolated_cli_paths / "service.file").read_text(encoding="utf-8")
    assert "--env-file" in content

    assert cli.main(["service", "uninstall"]) == 0
    assert "removed service file" in capsys.readouterr().out
    assert cli.service_status()["installed"] is False


def test_service_uninstall_not_installed(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["service", "uninstall"]) == 0

    assert "service file not installed" in capsys.readouterr().out


def test_bridge_help_exits_successfully(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["bridge", "--help"])

    assert exc.value.code == 0
    assert "Arguments passed to bridge server" in capsys.readouterr().out


def test_setup_writes_env_and_installs_provider(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BRIDGE_TOKEN", raising=False)
    project = isolated_cli_paths / "project"
    config_path = isolated_cli_paths / "cursor-sdk" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        'allowed_local_roots = ["/tmp/keep"]\nbridge_port = 8787\n',
        encoding="utf-8",
    )

    assert cli.main(["setup", "--cwd", str(project), "--no-service", "--token", "setup-token"]) == 0

    bridge_env = isolated_cli_paths / "cursor-sdk" / "bridge.env"
    hermes_env = isolated_cli_paths / ".hermes" / ".env"
    assert bridge_env.is_file()
    parsed = config.parse_bridge_env_file(bridge_env)
    assert parsed["HERMES_CURSOR_BRIDGE_TOKEN"] == "setup-token"
    assert parsed["HERMES_CURSOR_BRIDGE_CWD"] == str(project.resolve())
    assert hermes_env.is_file()
    assert "HERMES_CURSOR_BRIDGE_TOKEN=setup-token" in hermes_env.read_text(encoding="utf-8")
    assert cli.provider_status()["installed"] is True
    config_text = config_path.read_text(encoding="utf-8")
    assert 'allowed_local_roots = ["/tmp/keep"]' in config_text
    assert "bridge_port = 8787" in config_text
    assert 'bridge_cwd = "' in config_text
    assert "Phase 2 setup complete" in capsys.readouterr().out


def test_setup_requires_api_key(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_API_KEY", raising=False)

    assert cli.main(["setup", "--cwd", str(isolated_cli_paths / "project"), "--no-service"]) == 1
