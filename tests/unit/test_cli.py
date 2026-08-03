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
    assert cli.hermes_config_provider_status()["configured"] is True

    output = capsys.readouterr().out
    assert "installed provider shim" in output
    assert "providers.cursor" in output


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
    assert "hermes_cursor_sdk.bridge" in content
    assert "--env-file" not in content  # bridge.env not created yet

    bridge_env = isolated_cli_paths / "cursor-sdk" / "bridge.env"
    bridge_env.parent.mkdir(parents=True, exist_ok=True)
    bridge_env.write_text("CURSOR_API_KEY=k\nHERMES_CURSOR_BRIDGE_TOKEN=t\n", encoding="utf-8")
    assert cli.main(["service", "install"]) == 0
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
    hermes_config = (isolated_cli_paths / ".hermes" / "config.yaml").read_text(encoding="utf-8")
    assert "hermes-cursor-sdk-managed-provider" in hermes_config
    assert "cursor:" in hermes_config
    assert "HERMES_CURSOR_BRIDGE_TOKEN" in hermes_config
    config_text = config_path.read_text(encoding="utf-8")
    assert 'allowed_local_roots = ["/tmp/keep"]' in config_text
    assert "bridge_port = 8787" in config_text
    assert 'bridge_cwd = "' in config_text
    assert "Phase 2 setup complete" in capsys.readouterr().out


def test_upsert_hermes_config_provider_preserves_siblings(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "model:\n  provider: openrouter\nproviders:\n  other:\n    api: http://example.test/v1\n",
        encoding="utf-8",
    )

    cli.upsert_hermes_config_provider(
        hermes_home=hermes_home,
        base_url="http://127.0.0.1:8787/v1",
    )
    text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert "model:" in text
    assert "other:" in text
    assert "cursor:" in text
    assert 'api: "http://127.0.0.1:8787/v1"' in text

    cli.upsert_hermes_config_provider(
        hermes_home=hermes_home,
        base_url="http://127.0.0.1:9999/v1",
    )
    text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert text.count("cursor:") == 1
    assert 'api: "http://127.0.0.1:9999/v1"' in text
    assert "other:" in text

    assert cli.remove_hermes_config_provider(hermes_home=hermes_home) is not None
    text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert "cursor:" not in text
    assert "other:" in text
    assert "model:" in text


def test_upsert_refuses_unmanaged_cursor_provider(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "providers:\n  cursor:\n    api: http://hand-maintained.test/v1\n",
        encoding="utf-8",
    )

    with pytest.raises(cli.CLIError, match=r"unmanaged providers\.cursor"):
        cli.upsert_hermes_config_provider(
            hermes_home=hermes_home,
            base_url="http://127.0.0.1:8787/v1",
        )

    assert cli.remove_hermes_config_provider(hermes_home=hermes_home) is None
    text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert "hand-maintained.test" in text


def test_upsert_replaces_inline_empty_providers_map(tmp_path: Path) -> None:
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        "model:\n  provider: openrouter\nproviders: {}\nagent:\n  max_turns: 1\n",
        encoding="utf-8",
    )

    cli.upsert_hermes_config_provider(
        hermes_home=hermes_home,
        base_url="http://127.0.0.1:8787/v1",
    )
    text = (hermes_home / "config.yaml").read_text(encoding="utf-8")
    assert text.count("providers:") == 1
    assert "providers: {}" not in text
    assert "cursor:" in text
    assert "model:" in text
    assert "agent:" in text


