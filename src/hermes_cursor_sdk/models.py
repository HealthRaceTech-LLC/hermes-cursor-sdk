"""Cursor SDK catalog helpers."""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from hermes_cursor_sdk.errors import InvalidArgsError

ModelParameterValue: Any = None
ModelSelection: Any = None
try:  # pragma: no cover - depends on optional SDK runtime
    from cursor_sdk import ModelParameterValue as _SDKModelParameterValue
    from cursor_sdk import ModelSelection as _SDKModelSelection
except ImportError:  # pragma: no cover - tests use dict fallback
    pass
else:
    ModelParameterValue = _SDKModelParameterValue
    ModelSelection = _SDKModelSelection


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


# Composer models do not expose a catalog `context` param; Cursor documents a
# fixed 200K window (Max Mode does not expand it).
_COMPOSER_CONTEXT_LENGTH = 200_000


def parse_context_token_count(value: Any) -> int | None:
    """Parse catalog labels like ``200k`` / ``1m`` / ``272000`` into token ints.

    Requires a numeric prefix so labels like ``medium`` are not treated as
    millions (``.endswith("m")`` trap).
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    text = str(value).strip().lower().replace(",", "").replace("_", "")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([km])?", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return int(number * 1_000_000)
    if unit == "k":
        return int(number * 1_000)
    return int(number)


def _parameter_value_tokens(values: Any) -> list[int]:
    if values is None:
        return []
    items = values if isinstance(values, (list, tuple)) else [values]
    out: list[int] = []
    for item in items:
        if isinstance(item, Mapping):
            raw = item.get("value", item.get("id", item.get("display_name")))
        else:
            raw = _value(item, "value", "id", "display_name", default=item)
        parsed = parse_context_token_count(raw)
        if parsed is not None:
            out.append(parsed)
    return out


def catalog_context_options(
    parameters: Mapping[str, Any] | None = None,
) -> list[int]:
    """Return sorted unique context-window options from a catalog ``context`` param."""

    params = parameters or {}
    context_param = params.get("context") if isinstance(params, Mapping) else None
    if not isinstance(context_param, Mapping):
        return []
    tokens = _parameter_value_tokens(context_param.get("values"))
    default_tokens = parse_context_token_count(context_param.get("default"))
    if default_tokens is not None:
        tokens.append(default_tokens)
    return sorted(set(tokens))


def infer_model_context_length(
    model_id: str | None,
    parameters: Mapping[str, Any] | None = None,
    *,
    fallback: int | None = None,
    selected_context: Any | None = None,
) -> tuple[int | None, str | None]:
    """Return (tokens, source) for Hermes `/v1/models` context_length.

    - If ``selected_context`` is set (request param), use that exact window.
    - Else if the catalog lists ``context`` options, advertise the **max**
      (e.g. 1M when Max Mode is available). Base/default is still exposed via
      ``context_options``.
    - Composer models use a fixed 200K window.
    """

    selected = parse_context_token_count(selected_context)
    if selected is not None:
        return selected, "cursor_model_window"

    options = catalog_context_options(parameters)
    if options:
        return max(options), "cursor_model_window"

    mid = (model_id or "").strip().lower()
    if mid.startswith("composer"):
        return _COMPOSER_CONTEXT_LENGTH, "cursor_model_window"

    if fallback is not None and fallback > 0:
        return fallback, "connector_budget"
    return None, None


def normalize_model(model: Any, *, bridge_context_length: int | None = None) -> dict[str, Any]:
    model_id = _value(model, "id", "model_id", "name")
    parameters = _normalize_parameters(_value(model, "parameters", "params", "model_parameters"))
    options = catalog_context_options(parameters)
    cursor_context_length, context_source = infer_model_context_length(
        str(model_id) if model_id is not None else None,
        parameters,
        fallback=bridge_context_length,
    )
    return {
        "id": str(model_id) if model_id is not None else "",
        "name": _value(model, "name", "display_name", default=str(model_id) if model_id else ""),
        "provider": _value(model, "provider"),
        "parameters": parameters,
        "presets": _value(model, "presets", default=[]),
        "cursor_context_length": cursor_context_length,
        "bridge_context_length": bridge_context_length,
        "context_source": context_source,
        "context_options": options or ([cursor_context_length] if cursor_context_length else []),
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


def _sdk_param_entries(params: Mapping[str, Any]) -> list[dict[str, str]]:
    """Serialize params for cursor_sdk.ModelSelection.from_value."""

    return [{"id": str(key), "value": str(value)} for key, value in params.items()]


def _model_selection(model_id: str, params: Mapping[str, Any]) -> Any:
    if ModelSelection is None:
        serialized: dict[str, Any] = {"id": model_id}
        if params:
            serialized["params"] = {key: _parameter_value(value) for key, value in params.items()}
        return serialized

    # Prefer SDK from_value: params must be a sequence (never None). Passing
    # params=None overrides the dataclass default and crashes in to_json().
    from_value = getattr(ModelSelection, "from_value", None)
    if callable(from_value):
        payload: dict[str, Any] = {"id": model_id}
        if params:
            payload["params"] = _sdk_param_entries(params)
        try:
            return from_value(payload)
        except (TypeError, ValueError, AttributeError):
            pass

    # Legacy / test-double constructors may accept dict-shaped params.
    param_values = (
        {key: _parameter_value(value) for key, value in params.items()} if params else None
    )
    try:
        if param_values is None:
            return ModelSelection(id=model_id)
        return ModelSelection(id=model_id, params=param_values)
    except TypeError:
        try:
            if param_values is None:
                return ModelSelection(model_id)
            return ModelSelection(model_id, param_values)
        except TypeError:
            serialized = {"id": model_id}
            if param_values is not None:
                serialized["params"] = param_values
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
