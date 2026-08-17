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


# First-party Cursor models do not expose a catalog `context` param, so their
# windows are fixed by design and cannot be read from the catalog. Composer is a
# fixed 200K window (Max Mode does not expand it). Cursor Grok 4.5 is 256K, and
# Grok 4.6 shares the Grok 4.5 base so it is also 256K.
_FIXED_CONTEXT_LENGTHS: dict[str, int] = {
    "composer": 200_000,
    "grok-4.5": 256_000,
    "grok-4.6": 256_000,
}


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


def _fixed_context_length(model_id: str | None) -> int | None:
    """Return the fixed window for a first-party Cursor model, if known."""

    mid = (model_id or "").strip().lower()
    for prefix, tokens in _FIXED_CONTEXT_LENGTHS.items():
        if mid.startswith(prefix):
            return tokens
    return None


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
    - First-party Cursor models (Composer, Grok 4.5/4.6) use their fixed
      windows because they do not expose a ``context`` param.
    """

    selected = parse_context_token_count(selected_context)
    if selected is not None:
        return selected, "cursor_model_window"

    options = catalog_context_options(parameters)
    if options:
        return max(options), "cursor_model_window"

    fixed = _fixed_context_length(model_id)
    if fixed is not None:
        return fixed, "cursor_model_window"

    if fallback is not None and fallback > 0:
        return fallback, "connector_budget"
    return None, None


def _normalize_variants(value: Any) -> list[dict[str, Any]]:
    """Normalize SDK ``variants`` (formerly ``presets``) into plain dicts.

    Each variant is an effort/speed preset (e.g. ``fast``, ``effort=high``) with
    an ``is_default`` flag; its ``params`` are ``{id: value}`` pairs.
    """

    if not value:
        return []
    result: list[dict[str, Any]] = []
    for variant in value:
        params = _value(variant, "params", default=[]) or []
        result.append(
            {
                "name": _value(variant, "name", "display_name", default=""),
                "is_default": bool(_value(variant, "is_default", default=False)),
                "params": {
                    str(_value(param, "id", "name")): _value(param, "value") for param in params
                },
            }
        )
    return result


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
        "variants": _normalize_variants(_value(model, "variants", "presets")),
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


EFFORT_ALIAS_MAP: dict[str, str] = {
    "extra_high": "xhigh",
    "extra-high": "xhigh",
    "none": "minimal",
}


def map_reasoning_effort(
    model_entry: Mapping[str, Any] | None, params: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Map reasoning_effort to model's effort/reasoning parameter and clamp values."""

    if not params:
        return {}

    out = dict(params)
    raw_effort = out.pop("reasoning_effort", None)
    if raw_effort is None or model_entry is None:
        return out

    raw_val = str(raw_effort).strip().lower()

    if isinstance(model_entry, Mapping):
        param_defs = model_entry.get("parameters") or {}
    else:
        param_defs = getattr(model_entry, "parameters", None) or {}

    if isinstance(param_defs, (list, tuple)):
        param_map: dict[str, Any] = {}
        for p in param_defs:
            pid = getattr(p, "id", None) or (p.get("id") if isinstance(p, Mapping) else None)
            if pid:
                param_map[pid] = p
        param_defs = param_map

    target_key = None
    if "effort" in param_defs:
        target_key = "effort"
    elif "reasoning" in param_defs:
        target_key = "reasoning"
    elif "reasoning_effort" in param_defs:
        target_key = "reasoning_effort"

    if not target_key:
        return out

    param_def = param_defs[target_key]
    valid_values: list[str] = []

    if isinstance(param_def, Mapping):
        raw_vals = param_def.get("values") or []
    else:
        raw_vals = getattr(param_def, "values", None) or []

    for v in raw_vals:
        val_str = getattr(v, "value", None) or (
            v.get("value") if isinstance(v, Mapping) else str(v)
        )
        if val_str:
            valid_values.append(str(val_str).lower())

    if not valid_values:
        out[target_key] = raw_val
        return out

    if raw_val in valid_values:
        out[target_key] = raw_val
        return out

    aliased = EFFORT_ALIAS_MAP.get(raw_val, raw_val)
    if aliased in valid_values:
        out[target_key] = aliased
        return out

    if raw_val in ("xhigh", "extra-high", "extra_high"):
        for alt in ("xhigh", "extra-high", "high"):
            if alt in valid_values:
                out[target_key] = alt
                return out

    if raw_val == "max":
        for alt in ("max", "xhigh", "extra-high", "high"):
            if alt in valid_values:
                out[target_key] = alt
                return out

    out[target_key] = valid_values[-1]
    return out


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
    entry = _catalog_entry(catalog, model_id)
    if entry is not None:
        requested_params = map_reasoning_effort(entry, requested_params)
    _validate_params(model_id, requested_params, catalog)
    return _model_selection(model_id, requested_params)
