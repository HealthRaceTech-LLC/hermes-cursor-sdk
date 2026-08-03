"""Command-line interface for the Hermes Cursor SDK package."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

from hermes_cursor_sdk import __version__
from hermes_cursor_sdk.config import load_settings
from hermes_cursor_sdk.provider import CursorProfile

PLUGIN_DIR = Path.home() / ".hermes" / "plugins" / "model-providers" / "cursor"
PLUGIN_MARKER = ".hermes-cursor-sdk"
SERVICE_LABEL = "com.hermes.cursor-bridge"


class CLIError(Exception):
    """Expected CLI failure with a concise user-facing message."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the Hermes Cursor SDK bridge.")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="Show bridge, provider, and service status.")

    doctor = subparsers.add_parser("doctor", help="Check local bridge/provider configuration.")
    doctor.add_argument(
        "--provider-mode", action="store_true", help="Validate provider mode settings."
    )

    bridge = subparsers.add_parser("bridge", help="Start the OpenAI-compatible bridge.")
    bridge.add_argument(
        "bridge_args", nargs=argparse.REMAINDER, help="Arguments passed to bridge server."
    )

    provider = subparsers.add_parser(
        "provider", help="Install, uninstall, or inspect provider shim."
    )
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_sub.add_parser("install", help="Install provider shim into ~/.hermes.")
    provider_sub.add_parser("uninstall", help="Remove managed provider shim from ~/.hermes.")
    provider_sub.add_parser("status", help="Show provider shim status.")

    service = subparsers.add_parser(
        "service", help="Install, uninstall, or inspect user service files."
    )
    service_sub = service.add_subparsers(dest="service_command", required=True)
    service_sub.add_parser("install", help="Write a launchd/systemd user service file.")
    service_sub.add_parser("uninstall", help="Remove the managed user service file.")
    service_sub.add_parser("status", help="Show user service file status.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        if args.command == "status":
            return cmd_status()
        if args.command == "doctor":
            return cmd_doctor(provider_mode=args.provider_mode)
        if args.command == "bridge":
            from hermes_cursor_sdk.bridge.server import main as bridge_main

            return bridge_main(list(args.bridge_args))
        if args.command == "provider":
            return cmd_provider(args.provider_command)
        if args.command == "service":
            return cmd_service(args.service_command)
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


def cmd_status() -> int:
    settings = load_settings()
    provider = provider_status()
    service = service_status()
    print(f"bridge_url: http://{settings.bridge_host}:{settings.bridge_port}/v1")
    print(f"bridge_expose: {settings.bridge_expose}")
    print(f"bridge_token_configured: {bool(settings.bridge_token)}")
    print(f"provider: {provider['state']} ({provider['path']})")
    print(f"service: {service['state']} ({service['path']})")
    return 0


def cmd_doctor(*, provider_mode: bool) -> int:
    settings = load_settings()
    issues: list[str] = []
    if not settings.bridge_token:
        issues.append("HERMES_CURSOR_BRIDGE_TOKEN is not set")
    if settings.bridge_expose:
        issues.append("bridge is configured for non-loopback exposure")

    print("Hermes Cursor SDK doctor")
    print(f"version: {__version__}")
    print(f"bridge_url: http://{settings.bridge_host}:{settings.bridge_port}/v1")
    print(f"provider_installed: {provider_status()['installed']}")
    print(f"service_installed: {service_status()['installed']}")

    if provider_mode:
        profile = CursorProfile()
        print(f"provider_name: {profile.name}")
        print(f"provider_base_url: {profile.base_url}")
        print(f"provider_env_vars: {', '.join(profile.env_vars)}")

    if issues:
        print("issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1 if provider_mode else 0

    print("status: ok")
    return 0


def cmd_provider(command: str) -> int:
    if command == "install":
        install_provider()
    elif command == "uninstall":
        uninstall_provider()
    elif command == "status":
        status = provider_status()
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        raise CLIError(f"unknown provider command: {command}")
    return 0


def install_provider() -> None:
    destination = PLUGIN_DIR
    if destination.exists() and not is_managed_provider(destination):
        raise CLIError(f"refusing to overwrite unrelated provider files at {destination}")

    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".cursor-provider-", dir=parent))
    backup = parent / f".cursor-provider-backup-{uuid4().hex}"

    try:
        write_provider_files(temp_dir)
        if destination.exists():
            destination.rename(backup)
        temp_dir.rename(destination)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if destination.exists() and not is_managed_provider(destination) and backup.exists():
            destination.rename(parent / f".cursor-provider-failed-{uuid4().hex}")
        if backup.exists() and not destination.exists():
            backup.rename(destination)
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    print(f"installed provider shim: {destination}")


