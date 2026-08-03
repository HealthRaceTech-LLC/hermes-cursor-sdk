"""Configuration loading for Hermes' Cursor SDK connector."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from hermes_cursor_sdk.errors import AuthMissingError, ConfigurationError

CONFIG_PATH = Path("~/.hermes/cursor-sdk/config.toml").expanduser()
DEFAULT_STORE_DIR = Path("~/.hermes/cache/cursor-sdk/").expanduser()

BRIDGE_ENV_ALLOWLIST = {
    "CURSOR_API_KEY",
    "CURSOR_BASE_URL",
    "HERMES_CURSOR_BRIDGE_TOKEN",
    "HERMES_CURSOR_BRIDGE_CONTEXT_LENGTH",
    "HERMES_CURSOR_BRIDGE_MAX_COMPLETION_TOKENS",
    "HERMES_CURSOR_BRIDGE_MAX_OUTPUT_TOKENS",
    "HERMES_CURSOR_BRIDGE_REQUEST_SIZE_LIMIT",
    "HERMES_CURSOR_BRIDGE_MAX_SESSIONS",
    "HERMES_CURSOR_BRIDGE_IDLE_TIMEOUT_SECONDS",
    "HERMES_CURSOR_MCP_CONFIG",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


@dataclass(frozen=True)
class Settings:
    api_key: str | None = None
    default_model: str = "composer-2.5"
    default_cloud_ref: str = "main"
    store_dir: Path = field(default_factory=lambda: DEFAULT_STORE_DIR.expanduser().resolve())
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8787
    bridge_token: str | None = None
    bridge_cwd: Path | None = None
    bridge_env_file: Path | None = None
    provider_model_params: dict[str, Any] = field(default_factory=dict)
    bridge_context_length: int = 65536
    bridge_max_output_tokens: int = 8192
    sdk_http_timeout: int = 30
    run_wait_timeout: int = 900
    max_request_bytes: int = 1048576
    gateway_max_sessions: int = 8
    gateway_idle_seconds: int = 1800
    mcp_config: Path | None = None
    allowed_local_roots: list[Path] = field(default_factory=list)
    allowed_cloud_env_names: list[str] = field(default_factory=list)
    auto_create_pr: bool = False
    local_setting_sources: list[str] | None = None
    bridge_expose: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return _coerce_settings(_env_settings())

    @property
    def bridge_max_completion_tokens(self) -> int:
        return self.bridge_max_output_tokens

    @property
    def bridge_request_size_limit(self) -> int:
        return self.max_request_bytes

    @property
    def bridge_max_sessions(self) -> int:
        return self.gateway_max_sessions

    @property
    def bridge_idle_timeout_seconds(self) -> int:
        return self.gateway_idle_seconds


def _expand_path(value: str | Path | None) -> Path | None:
    if value in {None, ""}:
        return None
    return Path(value).expanduser().resolve()


def _parse_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ConfigurationError(f"{name} must be a boolean")


def _parse_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _parse_mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ConfigurationError(f"{name} must be JSON object text") from exc
        if isinstance(loaded, dict):
            return loaded
    raise ConfigurationError(f"{name} must be a mapping")


def _parse_list(value: Any, name: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        pattern = rf"[,{re.escape(os.pathsep)}]+"
        return [part.strip() for part in re.split(pattern, value) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    raise ConfigurationError(f"{name} must be a list")


def _coerce_settings(data: dict[str, Any]) -> Settings:
    known = {field.name for field in fields(Settings)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigurationError(f"Unknown cursor-sdk setting: {unknown[0]}")

    coerced = dict(data)
    for key in ("store_dir", "bridge_cwd", "bridge_env_file", "mcp_config"):
        if key in coerced:
            coerced[key] = _expand_path(coerced[key])
    if "allowed_local_roots" in coerced:
        coerced["allowed_local_roots"] = [
            path
            for value in _parse_list(coerced["allowed_local_roots"], "allowed_local_roots")
            if (path := _expand_path(value)) is not None
        ]
    if "allowed_cloud_env_names" in coerced:
        coerced["allowed_cloud_env_names"] = _parse_list(
            coerced["allowed_cloud_env_names"], "allowed_cloud_env_names"
        )
    if "local_setting_sources" in coerced and coerced["local_setting_sources"] is not None:
        coerced["local_setting_sources"] = _parse_list(
            coerced["local_setting_sources"], "local_setting_sources"
        )
    if "provider_model_params" in coerced:
        coerced["provider_model_params"] = _parse_mapping(
            coerced["provider_model_params"], "provider_model_params"
        )
    for key in (
        "bridge_port",
        "bridge_context_length",
        "bridge_max_output_tokens",
        "sdk_http_timeout",
        "run_wait_timeout",
        "max_request_bytes",
        "gateway_max_sessions",
        "gateway_idle_seconds",
    ):
        if key in coerced:
            coerced[key] = _parse_int(coerced[key], key)
    for key in ("auto_create_pr", "bridge_expose"):
        if key in coerced:
            coerced[key] = _parse_bool(coerced[key], key)

    if "store_dir" not in coerced:
        coerced["store_dir"] = DEFAULT_STORE_DIR.expanduser().resolve()
    return Settings(**coerced)


def _toml_settings(path: Path | None = None) -> dict[str, Any]:
    expanded = (path or CONFIG_PATH).expanduser()
    if not expanded.exists():
        return {}
    with expanded.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ConfigurationError("cursor-sdk config must be a TOML table")
    return data


def _env_settings(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    resolved_env = env if env is not None else os.environ
    mapping = {
        "HERMES_CURSOR_API_KEY": "api_key",
        "CURSOR_API_KEY": "api_key",
        "HERMES_CURSOR_DEFAULT_MODEL": "default_model",
        "HERMES_CURSOR_MODEL": "default_model",
        "HERMES_CURSOR_DEFAULT_CLOUD_REF": "default_cloud_ref",
        "HERMES_CURSOR_STORE_DIR": "store_dir",
        "HERMES_CURSOR_BRIDGE_HOST": "bridge_host",
        "HERMES_CURSOR_BRIDGE_PORT": "bridge_port",
        "HERMES_CURSOR_BRIDGE_TOKEN": "bridge_token",
        "HERMES_CURSOR_BRIDGE_CWD": "bridge_cwd",
        "HERMES_CURSOR_BRIDGE_ENV_FILE": "bridge_env_file",
        "HERMES_CURSOR_PROVIDER_MODEL_PARAMS": "provider_model_params",
        "HERMES_CURSOR_BRIDGE_CONTEXT_LENGTH": "bridge_context_length",
        "HERMES_CURSOR_BRIDGE_MAX_OUTPUT_TOKENS": "bridge_max_output_tokens",
        "HERMES_CURSOR_BRIDGE_MAX_COMPLETION_TOKENS": "bridge_max_output_tokens",
        "HERMES_CURSOR_SDK_HTTP_TIMEOUT": "sdk_http_timeout",
        "HERMES_CURSOR_RUN_WAIT_TIMEOUT": "run_wait_timeout",
        "HERMES_CURSOR_MAX_REQUEST_BYTES": "max_request_bytes",
        "HERMES_CURSOR_BRIDGE_REQUEST_SIZE_LIMIT": "max_request_bytes",
        "HERMES_CURSOR_GATEWAY_MAX_SESSIONS": "gateway_max_sessions",
        "HERMES_CURSOR_BRIDGE_MAX_SESSIONS": "gateway_max_sessions",
        "HERMES_CURSOR_GATEWAY_IDLE_SECONDS": "gateway_idle_seconds",
        "HERMES_CURSOR_BRIDGE_IDLE_TIMEOUT_SECONDS": "gateway_idle_seconds",
        "HERMES_CURSOR_MCP_CONFIG": "mcp_config",
        "HERMES_CURSOR_ALLOWED_LOCAL_ROOTS": "allowed_local_roots",
        "HERMES_CURSOR_ALLOWED_CLOUD_ENV_NAMES": "allowed_cloud_env_names",
        "HERMES_CURSOR_AUTO_CREATE_PR": "auto_create_pr",
        "HERMES_CURSOR_LOCAL_SETTING_SOURCES": "local_setting_sources",
        "HERMES_CURSOR_BRIDGE_EXPOSE": "bridge_expose",
    }
    result: dict[str, Any] = {}
    for env_name, setting_name in mapping.items():
        if env_name in resolved_env and resolved_env[env_name] != "":
            result[setting_name] = resolved_env[env_name]
    return result


def load_settings(
    path: str | Path | None = None, *, env: Mapping[str, str] | None = None
) -> Settings:
    """Load settings from TOML, then overlay environment variables."""

    data = _toml_settings()
    if path is not None:
        data.update(_env_settings(parse_bridge_env_file(path)))
    data.update(_env_settings(env))
    return _coerce_settings(data)


def parse_bridge_env_file(path: str | Path) -> dict[str, str]:
    """Parse a strict KEY=VALUE env file without shell evaluation."""

    expanded = Path(path).expanduser().resolve()
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        expanded.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line or raw_line.startswith("#"):
            continue
        if raw_line.startswith("export ") or raw_line.strip() != raw_line or "=" not in raw_line:
            raise ConfigurationError(f"Invalid bridge env line {line_number}: expected KEY=VALUE")
        key, value = raw_line.split("=", 1)
        if not _KEY_RE.fullmatch(key):
            raise ConfigurationError(f"Invalid bridge env key on line {line_number}")
        if key not in BRIDGE_ENV_ALLOWLIST:
            raise ConfigurationError(f"Bridge env key {key} is not allowlisted")
        if "\x00" in value:
            raise ConfigurationError(f"Invalid bridge env value for {key}")
        result[key] = value
    return result


def require_api_key(settings: Settings) -> str:
    """Return the configured API key or raise a normalized auth_missing error."""

    if settings.api_key and settings.api_key.strip():
        return settings.api_key.strip()
    raise AuthMissingError("Cursor API key is required")


CursorConfig = Settings
