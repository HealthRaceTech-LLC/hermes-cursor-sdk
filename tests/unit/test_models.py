from __future__ import annotations

import sys
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from hermes_cursor_sdk import models
from hermes_cursor_sdk.errors import InvalidArgsError
from hermes_cursor_sdk.models import (
    list_models,
    list_repositories,
    normalize_model,
    normalize_repository,
    resolve_model_selection,
)


@dataclass
class SDKModel:
    id: str
    name: str
    provider: str
    parameters: list[Any]
    presets: list[str]


@dataclass
class SDKParameter:
    name: str
    type: str
    values: list[str]
    default: str


def catalog() -> list[dict[str, Any]]:
    return [
        normalize_model(
            {
                "id": "composer-2.5",
                "name": "Composer",
                "parameters": {
                    "reasoning_effort": {"type": "string", "values": ["low", "medium", "high"]},
                    "max_tokens": {"type": "integer"},
                },
            },
            bridge_context_length=12345,
        )
    ]


def selection_id(selection: Any) -> str:
    if isinstance(selection, dict):
        return str(selection["id"])
    return str(selection.id)


def selection_params(selection: Any) -> dict[str, Any]:
    params = (
        selection.get("params")
        if isinstance(selection, dict)
        else getattr(selection, "params", None)
    )
    if not params:
        return {}
    if isinstance(params, dict):
        return {key: getattr(value, "value", value) for key, value in params.items()}
    result: dict[str, Any] = {}
    for item in params:
        if hasattr(item, "id"):
            result[str(item.id)] = getattr(item, "value", item)
        elif isinstance(item, dict):
            result[str(item.get("id"))] = item.get("value")
    return result


def test_normalize_model_from_mapping() -> None:
    model = normalize_model(
        {
            "id": "composer-2.5",
            "display_name": "Composer",
            "provider": "cursor",
            "params": {"reasoning_effort": {"type": "string"}},
        },
        bridge_context_length=65536,
    )

    assert model["id"] == "composer-2.5"
    assert model["name"] == "Composer"
    assert model["provider"] == "cursor"
    assert model["parameters"]["reasoning_effort"]["type"] == "string"
    assert model["bridge_context_length"] == 65536
    assert model["cursor_context_length"] == 200_000
    assert model["context_source"] == "cursor_model_window"


def test_infer_model_context_length_from_catalog_context_param() -> None:
    from hermes_cursor_sdk.models import catalog_context_options, infer_model_context_length

    params = {
        "context": {
            "name": "context",
            "values": [
                {"value": "272k", "display_name": "272K"},
                {"value": "1m", "display_name": "1M"},
            ],
        }
    }
    tokens, source = infer_model_context_length("gpt-5.6-sol", params, fallback=200_000)
    # Advertise max (1M) when Max Mode / larger context is available.
    assert tokens == 1_000_000
    assert source == "cursor_model_window"
    assert catalog_context_options(params) == [272_000, 1_000_000]

    selected, _ = infer_model_context_length(
        "gpt-5.6-sol", params, fallback=200_000, selected_context="272k"
    )
    assert selected == 272_000


def test_normalize_model_from_object_parameters() -> None:
    model = normalize_model(
        SDKModel(
            id="gpt-5",
            name="GPT-5",
            provider="cursor",
            presets=["fast"],
            parameters=[SDKParameter("effort", "string", ["low"], "low")],
        )
    )

    assert model["id"] == "gpt-5"
    assert model["parameters"]["effort"] == {
        "name": "effort",
        "type": "string",
        "values": ["low"],
        "default": "low",
    }
    assert model["presets"] == ["fast"]


def test_normalize_repository_from_object() -> None:
    repo = normalize_repository(
        SimpleNamespace(
            slug="repo-slug",
            clone_url="git@example.com:repo.git",
            default_ref="trunk",
        )
    )

    assert repo["id"] == "repo-slug"
    assert repo["name"] == "repo-slug"
    assert repo["url"] == "git@example.com:repo.git"
    assert repo["default_branch"] == "trunk"


