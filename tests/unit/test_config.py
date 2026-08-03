from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from hermes_cursor_sdk import config
from hermes_cursor_sdk.config import (
    ConfigurationError,
    Settings,
    load_settings,
    parse_bridge_env_file,
    require_api_key,
)
from hermes_cursor_sdk.errors import AuthMissingError


def test_env_overrides_toml_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                'api_key = "toml-key"',
                'default_model = "toml-model"',
                'store_dir = "~/toml-store"',
                "bridge_port = 9999",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", config_file)

    settings = load_settings(
        env={
            "HERMES_CURSOR_API_KEY": "env-key",
            "HERMES_CURSOR_MODEL": "env-model",
            "HERMES_CURSOR_BRIDGE_PORT": "1234",
        }
    )

    assert settings.api_key == "env-key"
    assert settings.default_model == "env-model"
    assert settings.bridge_port == 1234
    assert settings.store_dir.is_absolute()


def test_hermes_api_key_overrides_cursor_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    settings = load_settings(
        env={"CURSOR_API_KEY": "cursor-key", "HERMES_CURSOR_API_KEY": "hermes-key"}
    )

    assert settings.api_key == "hermes-key"


def test_bridge_env_file_overlays_after_settings_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text(
        "\n".join(
            [
                "CURSOR_API_KEY=bridge-key",
                "HERMES_CURSOR_BRIDGE_TOKEN=bridge-token",
            ]
        ),
        encoding="utf-8",
    )
    config_file = tmp_path / "config.toml"
    config_file.write_text(f'bridge_env_file = "{env_file}"\n', encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", config_file)

    settings = load_settings(env={"HERMES_CURSOR_API_KEY": "process-key"})

    assert settings.api_key == "process-key"
    assert settings.bridge_token == "bridge-token"


def test_load_settings_reads_toml_from_temp_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    config_dir = home / ".hermes" / "cursor-sdk"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "\n".join(
            [
                'api_key = "toml-key"',
                'allowed_local_roots = "~/workspace:/tmp/shared"',
                'allowed_cloud_env_names = "SAFE_ONE,SAFE_TWO"',
                'provider_model_params = "{\\"reasoning_effort\\": \\"high\\"}"',
                "auto_create_pr = true",
                'local_setting_sources = ["user", "workspace"]',
                "bridge_expose = false",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_CURSOR_API_KEY", raising=False)
    monkeypatch.setattr(config, "CONFIG_PATH", Path("~/.hermes/cursor-sdk/config.toml"))

    settings = load_settings(env={})

    assert settings.api_key == "toml-key"
    assert settings.allowed_local_roots[0] == (home / "workspace").resolve()
    assert settings.allowed_cloud_env_names == ["SAFE_ONE", "SAFE_TWO"]
    assert settings.provider_model_params == {"reasoning_effort": "high"}
    assert settings.auto_create_pr is True
    assert settings.local_setting_sources == ["user", "workspace"]
    assert settings.bridge_expose is False


def test_load_settings_rejects_bad_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    with pytest.raises(ConfigurationError, match="sdk_http_timeout"):
        load_settings(env={"HERMES_CURSOR_SDK_HTTP_TIMEOUT": "soon"})


def test_bridge_env_file_strict_parser(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text(
        "CURSOR_API_KEY=cursor_key\n"
        "HERMES_CURSOR_BRIDGE_TOKEN=bridge-token\n"
        "HTTPS_PROXY=https://proxy.example\n",
        encoding="utf-8",
    )

    parsed = parse_bridge_env_file(env_file)

    assert parsed["CURSOR_API_KEY"] == "cursor_key"
    assert parsed["HERMES_CURSOR_BRIDGE_TOKEN"] == "bridge-token"
    assert parsed["HTTPS_PROXY"] == "https://proxy.example"


@pytest.mark.parametrize(
    "line",
    [
        "export CURSOR_API_KEY=bad",
        " CURSOR_API_KEY=bad",
        "cursor_api_key=bad",
        "NOT_ALLOWED=value",
        "CURSOR_API_KEY",
    ],
)
def test_bridge_env_file_rejects_shellish_or_unknown_lines(tmp_path: Path, line: str) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text(line, encoding="utf-8")

    with pytest.raises(ConfigurationError):
        parse_bridge_env_file(env_file)


def test_bridge_env_file_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text(
        "\n# comment\nCURSOR_API_KEY=cursor_key\n",
        encoding="utf-8",
    )

    assert parse_bridge_env_file(env_file) == {"CURSOR_API_KEY": "cursor_key"}


def test_bridge_env_file_rejects_nul_value(tmp_path: Path) -> None:
    env_file = tmp_path / "bridge.env"
    env_file.write_text("CURSOR_API_KEY=bad\x00value", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="Invalid bridge env value"):
        parse_bridge_env_file(env_file)


def test_require_api_key_strips_or_rejects() -> None:
    assert require_api_key(Settings(api_key=" cursor-key ")) == "cursor-key"

    with pytest.raises(AuthMissingError, match="Cursor API key is required"):
        require_api_key(Settings(api_key=" "))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("yes", True),
        ("on", True),
        ("0", False),
        ("off", False),
        (1, True),
        (0, False),
    ],
)
def test_bool_env_parsing(
    value: str | int,
    expected: bool,
) -> None:
    settings = config._coerce_settings({"auto_create_pr": value})

    assert settings.auto_create_pr is expected


def test_bool_env_parsing_rejects_bad_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    with pytest.raises(ConfigurationError, match="auto_create_pr"):
        load_settings(env={"HERMES_CURSOR_AUTO_CREATE_PR": "sometimes"})


def test_local_setting_sources_default_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    settings = load_settings(env={"IGNORED": "1"})

    assert settings.local_setting_sources is None


def test_path_expansion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    settings = load_settings(
        env={
            "HERMES_CURSOR_STORE_DIR": "~/cursor-store",
            "HERMES_CURSOR_BRIDGE_CWD": "~/workspace",
            "HERMES_CURSOR_MCP_CONFIG": "~/mcp.json",
        }
    )

    assert settings.store_dir == (home / "cursor-store").resolve()
    assert settings.bridge_cwd == (home / "workspace").resolve()
    assert settings.mcp_config == (home / "mcp.json").resolve()


def test_settings_is_frozen() -> None:
    settings = Settings(api_key="key")

    with pytest.raises(FrozenInstanceError):
        settings.api_key = "other"  # type: ignore[misc]


def test_resolve_hermes_home_prefers_env_then_active_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "co-cto"
    profile.mkdir(parents=True)
    (root / "active_profile").write_text("co-cto\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_HOME", raising=False)

    assert config.resolve_hermes_home() == profile.resolve()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "explicit"))
    assert config.resolve_hermes_home() == (tmp_path / "explicit").resolve()
    assert (
        config.resolve_hermes_home(profile="other")
        == (tmp_path / ".hermes" / "profiles" / "other").resolve()
    )


def test_load_settings_auto_loads_default_bridge_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge_env = tmp_path / "bridge.env"
    bridge_env.write_text(
        "CURSOR_API_KEY=from-file\nHERMES_CURSOR_BRIDGE_TOKEN=token-from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(config, "DEFAULT_BRIDGE_ENV_PATH", bridge_env)

    settings = load_settings(env={})

    assert settings.api_key == "from-file"
    assert settings.bridge_token == "token-from-file"
    assert settings.bridge_env_file == bridge_env.resolve()
