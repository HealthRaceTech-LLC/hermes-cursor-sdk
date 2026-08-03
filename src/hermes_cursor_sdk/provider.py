"""Hermes model-provider profile for the Cursor bridge."""

from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Mapping
from os import getenv
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from hermes_cursor_sdk.client import CursorClient
from hermes_cursor_sdk.config import CursorConfig

ProviderProfile: Any
_register_provider: Any
try:  # pragma: no cover - depends on Hermes runtime.
    # Hermes keeps ProviderProfile on providers.base and register_provider on
    # the providers package root (not on providers.base).
    _providers = importlib.import_module("providers")
    _providers_base = importlib.import_module("providers.base")
except ImportError:  # pragma: no cover - exercised in tests without Hermes.

    class ProviderProfile:
        """Small fallback base that mirrors Hermes profile attribute storage."""

        def __init__(self, **kwargs: Any) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    _REGISTERED_PROVIDERS: dict[str, ProviderProfile] = {}

    def _register_provider(*args: Any) -> ProviderProfile:
        profile = args[-1]
        _REGISTERED_PROVIDERS[getattr(profile, "name", "cursor")] = profile
        return profile
else:
    ProviderProfile = _providers_base.ProviderProfile
    _register_provider = _providers.register_provider


DEFAULT_BASE_URL = "http://127.0.0.1:8787/v1"
DEFAULT_FALLBACK_MODELS = ("composer-2.5",)


def resolve_bridge_base_url() -> str:
    """Resolve the OpenAI-compatible bridge base URL from env or defaults."""

    explicit = getenv("HERMES_CURSOR_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    port = getenv("HERMES_CURSOR_BRIDGE_PORT") or "8787"
    host = getenv("HERMES_CURSOR_BRIDGE_HOST") or "127.0.0.1"
    return f"http://{host}:{port}/v1"


class CursorProfile(ProviderProfile):
    """Provider profile consumed by Hermes model-provider mode."""

    name = "cursor"
    aliases = ("cursor-sdk", "cursor-bridge", "hermes-cursor")
    display_name = "Cursor (SDK bridge)"
    description = "Unofficial Cursor SDK via local OpenAI-compatible bridge"
    env_vars = ("HERMES_CURSOR_BRIDGE_TOKEN", "HERMES_CURSOR_BASE_URL")
    base_url = DEFAULT_BASE_URL
    auth_type = "api_key"
    api_mode = "chat_completions"
    supports_health_check = True
    fallback_models = DEFAULT_FALLBACK_MODELS
    default_max_tokens = 8192
    default_aux_model = ""

    def __init__(self, **overrides: Any) -> None:
        values = {
            "name": self.name,
            "aliases": self.aliases,
            "display_name": self.display_name,
            "description": self.description,
            "env_vars": self.env_vars,
            "base_url": overrides.get("base_url") or resolve_bridge_base_url(),
            "auth_type": self.auth_type,
            "api_mode": self.api_mode,
            "supports_health_check": self.supports_health_check,
            "fallback_models": self.fallback_models,
            "default_max_tokens": self.default_max_tokens,
            "default_aux_model": self.default_aux_model,
        }
        values.update(overrides)
        init_kwargs: dict[str, Any] = {}
        try:
            signature = inspect.signature(super().__init__)
        except (TypeError, ValueError):
            signature = None
        if signature is not None:
            parameters = signature.parameters
            if any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
            ):
                init_kwargs = values
            else:
                init_kwargs = {key: value for key, value in values.items() if key in parameters}
        super().__init__(**init_kwargs)
        for key, value in values.items():
            setattr(self, key, value)

    def fetch_models(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 8.0,
    ) -> list[str] | None:
        """Fetch bridge model IDs via OpenAI-compatible ``/models``.

        Matches Hermes ``ProviderProfile.fetch_models`` so
        ``provider_model_ids()`` can discover the live catalog. Returns
        ``None`` on auth/network/shape failures so Hermes can fall back to
        ``fallback_models`` instead of swallowing a TypeError and emptying
        the picker row.
        """
        resolved_base_url = (base_url or getenv("HERMES_CURSOR_BASE_URL") or self.base_url).rstrip(
            "/"
        )
        resolved_token = api_key or getenv("HERMES_CURSOR_BRIDGE_TOKEN")
        if not resolved_token or not resolved_base_url:
            return None

        request = Request(
            f"{resolved_base_url}/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {resolved_token}",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, OSError, URLError, json.JSONDecodeError):
            return None

        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            return None

        model_ids: list[str] = []
        seen: set[str] = set()
        for item in data:
            if not isinstance(item, Mapping):
                continue
            model_id = item.get("id")
            if not isinstance(model_id, str):
                continue
            normalized = model_id.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            model_ids.append(normalized)
        return model_ids or None

    def build_extra_body(self, session_id: str | None = None, **context: Any) -> dict[str, Any]:
        """Build the OpenAI extra_body cursor extension."""
        cwd = context.pop("cwd", None)
        params = context.pop("params", None)
        if params is None:
            params = {}
        if not isinstance(params, Mapping):
            raise TypeError("params must be a mapping")
        merged_params = dict(params)
        merged_params.update(context)
        return {"cursor": {"session_id": session_id, "cwd": cwd, "params": merged_params}}


def register_cursor_provider() -> Any:
    """Register the Cursor provider profile with Hermes when available."""
    profile = CursorProfile(
        name="cursor",
        aliases=("cursor-sdk", "cursor-bridge", "hermes-cursor"),
        display_name="Cursor (SDK bridge)",
        description="Unofficial Cursor SDK via local OpenAI-compatible bridge",
        env_vars=("HERMES_CURSOR_BRIDGE_TOKEN", "HERMES_CURSOR_BASE_URL"),
        base_url=resolve_bridge_base_url(),
        auth_type="api_key",
        api_mode="chat_completions",
        supports_health_check=True,
        fallback_models=DEFAULT_FALLBACK_MODELS,
        default_max_tokens=8192,
        default_aux_model="",
    )
    try:
        return _register_provider(profile)
    except TypeError:
        return _register_provider("cursor", profile)


class CursorChatProvider:
    """Minimal backwards-compatible chat provider facade over CursorClient."""

    provider_id = "cursor"

    def __init__(self, client: CursorClient | None = None) -> None:
        self.client = client or CursorClient(CursorConfig.from_env())

    def complete(self, prompt: str, **options: Any) -> str:
        """Return text for a prompt."""
        return self.client.run(prompt, **options).text