def test_list_models_and_repositories_import_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    class Resource:
        def __init__(self, payload: Any) -> None:
            self.payload = payload

        def list(self, *args: Any, **kwargs: Any) -> Any:
            assert args == ()
            assert kwargs == {"api_key": "cursor-key"}
            return self.payload

    fake_sdk = SimpleNamespace(
        Cursor=SimpleNamespace(
            models=Resource(SimpleNamespace(models=[{"id": "composer-2.5"}])),
            repositories=Resource(
                SimpleNamespace(repositories=[{"url": "git@example.com:repo.git"}])
            ),
        )
    )
    monkeypatch.setitem(sys.modules, "cursor_sdk", fake_sdk)

    assert list_models("cursor-key")[0]["id"] == "composer-2.5"
    assert list_repositories("cursor-key")[0]["id"] == "git@example.com:repo.git"


def test_call_list_supports_positional_and_no_arg_fallbacks() -> None:
    class PositionalResource:
        def list(self, *args: Any, **kwargs: Any) -> list[str]:
            if kwargs:
                raise TypeError("no kwargs")
            assert args == ({"api_key": "cursor-key"},)
            return ["positional"]

    class NoArgResource:
        def list(self, *args: Any, **kwargs: Any) -> list[str]:
            if args or kwargs:
                raise TypeError("no args")
            return ["none"]

    assert models._call_list(PositionalResource(), "cursor-key") == ["positional"]
    assert models._call_list(NoArgResource(), "cursor-key") == ["none"]


def test_resolve_model_selection_empty_params_are_iterable() -> None:
    selection = resolve_model_selection(None, {}, catalog(), "composer-2.5")

    assert selection_id(selection) == "composer-2.5"
    params = (
        selection.get("params")
        if isinstance(selection, dict)
        else getattr(selection, "params", ())
    )
    assert params is not None
    assert list(params) == []
    if hasattr(selection, "to_json"):
        assert selection.to_json()["id"] == "composer-2.5"


def test_resolve_model_selection_uses_default_and_params() -> None:
    selection = resolve_model_selection(
        None,
        {"reasoning_effort": "high"},
        catalog(),
        "composer-2.5",
    )

    assert selection_id(selection) == "composer-2.5"
    assert selection_params(selection)["reasoning_effort"] == "high"


def test_resolve_model_selection_merges_model_dict_params() -> None:
    selection = resolve_model_selection(
        {"id": "composer-2.5", "params": {"reasoning_effort": "low"}},
        {"max_tokens": 100},
        catalog(),
        "unused",
    )

    assert selection_id(selection) == "composer-2.5"
    # cursor_sdk.ModelParameterValue stores values as strings.
    assert selection_params(selection) == {
        "reasoning_effort": "low",
        "max_tokens": "100",
    }


def test_resolve_model_selection_invalid_param_raises() -> None:
    with pytest.raises(InvalidArgsError, match="Unknown parameter"):
        resolve_model_selection(
            "composer-2.5",
            {"temperature": 0.2},
            catalog(),
            "composer-2.5",
        )


def test_resolve_model_selection_unknown_model_raises() -> None:
    with pytest.raises(InvalidArgsError, match="Unknown Cursor model"):
        resolve_model_selection("missing", {}, catalog(), "composer-2.5")


def test_model_selection_falls_back_to_positional_constructors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PositionalValue:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if kwargs:
                raise TypeError("positional value only")
            self.value = args[0]

    class PositionalSelection:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if kwargs:
                raise TypeError("positional selection only")
            self.id = args[0]
            self.params = args[1]

    monkeypatch.setattr(models, "ModelParameterValue", PositionalValue)
    monkeypatch.setattr(models, "ModelSelection", PositionalSelection)

    selection = resolve_model_selection(
        "composer-2.5",
        {"reasoning_effort": "high"},
        catalog(),
        "composer-2.5",
    )

    assert selection_id(selection) == "composer-2.5"
    assert selection_params(selection) == {"reasoning_effort": "high"}