def uninstall_provider() -> None:
    destination = PLUGIN_DIR
    if not destination.exists():
        print(f"provider shim not installed: {destination}")
        return
    if not is_managed_provider(destination):
        raise CLIError(f"refusing to remove unrelated provider files at {destination}")
    shutil.rmtree(destination)
    print(f"removed provider shim: {destination}")


def provider_status() -> dict[str, Any]:
    installed = PLUGIN_DIR.exists() and is_managed_provider(PLUGIN_DIR)
    return {
        "installed": installed,
        "state": "installed" if installed else "not-installed",
        "path": str(PLUGIN_DIR),
        "version": read_provider_version(PLUGIN_DIR) if installed else None,
    }


def write_provider_files(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / "provider_shim"
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy2(source / name, destination / name)
    (destination / PLUGIN_MARKER).write_text(
        json.dumps({"package": "hermes-cursor-sdk", "version": __version__}, sort_keys=True),
        encoding="utf-8",
    )


def is_managed_provider(path: Path) -> bool:
    marker = path / PLUGIN_MARKER
    if marker.exists():
        return True
    plugin_yaml = path / "plugin.yaml"
    if not plugin_yaml.exists():
        return False
    content = plugin_yaml.read_text(encoding="utf-8")
    return "name: cursor-provider" in content and "kind: model-provider" in content


def read_provider_version(path: Path) -> str | None:
    marker = path / PLUGIN_MARKER
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    version = payload.get("version")
    return str(version) if version else None


def cmd_service(command: str) -> int:
    if command == "install":
        install_service()
    elif command == "uninstall":
        uninstall_service()
    elif command == "status":
        print(json.dumps(service_status(), indent=2, sort_keys=True))
    else:
        raise CLIError(f"unknown service command: {command}")
    return 0


def install_service() -> None:
    path = service_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(service_file_content(path), encoding="utf-8")
    print(f"wrote service file: {path}")


def uninstall_service() -> None:
    path = service_path()
    if not path.exists():
        print(f"service file not installed: {path}")
        return
    path.unlink()
    print(f"removed service file: {path}")


def service_status() -> dict[str, Any]:
    path = service_path()
    installed = path.exists()
    return {
        "installed": installed,
        "state": "installed" if installed else "not-installed",
        "path": str(path),
        "kind": "launchd" if sys.platform == "darwin" else "systemd",
    }


def service_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
    return Path.home() / ".config" / "systemd" / "user" / "hermes-cursor-bridge.service"


def service_file_content(path: Path) -> str:
    log_dir = Path.home() / ".hermes" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        return launchd_plist(log_dir)
    return systemd_unit()


def launchd_plist(log_dir: Path) -> str:
    python = escape(sys.executable)
    stdout = escape(str(log_dir / "cursor-bridge.out.log"))
    stderr = escape(str(log_dir / "cursor-bridge.err.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>-m</string>
    <string>hermes_cursor_sdk.bridge</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{stdout}</string>
  <key>StandardErrorPath</key>
  <string>{stderr}</string>
</dict>
</plist>
"""


def systemd_unit() -> str:
    return f"""[Unit]
Description=Hermes Cursor SDK bridge

[Service]
Type=simple
ExecStart={sys.executable} -m hermes_cursor_sdk.bridge
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
