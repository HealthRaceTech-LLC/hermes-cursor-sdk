"""Command-line interface for the Hermes Cursor SDK package."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.sax.saxutils import escape

from hermes_cursor_sdk import __version__
from hermes_cursor_sdk.config import (
    CONFIG_PATH,
    DEFAULT_BRIDGE_ENV_PATH,
    load_settings,
    resolve_hermes_home,
)
from hermes_cursor_sdk.provider import CursorProfile, resolve_bridge_base_url

PLUGIN_MARKER = ".hermes-cursor-sdk"
SERVICE_LABEL = "com.hermes.cursor-bridge"

# Tests monkeypatch this to a temp path. ``None`` means resolve from Hermes home.
PLUGIN_DIR: Path | None = None


class CLIError(Exception):
    """Expected CLI failure with a concise user-facing message."""


def plugin_dir(*, profile: str | None = None) -> Path:
    """Return the managed provider-shim install path."""

    if PLUGIN_DIR is not None:
        return PLUGIN_DIR
    return resolve_hermes_home(profile=profile) / "plugins" / "model-providers" / "cursor"


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
    provider_install = provider_sub.add_parser(
        "install", help="Install provider shim into the active Hermes home."
    )
    provider_install.add_argument(
        "--profile",
        help="Hermes profile name (default: HERMES_HOME / active_profile).",
    )
    provider_uninstall = provider_sub.add_parser(
        "uninstall", help="Remove managed provider shim from the active Hermes home."
    )
    provider_uninstall.add_argument("--profile", help="Hermes profile name.")
    provider_status = provider_sub.add_parser("status", help="Show provider shim status.")
    provider_status.add_argument("--profile", help="Hermes profile name.")

    service = subparsers.add_parser(
        "service", help="Install, uninstall, or inspect user service files."
    )
    service_sub = service.add_subparsers(dest="service_command", required=True)
    service_sub.add_parser("install", help="Write a launchd/systemd user service file.")
    service_sub.add_parser("uninstall", help="Remove the managed user service file.")
    service_sub.add_parser("status", help="Show user service file status.")

    setup = subparsers.add_parser(
        "setup",
        help="Configure Phase 2 chat-provider mode (bridge.env, Hermes .env, shim, service).",
    )
    setup.add_argument(
        "--cwd",
        required=True,
        help="Absolute default project directory for bridge sessions.",
    )
    setup.add_argument(
        "--profile",
        help="Hermes profile to install into (default: HERMES_HOME / active_profile).",
    )
    setup.add_argument(
        "--token",
        help="Bridge bearer token (generated if omitted).",
    )
    setup.add_argument(
        "--no-service",
        action="store_true",
        help="Skip writing the launchd/systemd unit.",
    )
    setup.add_argument(
        "--load-service",
        action="store_true",
        help="On macOS, bootstrap/kickstart the launchd agent after writing it.",
    )

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
            return cmd_provider(args.provider_command, profile=getattr(args, "profile", None))
        if args.command == "service":
            return cmd_service(args.service_command)
        if args.command == "setup":
            return cmd_setup(
                cwd=args.cwd,
                profile=args.profile,
                token=args.token,
                install_service_unit=not args.no_service,
                load_service=args.load_service,
            )
    except CLIError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


def cmd_status() -> int:
    settings = load_settings()
    provider = provider_status()
    service = service_status()
    print(f"hermes_home: {resolve_hermes_home()}")
    print(f"bridge_url: http://{settings.bridge_host}:{settings.bridge_port}/v1")
    print(f"bridge_expose: {settings.bridge_expose}")
    print(f"bridge_token_configured: {bool(settings.bridge_token)}")
    print(f"bridge_cwd: {settings.bridge_cwd or ''}")
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
    if provider_mode and not provider_status()["installed"]:
        issues.append("provider shim is not installed (run: hermes-cursor setup --cwd …)")
    if provider_mode and settings.bridge_cwd is None:
        issues.append("HERMES_CURSOR_BRIDGE_CWD is not set")

    print("Hermes Cursor SDK doctor")
    print(f"version: {__version__}")
    print(f"hermes_home: {resolve_hermes_home()}")
    print(f"bridge_url: http://{settings.bridge_host}:{settings.bridge_port}/v1")
    print(f"provider_installed: {provider_status()['installed']}")
    print(f"service_installed: {service_status()['installed']}")

    if provider_mode:
        profile = CursorProfile()
        print(f"provider_name: {profile.name}")
        print(f"provider_display_name: {profile.display_name}")
        print(f"provider_base_url: {profile.base_url}")
        print(f"provider_env_vars: {', '.join(profile.env_vars)}")

    if issues:
        print("issues:")
        for issue in issues:
            print(f"- {issue}")
        return 1 if provider_mode else 0

    print("status: ok")
    return 0


def cmd_setup(
    *,
    cwd: str,
    profile: str | None,
    token: str | None,
    install_service_unit: bool,
    load_service: bool,
) -> int:
    """Write Phase 2 config, install provider shim, and optionally the bridge service."""

    project = Path(cwd).expanduser().resolve()
    if not project.is_dir():
        raise CLIError(f"--cwd must be an existing directory: {project}")

    api_key = (
        os.environ.get("HERMES_CURSOR_API_KEY") or os.environ.get("CURSOR_API_KEY") or ""
    ).strip()
    if not api_key:
        raise CLIError("CURSOR_API_KEY (or HERMES_CURSOR_API_KEY) must be set in the environment")

    hermes_home = resolve_hermes_home(profile=profile)
    bridge_token = (token or os.environ.get("HERMES_CURSOR_BRIDGE_TOKEN") or "").strip()
    if not bridge_token:
        bridge_token = secrets.token_hex(32)

    base_url = resolve_bridge_base_url()
    bridge_env_path = DEFAULT_BRIDGE_ENV_PATH.expanduser().resolve()
    bridge_env_path.parent.mkdir(parents=True, exist_ok=True)
    write_bridge_env(
        bridge_env_path,
        {
            "CURSOR_API_KEY": api_key,
            "HERMES_CURSOR_BRIDGE_TOKEN": bridge_token,
            "HERMES_CURSOR_BRIDGE_CWD": str(project),
            "HERMES_CURSOR_BASE_URL": base_url,
        },
    )
    print(f"wrote bridge env: {bridge_env_path}")

    hermes_env = hermes_home / ".env"
    upsert_env_file(
        hermes_env,
        {
            "CURSOR_API_KEY": api_key,
            "HERMES_CURSOR_BRIDGE_TOKEN": bridge_token,
            "HERMES_CURSOR_BASE_URL": base_url,
            "HERMES_CURSOR_BRIDGE_CWD": str(project),
            "HERMES_CURSOR_DEFAULT_MODEL": os.environ.get(
                "HERMES_CURSOR_DEFAULT_MODEL", "composer-2.5"
            ),
        },
    )
    print(f"updated Hermes env: {hermes_env}")

    write_config_toml(bridge_cwd=project, bridge_env_file=bridge_env_path)
    print(f"updated config: {CONFIG_PATH.expanduser()}")

    os.environ["HERMES_CURSOR_BRIDGE_TOKEN"] = bridge_token
    os.environ["HERMES_CURSOR_BASE_URL"] = base_url
    os.environ["HERMES_CURSOR_BRIDGE_CWD"] = str(project)

    install_provider(profile=profile)
    if install_service_unit:
        install_service()
        if load_service:
            bootstrap_service()

    print("Phase 2 setup complete.")
    print(f"hermes_home: {hermes_home}")
    print(f"provider: {plugin_dir(profile=profile)}")
    print(
        "Next: restart Hermes Desktop / gateway, then pick "
        "Cursor (SDK bridge) in the model picker."
    )
    print("Run: hermes-cursor doctor --provider-mode")
    return 0


def write_bridge_env(path: Path, values: Mapping[str, str]) -> None:
    lines = [f"{key}={values[key]}" for key in sorted(values)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def upsert_env_file(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    keys = set(values)
    seen: set[str] = set()
    out: list[str] = []
    for line in existing:
        if line and not line.startswith("#") and "=" in line:
            key = line.split("=", 1)[0]
            if key in keys:
                out.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    path.chmod(0o600)


def write_config_toml(*, bridge_cwd: Path, bridge_env_file: Path) -> None:
    path = CONFIG_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            existing[key.strip()] = value.strip().strip('"')
    existing["bridge_cwd"] = str(bridge_cwd)
    existing["bridge_env_file"] = str(bridge_env_file)
    rendered = "\n".join(f'{key} = "{value}"' for key, value in sorted(existing.items())) + "\n"
    path.write_text(rendered, encoding="utf-8")


def cmd_provider(command: str, *, profile: str | None = None) -> int:
    if command == "install":
        install_provider(profile=profile)
    elif command == "uninstall":
        uninstall_provider(profile=profile)
    elif command == "status":
        status = provider_status(profile=profile)
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        raise CLIError(f"unknown provider command: {command}")
    return 0


def install_provider(*, profile: str | None = None) -> None:
    destination = plugin_dir(profile=profile)
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


def uninstall_provider(*, profile: str | None = None) -> None:
    destination = plugin_dir(profile=profile)
    if not destination.exists():
        print(f"provider shim not installed: {destination}")
        return
    if not is_managed_provider(destination):
        raise CLIError(f"refusing to remove unrelated provider files at {destination}")
    shutil.rmtree(destination)
    print(f"removed provider shim: {destination}")


def provider_status(*, profile: str | None = None) -> dict[str, Any]:
    destination = plugin_dir(profile=profile)
    installed = destination.exists() and is_managed_provider(destination)
    return {
        "installed": installed,
        "state": "installed" if installed else "not-installed",
        "path": str(destination),
        "hermes_home": str(resolve_hermes_home(profile=profile)),
        "version": read_provider_version(destination) if installed else None,
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


def bootstrap_service() -> None:
    """Load or kickstart the macOS launchd agent when requested."""

    if sys.platform != "darwin":
        print("service load skipped: only implemented for macOS launchd")
        return
    path = service_path()
    if not path.exists():
        raise CLIError(f"service file not installed: {path}")
    domain = f"gui/{os.getuid()}"
    label = SERVICE_LABEL
    # Best-effort unload then bootstrap so re-setup is idempotent.
    os.system(f"launchctl bootout {domain}/{label} >/dev/null 2>&1")
    rc = os.system(f"launchctl bootstrap {domain} {path}")
    if rc != 0:
        raise CLIError(f"launchctl bootstrap failed with code {rc}")
    os.system(f"launchctl enable {domain}/{label} >/dev/null 2>&1")
    os.system(f"launchctl kickstart -k {domain}/{label} >/dev/null 2>&1")
    print(f"loaded launchd service: {label}")


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
    env_file = escape(str(DEFAULT_BRIDGE_ENV_PATH.expanduser().resolve()))
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
    <string>--env-file</string>
    <string>{env_file}</string>
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
    env_file = DEFAULT_BRIDGE_ENV_PATH.expanduser().resolve()
    return f"""[Unit]
Description=Hermes Cursor SDK bridge

[Service]
Type=simple
ExecStart={sys.executable} -m hermes_cursor_sdk.bridge --env-file {env_file}
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
