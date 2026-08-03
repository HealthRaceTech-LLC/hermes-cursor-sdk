"""Hermes connector for the Cursor SDK."""

from importlib.metadata import PackageNotFoundError, version

from hermes_cursor_sdk import errors
from hermes_cursor_sdk.client import CursorSDKClient
from hermes_cursor_sdk.config import Settings, load_settings, parse_bridge_env_file
from hermes_cursor_sdk.errors import map_exception

try:
    __version__ = version("hermes-cursor-sdk")
except PackageNotFoundError:  # pragma: no cover - local editable fallback
    __version__ = "0.1.0"

__all__ = [
    "CursorSDKClient",
    "Settings",
    "__version__",
    "errors",
    "load_settings",
    "map_exception",
    "parse_bridge_env_file",
]