def test_resolve_provider_base_url_matches_reported_bridge_url(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BASE_URL", raising=False)
    bridge_env = isolated_cli_paths / "cursor-sdk" / "bridge.env"
    bridge_env.parent.mkdir(parents=True, exist_ok=True)
    bridge_env.write_text(
        "CURSOR_API_KEY=k\nHERMES_CURSOR_BASE_URL=http://127.0.0.1:9999/v1\n",
        encoding="utf-8",
    )

    assert cli.resolve_provider_base_url() == "http://127.0.0.1:9999/v1"


def test_setup_requires_api_key(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_API_KEY", raising=False)

    assert cli.main(["setup", "--cwd", str(isolated_cli_paths / "project"), "--no-service"]) == 1


def test_setup_reuses_credentials_from_profile_env(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_BRIDGE_TOKEN", raising=False)
    hermes_env = isolated_cli_paths / ".hermes" / ".env"
    hermes_env.parent.mkdir(parents=True, exist_ok=True)
    hermes_env.write_text(
        "CURSOR_API_KEY=from-profile\nHERMES_CURSOR_BRIDGE_TOKEN=existing-token\n",
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "setup",
                "--cwd",
                str(isolated_cli_paths / "project"),
                "--no-service",
            ]
        )
        == 0
    )

    parsed = config.parse_bridge_env_file(isolated_cli_paths / "cursor-sdk" / "bridge.env")
    assert parsed["CURSOR_API_KEY"] == "from-profile"
    assert parsed["HERMES_CURSOR_BRIDGE_TOKEN"] == "existing-token"


def test_toml_string_escapes_windows_paths() -> None:
    assert cli.toml_string(r"C:\Users\me\proj") == r'"C:\\Users\\me\\proj"'


def test_write_bridge_env_preserves_existing_allowlisted_keys(tmp_path: Path) -> None:
    path = tmp_path / "bridge.env"
    path.write_text(
        "CURSOR_API_KEY=old-key\nHERMES_CURSOR_BRIDGE_CONTEXT_LENGTH=12345\n",
        encoding="utf-8",
    )

    cli.write_bridge_env(
        path,
        {
            "CURSOR_API_KEY": "new-key",
            "HERMES_CURSOR_BRIDGE_TOKEN": "token",
        },
    )

    parsed = config.parse_bridge_env_file(path)
    assert parsed["CURSOR_API_KEY"] == "new-key"
    assert parsed["HERMES_CURSOR_BRIDGE_TOKEN"] == "token"
    assert parsed["HERMES_CURSOR_BRIDGE_CONTEXT_LENGTH"] == "12345"


def test_setup_preserves_custom_base_url(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BASE_URL", raising=False)
    hermes_env = isolated_cli_paths / ".hermes" / ".env"
    hermes_env.parent.mkdir(parents=True, exist_ok=True)
    hermes_env.write_text(
        "CURSOR_API_KEY=cursor-key\nHERMES_CURSOR_BASE_URL=http://127.0.0.1:9999/v1\n",
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "setup",
                "--cwd",
                str(isolated_cli_paths / "project"),
                "--no-service",
                "--token",
                "tok",
            ]
        )
        == 0
    )

    parsed = config.parse_bridge_env_file(isolated_cli_paths / "cursor-sdk" / "bridge.env")
    assert parsed["HERMES_CURSOR_BASE_URL"] == "http://127.0.0.1:9999/v1"
    assert parsed["HERMES_CURSOR_BRIDGE_HOST"] == "127.0.0.1"
    assert parsed["HERMES_CURSOR_BRIDGE_PORT"] == "9999"


def test_setup_doctor_hint_includes_profile(
    isolated_cli_paths: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "setup",
                "--cwd",
                str(isolated_cli_paths / "project"),
                "--profile",
                "co-cto",
                "--no-service",
                "--token",
                "tok",
            ]
        )
        == 0
    )
    assert "doctor --provider-mode --profile co-cto" in capsys.readouterr().out


def test_write_config_toml_dedupes_keys(isolated_cli_paths: Path) -> None:
    path = isolated_cli_paths / "cursor-sdk" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'bridge_cwd = "/old"\nbridge_cwd = "/also-old"\nkeep = "yes"\n',
        encoding="utf-8",
    )

    cli.write_config_toml(
        bridge_cwd=isolated_cli_paths / "project",
        bridge_env_file=isolated_cli_paths / "cursor-sdk" / "bridge.env",
    )

    text = path.read_text(encoding="utf-8")
    assert text.count("bridge_cwd =") == 1
    assert 'keep = "yes"' in text
    assert str(isolated_cli_paths / "project") in text


