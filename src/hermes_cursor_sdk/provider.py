"""Hermes model-provider profile for the Cursor bridge."""

from __future__ import annotations

import importlib
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
    _register_provider = _providers_base.register_provider


DEFAULT_BASE_URL = "http://127.0.0.1:8787/v1"
DEFAULT_FALLBACK_MODELS = ("composer-2.5",)


class CursorProfile(ProviderProfile):
    """Provider profile consumed by Hermes model-provider mode."""

    name = "cursor"
    aliases = ("cursor-sdk", "cursor-bridge", "hermes-cursor")
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
            "env_vars": self.env_vars,
            "base_url": self.base_url,
            "auth_type": self.auth_type,
            "api_mode": self.api_mode,
            "supports_health_check": self.supports_health_check,
            "fallback_models": self.fallback_models,
            "default_max_tokens": self.default_max_tokens,
            "default_aux_model": self.default_aux_model,
        }
        values.update(overrides)
        try:
            super().__init__(**values)
        except TypeError:
            super().__init__()
        for key, value in values.items():
            setattr(self, key, value)

    def fetch_models(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        """Fetch bridge models through the OpenAI-compatible /models endpoint."""
        resolved_base_url = (base_url or getenv("HERMES_CURSOR_BASE_URL") or self.base_url).rstrip(
            "/"
        )
        resolved_token = token or getenv("HERMES_CURSOR_BRIDGE_TOKEN")
        if not resolved_token:
            raise RuntimeError("HERMES_CURSOR_BRIDGE_TOKEN is required to fetch Cursor models")

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
        except HTTPError as exc:
            raise RuntimeError(f"Cursor bridge model fetch failed with HTTP {exc.code}") from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cursor bridge model fetch failed: {exc}") from exc

        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            raise RuntimeError("Cursor bridge /models response did not include a data array")
        return [dict(item) for item in data if isinstance(item, Mapping)]

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
        env_vars=("HERMES_CURSOR_BRIDGE_TOKEN", "HERMES_CURSOR_BASE_URL"),
        base_url=DEFAULT_BASE_URL,
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
