"""Command-line interface for the Hermes Cursor SDK package."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
from xml.sax.saxutils import escape

from hermes_cursor_sdk import __version__
from hermes_cursor_sdk.config import (
    BRIDGE_ENV_ALLOWLIST,
    CONFIG_PATH,
    DEFAULT_BRIDGE_ENV_PATH,
    ConfigurationError,
    load_settings,
    parse_bridge_env_file,
    resolve_hermes_home,
)
from hermes_cursor_sdk.provider import CursorProfile

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

    status = subparsers.add_parser("status", help="Show bridge, provider, and service status.")
    status.add_argument(
        "--profile",
        help="Hermes profile name (default: HERMES_HOME / active_profile).",
    )

    doctor = subparsers.add_parser("doctor", help="Check local bridge/provider configuration.")
    doctor.add_argument(
        "--provider-mode", action="store_true", help="Validate provider mode settings."
    )
    doctor.add_argument(
        "--profile",
        help="Hermes profile name used when checking the provider shim path.",
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
            return cmd_status(profile=args.profile)
        if args.command == "doctor":
            return cmd_doctor(provider_mode=args.provider_mode, profile=args.profile)
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
    except (CLIError, ConfigurationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.error(f"unknown command: {args.command}")
    return 2


def read_bridge_env_map(settings: Any | None = None) -> dict[str, str]:
    """Read allowlisted keys from the configured/shared bridge.env when present."""

    resolved = settings if settings is not None else load_settings()
    env_file = getattr(resolved, "bridge_env_file", None) or DEFAULT_BRIDGE_ENV_PATH
    path = Path(env_file).expanduser().resolve()
    if not path.is_file():
        return {}
    try:
        return parse_bridge_env_file(path)
    except ConfigurationError:
        return {}


def resolve_reported_bridge_url(
    *,
    settings: Any,
    profile_env: Mapping[str, str],
    bridge_env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the bridge base URL for status/doctor reporting."""

    shared = bridge_env if bridge_env is not None else read_bridge_env_map(settings)
    return (
        os.environ.get("HERMES_CURSOR_BASE_URL")
        or profile_env.get("HERMES_CURSOR_BASE_URL")
        or shared.get("HERMES_CURSOR_BASE_URL")
        or f"http://{settings.bridge_host}:{settings.bridge_port}/v1"
    ).rstrip("/")


def cmd_status(*, profile: str | None = None) -> int:
    settings = load_settings()
    hermes_home = resolve_hermes_home(profile=profile)
    profile_env = read_env_file(hermes_home / ".env")
    bridge_env = read_bridge_env_map(settings)
    provider = provider_status(profile=profile)
    service = service_status()
    bridge_token = (
        os.environ.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or profile_env.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or settings.bridge_token
        or bridge_env.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or ""
    ).strip()
    bridge_cwd = (
        os.environ.get("HERMES_CURSOR_BRIDGE_CWD")
        or profile_env.get("HERMES_CURSOR_BRIDGE_CWD")
        or (str(settings.bridge_cwd) if settings.bridge_cwd else "")
        or bridge_env.get("HERMES_CURSOR_BRIDGE_CWD")
        or ""
    )
    bridge_url = resolve_reported_bridge_url(
        settings=settings, profile_env=profile_env, bridge_env=bridge_env
    )
    print(f"hermes_home: {hermes_home}")
    print(f"bridge_url: {bridge_url}")
    print(f"bridge_expose: {settings.bridge_expose}")
    print(f"bridge_token_configured: {bool(bridge_token)}")
    print(f"bridge_cwd: {bridge_cwd}")
    print(f"provider: {provider['state']} ({provider['path']})")
    print(f"service: {service['state']} ({service['path']})")
    return 0


