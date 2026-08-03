"""Cursor SDK catalog helpers."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from hermes_cursor_sdk.errors import InvalidArgsError

try:  # pragma: no cover - depends on optional SDK runtime
    from cursor_sdk import ModelParameterValue, ModelSelection
except ImportError:  # pragma: no cover - tests use dict fallback
    ModelParameterValue = None  # type: ignore[assignment]
    ModelSelection = None  # type: ignore[assignment]


@dataclass(frozen=True)
class CursorPrompt:  # pragma: no cover - legacy CursorClient request
    """Compatibility prompt request used by older Hermes adapters."""

    prompt: str
    workspace: str | None = None
    model: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CursorResult:  # pragma: no cover - legacy CursorClient result
    """Compatibility normalized result used by older Hermes adapters."""

    text: str
    status: Literal["ok", "error"] = "ok"
    raw: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def _sdk() -> Any:
    return importlib.import_module("cursor_sdk")


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            value = getattr(obj, name)
            return value() if callable(value) and name.startswith("get_") else value
    return default


def _call_list(resource: Any, api_key: str) -> Any:
    try:
        return resource.list(api_key=api_key)
    except TypeError:
        try:
            return resource.list({"api_key": api_key})
        except TypeError:
            return resource.list()


def _as_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    for name in ("items", "models", "repositories", "data"):
        items = _value(value, name)
        if items is not None:
            return list(items)
    return list(value) if not isinstance(value, (str, bytes, dict)) else [value]


def _normalize_parameters(parameters: Any) -> dict[str, dict[str, Any]]:
    if not parameters:
        return {}
    if isinstance(parameters, dict):
        return {
            str(name): dict(value) if isinstance(value, dict) else {"value": value}
            for name, value in parameters.items()
        }
    result: dict[str, dict[str, Any]] = {}
    for parameter in parameters:
        name = _value(parameter, "name", "id", "key")
        if name:
            result[str(name)] = {
                "name": str(name),
                "type": _value(parameter, "type"),
                "values": _value(parameter, "values", "enum", "options"),
                "default": _value(parameter, "default"),
            }
    return result


def normalize_model(model: Any, *, bridge_context_length: int | None = None) -> dict[str, Any]:
    model_id = _value(model, "id", "model_id", "name")
    parameters = _normalize_parameters(_value(model, "parameters", "params", "model_parameters"))
    return {
        "id": str(model_id) if model_id is not None else "",
        "name": _value(model, "name", "display_name", default=str(model_id) if model_id else ""),
        "provider": _value(model, "provider"),
        "parameters": parameters,
        "presets": _value(model, "presets", default=[]),
        "cursor_context_length": None,
        "bridge_context_length": bridge_context_length,
        "raw": model,
    }


def normalize_repository(repository: Any) -> dict[str, Any]:
    repo_id = _value(repository, "id", "name", "slug", "url")
    return {
        "id": str(repo_id) if repo_id is not None else "",
        "name": _value(repository, "name", "slug", default=str(repo_id) if repo_id else ""),
        "url": _value(repository, "url", "clone_url", "html_url"),
        "default_branch": _value(repository, "default_branch", "default_ref", default="main"),
        "raw": repository,
    }


def list_models(api_key: str) -> list[dict[str, Any]]:
    """List Cursor models using an explicit API key."""

    cursor = _sdk().Cursor
    return [normalize_model(item) for item in _as_items(_call_list(cursor.models, api_key))]


def list_repositories(api_key: str) -> list[dict[str, Any]]:
    """List Cursor repositories using an explicit API key."""

    cursor = _sdk().Cursor
    return [
        normalize_repository(item) for item in _as_items(_call_list(cursor.repositories, api_key))
    ]


def _catalog_entry(catalog: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    for entry in catalog:
        if entry.get("id") == model_id or entry.get("name") == model_id:
            return entry
    return None


def _validate_params(
    model_id: str, params: Mapping[str, Any], catalog: list[dict[str, Any]]
) -> None:
    entry = _catalog_entry(catalog, model_id)
    if not entry:
        raise InvalidArgsError(f"Unknown Cursor model: {model_id}")
    allowed = set((entry.get("parameters") or {}).keys())
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise InvalidArgsError(f"Unknown parameter for {model_id}: {unknown[0]}")


def _parameter_value(value: Any) -> Any:
    if ModelParameterValue is None:
        return value
    try:
        return ModelParameterValue(value=value)
    except TypeError:
        try:
            return ModelParameterValue(value)
        except TypeError:
            return value


def _model_selection(model_id: str, params: Mapping[str, Any]) -> Any:
    serialized = {"id": model_id}
    if params:
        serialized["params"] = {key: _parameter_value(value) for key, value in params.items()}
    if ModelSelection is None:
        return serialized
    try:
        return ModelSelection(id=model_id, params=serialized.get("params"))
    except TypeError:
        try:
            return ModelSelection(model_id, serialized.get("params"))
        except TypeError:
            return serialized


def resolve_model_selection(
    model: str | dict[str, Any] | None,
    params: Mapping[str, Any] | None,
    catalog: list[dict[str, Any]],
    default_model: str,
) -> Any:
    """Validate requested model/params and return SDK-ready selection."""

    requested_params = dict(params or {})
    if isinstance(model, dict):
        model_id = str(model.get("id") or model.get("name") or default_model)
        requested_params = {**dict(model.get("params") or {}), **requested_params}
    else:
        model_id = str(model or default_model)
    _validate_params(model_id, requested_params, catalog)
    return _model_selection(model_id, requested_params)