def test_doctor_prefers_profile_env_over_bridge_env(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BRIDGE_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_BRIDGE_CWD", raising=False)
    bridge_env = isolated_cli_paths / "cursor-sdk" / "bridge.env"
    bridge_env.parent.mkdir(parents=True, exist_ok=True)
    bridge_env.write_text(
        "CURSOR_API_KEY=k\n"
        "HERMES_CURSOR_BRIDGE_TOKEN=from-bridge\n"
        "HERMES_CURSOR_BRIDGE_CWD=/from/bridge\n",
        encoding="utf-8",
    )
    hermes_env = isolated_cli_paths / ".hermes" / ".env"
    hermes_env.parent.mkdir(parents=True, exist_ok=True)
    hermes_env.write_text(
        "HERMES_CURSOR_BRIDGE_TOKEN=from-profile\nHERMES_CURSOR_BRIDGE_CWD=/from/profile\n",
        encoding="utf-8",
    )
    assert cli.main(["provider", "install"]) == 0

    assert cli.main(["doctor", "--provider-mode"]) == 0
    # Doctor should not fail on cwd; profile values win over shared bridge.env.
    # Indirect check: status reports profile cwd when both exist.
    assert cli.main(["status"]) == 0
    assert "bridge_cwd: /from/profile" in capsys.readouterr().out


def test_status_respects_profile_flag(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile_home = isolated_cli_paths / "profiles" / "co-cto"
    plugin = profile_home / "plugins" / "model-providers" / "cursor"
    monkeypatch.setattr(cli, "PLUGIN_DIR", None)
    monkeypatch.setattr(
        cli,
        "resolve_hermes_home",
        lambda profile=None: (
            profile_home if profile == "co-cto" else isolated_cli_paths / ".hermes"
        ),
    )
    monkeypatch.setattr(
        cli,
        "plugin_dir",
        lambda profile=None: (
            profile_home / "plugins" / "model-providers" / "cursor"
            if profile == "co-cto"
            else isolated_cli_paths / ".hermes" / "plugins" / "model-providers" / "cursor"
        ),
    )
    plugin.mkdir(parents=True)
    (plugin / cli.PLUGIN_MARKER).write_text('{"version":"test"}', encoding="utf-8")

    assert cli.main(["status", "--profile", "co-cto"]) == 0
    output = capsys.readouterr().out
    assert f"hermes_home: {profile_home}" in output
    assert "provider: installed" in output


def test_status_and_doctor_read_base_url_from_bridge_env(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("HERMES_CURSOR_BASE_URL", raising=False)
    bridge_env = isolated_cli_paths / "cursor-sdk" / "bridge.env"
    bridge_env.parent.mkdir(parents=True, exist_ok=True)
    bridge_env.write_text(
        "CURSOR_API_KEY=k\n"
        "HERMES_CURSOR_BRIDGE_TOKEN=tok\n"
        "HERMES_CURSOR_BASE_URL=http://127.0.0.1:9999/v1\n",
        encoding="utf-8",
    )
    assert cli.main(["provider", "install"]) == 0
    assert cli.main(["status"]) == 0
    assert "bridge_url: http://127.0.0.1:9999/v1" in capsys.readouterr().out
    assert cli.main(["doctor", "--provider-mode"]) == 0
    assert "bridge_url: http://127.0.0.1:9999/v1" in capsys.readouterr().out


def test_bootstrap_service_fails_when_kickstart_fails(
    isolated_cli_paths: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = isolated_cli_paths / "service.file"
    service.write_text("plist", encoding="utf-8")
    monkeypatch.setattr(cli, "service_path", lambda: service)
    monkeypatch.setattr(cli.sys, "platform", "darwin")

    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(args))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        if args[:2] == ["launchctl", "kickstart"]:
            result = Result()
            result.returncode = 7
            result.stderr = "kickstart failed"
            return result
        return Result()

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    with pytest.raises(cli.CLIError, match="kickstart failed"):
        cli.bootstrap_service()