def cmd_doctor(*, provider_mode: bool, profile: str | None = None) -> int:
    settings = load_settings()
    hermes_home = resolve_hermes_home(profile=profile)
    profile_env = read_env_file(hermes_home / ".env")
    bridge_env = read_bridge_env_map(settings)
    provider = provider_status(profile=profile)
    # Prefer process env + selected profile .env over shared bridge.env values
    # that load_settings() may have already absorbed.
    bridge_token = (
        os.environ.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or profile_env.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or settings.bridge_token
        or bridge_env.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or ""
    ).strip()
    bridge_cwd = (
        os.environ.get("HERMES_CURSOR_BRIDGE_CWD")
        or profile_env.get("HERMES_CURSOR_BRIDGE_CWD")
        or (str(settings.bridge_cwd) if settings.bridge_cwd else None)
        or bridge_env.get("HERMES_CURSOR_BRIDGE_CWD")
    )
    bridge_url = resolve_reported_bridge_url(
        settings=settings, profile_env=profile_env, bridge_env=bridge_env
    )
    issues: list[str] = []
    if not bridge_token:
        issues.append("HERMES_CURSOR_BRIDGE_TOKEN is not set")
    if settings.bridge_expose:
        issues.append("bridge is configured for non-loopback exposure")
    if provider_mode and not provider["installed"]:
        issues.append("provider shim is not installed (run: hermes-cursor setup --cwd …)")
    if provider_mode and not bridge_cwd:
        issues.append("HERMES_CURSOR_BRIDGE_CWD is not set")

    print("Hermes Cursor SDK doctor")
    print(f"version: {__version__}")
    print(f"hermes_home: {hermes_home}")
    print(f"bridge_url: {bridge_url}")
    print(f"provider_installed: {provider['installed']}")
    print(f"service_installed: {service_status()['installed']}")

    if provider_mode:
        cursor_profile = CursorProfile()
        print(f"provider_name: {cursor_profile.name}")
        print(f"provider_display_name: {cursor_profile.display_name}")
        print(f"provider_base_url: {bridge_url}")
        print(f"provider_env_vars: {', '.join(cursor_profile.env_vars)}")

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

    try:
        hermes_home = resolve_hermes_home(profile=profile)
    except ConfigurationError as exc:
        raise CLIError(str(exc)) from exc

    hermes_home.mkdir(parents=True, exist_ok=True)
    bridge_env_path = DEFAULT_BRIDGE_ENV_PATH.expanduser().resolve()
    existing_bridge = parse_bridge_env_file(bridge_env_path) if bridge_env_path.is_file() else {}
    existing_hermes = read_env_file(hermes_home / ".env")

    # Prefer process env, then the target profile .env, then shared bridge.env.
    api_key = (
        os.environ.get("HERMES_CURSOR_API_KEY")
        or os.environ.get("CURSOR_API_KEY")
        or existing_hermes.get("HERMES_CURSOR_API_KEY")
        or existing_hermes.get("CURSOR_API_KEY")
        or existing_bridge.get("HERMES_CURSOR_API_KEY")
        or existing_bridge.get("CURSOR_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise CLIError(
            "CURSOR_API_KEY (or HERMES_CURSOR_API_KEY) must be set in the environment, "
            "bridge.env, or the Hermes profile .env"
        )

    bridge_token = (
        token
        or os.environ.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or existing_hermes.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or existing_bridge.get("HERMES_CURSOR_BRIDGE_TOKEN")
        or ""
    ).strip()
    if not bridge_token:
        bridge_token = secrets.token_hex(32)

    settings = load_settings()
    base_url = (
        os.environ.get("HERMES_CURSOR_BASE_URL")
        or existing_hermes.get("HERMES_CURSOR_BASE_URL")
        or existing_bridge.get("HERMES_CURSOR_BASE_URL")
        or f"http://{settings.bridge_host}:{settings.bridge_port}/v1"
    ).rstrip("/")
    bridge_host, bridge_port = bridge_bind_from_base_url(
        base_url,
        fallback_host=settings.bridge_host,
        fallback_port=settings.bridge_port,
    )
    bridge_env_path.parent.mkdir(parents=True, exist_ok=True)
    write_bridge_env(
        bridge_env_path,
        {
            "CURSOR_API_KEY": api_key,
            "HERMES_CURSOR_BRIDGE_TOKEN": bridge_token,
            "HERMES_CURSOR_BRIDGE_CWD": str(project),
            "HERMES_CURSOR_BRIDGE_HOST": bridge_host,
            "HERMES_CURSOR_BRIDGE_PORT": bridge_port,
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
            "HERMES_CURSOR_DEFAULT_MODEL": (
                os.environ.get("HERMES_CURSOR_DEFAULT_MODEL")
                or existing_hermes.get("HERMES_CURSOR_DEFAULT_MODEL")
                or "composer-2.5"
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
        "Next: restart Hermes Desktop / gateway, then pick Cursor (SDK bridge) in the model picker."
    )
    doctor_hint = "hermes-cursor doctor --provider-mode"
    if profile:
        doctor_hint += f" --profile {profile}"
    print(f"Run: {doctor_hint}")
    return 0


def bridge_bind_from_base_url(
    base_url: str, *, fallback_host: str, fallback_port: int
) -> tuple[str, str]:
    """Derive bridge bind host/port from a base URL so they stay aligned."""

    parsed = urlparse(base_url)
    host = parsed.hostname or fallback_host
    if parsed.port is not None:
        port = str(parsed.port)
    elif parsed.scheme == "https":
        port = "443"
    elif parsed.scheme == "http":
        port = "80"
    else:
        port = str(fallback_port)
    return host, port


def read_env_file(path: Path) -> dict[str, str]:
    """Best-effort KEY=VALUE reader for Hermes .env files (no shell expansion)."""

    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.strip():
            result[key.strip()] = value
    return result


def toml_string(value: str) -> str:
    """Render a TOML basic string with escapes safe on Windows paths."""

    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def write_bridge_env(path: Path, values: Mapping[str, str]) -> None:
    """Upsert allowlisted keys into bridge.env without dropping unrelated entries."""

    existing: dict[str, str] = {}
    if path.is_file():
        existing = parse_bridge_env_file(path)
    updates = {key: value for key, value in values.items() if key in BRIDGE_ENV_ALLOWLIST}
    unknown = sorted(set(values) - BRIDGE_ENV_ALLOWLIST)
    if unknown:
        raise CLIError(f"refusing to write non-allowlisted bridge env keys: {', '.join(unknown)}")
    merged = {**existing, **updates}
    lines = [f"{key}={merged[key]}" for key in sorted(merged)]
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
            key = line.split("=", 1)[0].strip()
            if key in keys:
                if key in seen:
                    # Drop duplicate assignments for keys we are upserting.
                    continue
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
    """Upsert only bridge path keys; leave unrelated TOML content untouched."""

    path = CONFIG_PATH.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    updates = {
        "bridge_cwd": str(bridge_cwd),
        "bridge_env_file": str(bridge_env_file),
    }
    if not path.is_file():
        path.write_text(
            "\n".join(f"{key} = {toml_string(value)}" for key, value in updates.items()) + "\n",
            encoding="utf-8",
        )
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        replaced = False
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                if key in seen:
                    # Drop later duplicate assignments for keys we are upserting.
                    continue
                out.append(f"{key} = {toml_string(updates[key])}")
                seen.add(key)
                replaced = True
        if not replaced:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key} = {toml_string(value)}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


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
    subprocess.run(
        ["launchctl", "bootout", f"{domain}/{label}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CLIError(f"launchctl bootstrap failed with code {result.returncode}: {detail}")
    subprocess.run(
        ["launchctl", "enable", f"{domain}/{label}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    kick = subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{label}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if kick.returncode != 0:
        detail = (kick.stderr or kick.stdout or "").strip()
        raise CLIError(f"launchctl kickstart failed with code {kick.returncode}: {detail}")
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


def bridge_program_args() -> list[str]:
    """Return argv for the bridge process, including --env-file when present."""

    args = [sys.executable, "-m", "hermes_cursor_sdk.bridge"]
    settings = load_settings()
    env_file = settings.bridge_env_file or DEFAULT_BRIDGE_ENV_PATH.expanduser().resolve()
    env_path = Path(env_file).expanduser().resolve()
    if env_path.is_file():
        args.extend(["--env-file", str(env_path)])
    return args


def launchd_plist(log_dir: Path) -> str:
    stdout = escape(str(log_dir / "cursor-bridge.out.log"))
    stderr = escape(str(log_dir / "cursor-bridge.err.log"))
    arg_xml = "\n".join(f"    <string>{escape(part)}</string>" for part in bridge_program_args())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{arg_xml}
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
    exec_start = " ".join(shlex.quote(part) for part in bridge_program_args())
    return f"""[Unit]
Description=Hermes Cursor SDK bridge

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
