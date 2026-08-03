"""Hermes tool handlers for the Cursor SDK plugin."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from os import getenv
from typing import Any, cast

from hermes_cursor_sdk.client import CursorSDKClient
from hermes_cursor_sdk.config import Settings, load_settings
from hermes_cursor_sdk.errors import map_exception
from hermes_cursor_sdk.results import ResultDict, error_result, ok_result
from hermes_cursor_sdk.schemas import TOOL_SCHEMAS

_CLIENT: CursorSDKClient | None = None
_AGENT_ACTIONS = {"list", "get", "archive", "delete", "cancel"}
_AGENT_ID_ACTIONS = {"get", "archive", "delete", "cancel"}
_RUNTIMES = {"local", "cloud"}
_START_MODES = {"agent", "plan"}


class _InvalidArgs(ValueError):
    def __init__(self, message: str, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = dict(details or {})


def cursor_api_key_available() -> bool:
    """Return whether a Cursor API key is configured without touching the network."""
    try:
        settings = load_settings()
    except Exception:
        return bool(getenv("CURSOR_API_KEY"))

    api_key = (
        getattr(settings, "cursor_api_key", None)
        or getattr(settings, "api_key", None)
        or getattr(settings, "CURSOR_API_KEY", None)
    )
    return bool(api_key or getenv("CURSOR_API_KEY"))


def _client() -> CursorSDKClient:
    """Lazily construct the planned shared Cursor SDK client."""
    global _CLIENT

    if _CLIENT is not None:
        return _CLIENT
    client_class = CursorSDKClient
    if client_class is None:
        raise RuntimeError("CursorSDKClient is not available")

    settings: Settings = load_settings()
    try:
        _CLIENT = client_class(settings=settings)
    except TypeError:
        _CLIENT = client_class(settings)
    return _CLIENT


def _json(result: Any) -> str:
    if isinstance(result, str):
        try:
            json.loads(result)
        except json.JSONDecodeError:
            pass
        else:
            return result
    return json.dumps(result, default=str, sort_keys=True)


def _ok(value: Any) -> ResultDict:
    if isinstance(value, dict) and "ok" in value:
        return cast(ResultDict, value)
    if isinstance(value, str):
        return ok_result(result_text=value)
    return ok_result(metadata={"data": value})


def _error(code: str, message: str, details: Any | None = None) -> ResultDict:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": False,
        "retry_after": None,
        "request_id": None,
        "status_code": None,
        "help_url": None,
    }
    metadata: dict[str, Any] | None = None
    if details is not None:
        metadata = {"details": details}
    try:
        return error_result(error, metadata=metadata)
    except TypeError:
        legacy_error_result = cast(Any, error_result)
        return cast(ResultDict, legacy_error_result(code=code, message=message, details=details))


def _exception_error(exc: Exception) -> ResultDict:
    mapped = map_exception(exc)
    if isinstance(mapped, dict):
        code = str(mapped.get("code") or exc.__class__.__name__)
        message = str(mapped.get("message") or str(exc))
        details = mapped.get("details")
        return _error(code, message, details)
    return _error(exc.__class__.__name__, str(mapped or exc))


def _handler(func: Callable[[dict[str, Any]], Any]) -> Callable[..., str]:
    def wrapped(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
        try:
            if args is None:
                args = {}
            if not isinstance(args, dict):
                return _json(_error("invalid_args", "Tool arguments must be an object."))
            return _json(_ok(func(args, **kwargs)))
        except _InvalidArgs as exc:
            return _json(_error("invalid_args", str(exc), exc.details or None))
        except Exception as exc:  # pragma: no cover - exercised by shared client failures
            return _json(_exception_error(exc))

    return wrapped


def _required_str(args: Mapping[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArgs(f"{name} is required.", {"field": name})
    return value


def _optional_str(args: Mapping[str, Any], name: str) -> str | None:
    if name not in args:
        return None
    value = args[name]
    if not isinstance(value, str) or not value.strip():
        raise _InvalidArgs(f"{name} must be a non-empty string.", {"field": name})
    return value


def _optional_bool(args: Mapping[str, Any], name: str) -> bool | None:
    if name not in args:
        return None
    value = args[name]
    if not isinstance(value, bool):
        raise _InvalidArgs(f"{name} must be a boolean.", {"field": name})
    return value


def _optional_dict(args: Mapping[str, Any], name: str) -> dict[str, Any] | None:
    if name not in args:
        return None
    value = args[name]
    if not isinstance(value, dict):
        raise _InvalidArgs(f"{name} must be an object.", {"field": name})
    return dict(value)


def _optional_str_list(args: Mapping[str, Any], name: str) -> list[str] | None:
    if name not in args:
        return None
    value = args[name]
    if not isinstance(value, list):
        raise _InvalidArgs(f"{name} must be a list of non-empty strings.", {"field": name})
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _InvalidArgs(f"{name} must be a list of non-empty strings.", {"field": name})
        result.append(item)
    return result


def _optional_enum(args: Mapping[str, Any], name: str, allowed: set[str]) -> str | None:
    value = _optional_str(args, name)
    if value is not None and value not in allowed:
        raise _InvalidArgs(
            f"{name} must be one of: {', '.join(sorted(allowed))}.",
            {"field": name, "allowed": sorted(allowed)},
        )
    return value


def _required_repos(args: Mapping[str, Any]) -> list[dict[str, Any]]:
    repos = args.get("repos")
    if not isinstance(repos, list) or not repos:
        raise _InvalidArgs("repos is required.", {"field": "repos"})

    normalized: list[dict[str, Any]] = []
    for index, repo in enumerate(repos):
        if not isinstance(repo, dict):
            raise _InvalidArgs("Each repo must be an object.", {"field": f"repos[{index}]"})
        url = repo.get("url")
        starting_ref = repo.get("starting_ref") or "main"
        if not isinstance(url, str) or not url.strip():
            raise _InvalidArgs("repo.url is required.", {"field": f"repos[{index}].url"})
        if not isinstance(starting_ref, str) or not starting_ref.strip():
            raise _InvalidArgs(
                "repo.starting_ref must be a non-empty string.",
                {"field": f"repos[{index}].starting_ref"},
            )
        normalized_repo = {"url": url, "starting_ref": starting_ref}
        pr_url = repo.get("pr_url")
        if pr_url is not None:
            if not isinstance(pr_url, str) or not pr_url.strip():
                raise _InvalidArgs(
                    "repo.pr_url must be a non-empty string.",
                    {"field": f"repos[{index}].pr_url"},
                )
            normalized_repo["pr_url"] = pr_url
        normalized.append(normalized_repo)
    return normalized


def _invoke(method_names: Iterable[str], payload: Mapping[str, Any]) -> Any:
    client = _client()
    for method_name in method_names:
        method = getattr(client, method_name, None)
        if callable(method):
            return method(**payload)
    raise AttributeError(f"CursorSDKClient lacks handler for {', '.join(method_names)}")


def _derive_idempotency_key(
    args: Mapping[str, Any], kwargs: Mapping[str, Any], repos: list[dict[str, Any]]
) -> str:
    seed = {
        "correlation_id": args.get("correlation_id"),
        "mode": args.get("mode", "agent"),
        "model": args.get("model"),
        "prompt": args.get("prompt"),
        "repos": repos,
        "session_id": kwargs.get("session_id"),
        "task_id": kwargs.get("task_id"),
    }
    digest = hashlib.sha256(json.dumps(seed, default=str, sort_keys=True).encode()).hexdigest()
    return f"cursor-start-{digest[:32]}"


def _session_key(
    session_id: Any,
    task_id: Any,
    session_tag: str | None,
) -> str:
    if session_id or task_id:
        return f"hermes:{session_id or '-'}:{task_id or '-'}"
    if session_tag:
        return f"tag:{session_tag}"
    raise _InvalidArgs(
        "cursor_session_send requires a Hermes session id, task id, or session_tag.",
        {"field": "session_tag"},
    )


@_handler
def cursor_models(args: dict[str, Any], **_: Any) -> Any:
    payload: dict[str, Any] = {}
    refresh = _optional_bool(args, "refresh")
    if refresh is not None:
        payload["refresh"] = refresh
    return _invoke(("list_models",), payload)


@_handler
def cursor_repositories(args: dict[str, Any], **_: Any) -> Any:
    payload: dict[str, Any] = {}
    refresh = _optional_bool(args, "refresh")
    if refresh is not None:
        payload["refresh"] = refresh
    return _invoke(("list_repositories",), payload)


@_handler
def cursor_run(args: dict[str, Any], **_: Any) -> Any:
    payload = {
        "prompt": _required_str(args, "prompt"),
        "cwd": _required_str(args, "cwd"),
    }
    model = _optional_str(args, "model")
    params = _optional_dict(args, "params")
    if model is not None:
        payload["model"] = model
    if params is not None:
        payload["params"] = params
    return _invoke(("run_local",), payload)


@_handler
def cursor_start(args: dict[str, Any], **kwargs: Any) -> Any:
    repos = _required_repos(args)
    payload: dict[str, Any] = {
        "prompt": _required_str(args, "prompt"),
        "repos": repos,
    }

    mode = _optional_enum(args, "mode", _START_MODES)
    model = _optional_str(args, "model")
    correlation_id = _optional_str(args, "correlation_id")
    params = _optional_dict(args, "params")
    env_names = _optional_str_list(args, "env_names")
    auto_create_pr = _optional_bool(args, "auto_create_pr")
    skip_reviewer_request = _optional_bool(args, "skip_reviewer_request")
    wait = _optional_bool(args, "wait")
    if mode is not None:
        payload["mode"] = mode
    if model is not None:
        payload["model"] = model
    if correlation_id is not None:
        payload["metadata"] = {"correlation_id": correlation_id}
    if params is not None:
        payload["params"] = params
    if env_names is not None:
        payload["env_names"] = env_names
    if auto_create_pr is not None:
        payload["auto_create_pr"] = auto_create_pr
    if skip_reviewer_request is not None:
        payload["skip_reviewer_request"] = skip_reviewer_request
    if wait is not None:
        payload["wait"] = wait

    idempotency_key = _optional_str(args, "idempotency_key")
    payload["idempotency_key"] = idempotency_key or _derive_idempotency_key(args, kwargs, repos)
    return _invoke(("start_cloud",), payload)


@_handler
def cursor_status(args: dict[str, Any], **_: Any) -> Any:
    payload = {"agent_id": _required_str(args, "agent_id")}
    run_id = _optional_str(args, "run_id")
    if run_id is not None:
        payload["run_id"] = run_id
    return _invoke(("status",), payload)


@_handler
def cursor_resume(args: dict[str, Any], **_: Any) -> Any:
    payload = {
        "agent_id": _required_str(args, "agent_id"),
        "prompt": _required_str(args, "prompt"),
    }
    cwd = _optional_str(args, "cwd")
    force = _optional_bool(args, "force")
    if cwd is not None:
        payload["cwd"] = cwd
    if force is not None:
        payload["force"] = force
    return _invoke(("resume_and_send",), payload)


@_handler
def cursor_cancel(args: dict[str, Any], **_: Any) -> Any:
    payload = {"agent_id": _required_str(args, "agent_id")}
    run_id = _optional_str(args, "run_id")
    if run_id is not None:
        payload["run_id"] = run_id
    return _invoke(("cancel",), payload)


@_handler
def cursor_session_send(args: dict[str, Any], **kwargs: Any) -> Any:
    session_id = kwargs.get("session_id")
    task_id = kwargs.get("task_id")
    session_tag = _optional_str(args, "session_tag")
    agent_id = _optional_str(args, "agent_id")
    cwd = _optional_str(args, "cwd")
    model = _optional_str(args, "model")
    session_key = _session_key(session_id, task_id, session_tag)

    if agent_id is None and cwd is None:
        store = getattr(_client(), "store", None)
        get_session = getattr(store, "get_session", None)
        if not callable(get_session) or not get_session(session_key):
            raise _InvalidArgs(
                "cwd is required on the first cursor_session_send turn.", {"field": "cwd"}
            )

    payload: dict[str, Any] = {
        "prompt": _required_str(args, "prompt"),
        "session_key": session_key,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    if task_id is not None:
        payload["task_id"] = task_id
    if cwd is not None:
        payload["cwd"] = cwd
    if session_tag is not None:
        payload["session_tag"] = session_tag
    if agent_id is not None:
        payload["agent_id"] = agent_id
    if model is not None:
        payload["model"] = model

    params = _optional_dict(args, "params")
    force = _optional_bool(args, "force")
    close = _optional_bool(args, "close")
    if params is not None:
        payload["params"] = params
    if force is not None:
        payload["force"] = force
    if close is not None:
        payload["close"] = close
    return _invoke(("session_send",), payload)


@_handler
def cursor_agent(args: dict[str, Any], **_: Any) -> Any:
    action = _required_str(args, "action")
    if action not in _AGENT_ACTIONS:
        raise _InvalidArgs(
            "action is invalid.",
            {"field": "action", "allowed": sorted(_AGENT_ACTIONS)},
        )

    payload: dict[str, Any] = {"action": action}
    runtime = _optional_enum(args, "runtime", _RUNTIMES)
    agent_id = _optional_str(args, "agent_id")
    if runtime is not None:
        payload["runtime"] = runtime
    if agent_id is not None:
        payload["agent_id"] = agent_id

    if action in _AGENT_ID_ACTIONS and agent_id is None:
        raise _InvalidArgs(f"agent_id is required for {action}.", {"field": "agent_id"})
    if action in {"archive", "delete"}:
        confirm_agent_id = _optional_str(args, "confirm_agent_id")
        if confirm_agent_id != agent_id:
            raise _InvalidArgs(
                f"confirm_agent_id must match agent_id to {action}.",
                {"field": "confirm_agent_id"},
            )
        payload["confirm_agent_id"] = confirm_agent_id

    return _invoke(("manage_agent",), payload)


HANDLERS: dict[str, Callable[..., str]] = {
    "cursor_models": cursor_models,
    "cursor_repositories": cursor_repositories,
    "cursor_run": cursor_run,
    "cursor_start": cursor_start,
    "cursor_status": cursor_status,
    "cursor_resume": cursor_resume,
    "cursor_cancel": cursor_cancel,
    "cursor_session_send": cursor_session_send,
    "cursor_agent": cursor_agent,
}


def list_tools() -> list[dict[str, Any]]:
    """Return Hermes tool descriptors with handlers attached."""
    return [
        {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["parameters"],
            "handler": HANDLERS[schema["name"]],
        }
        for schema in TOOL_SCHEMAS
    ]


__all__ = [
    "HANDLERS",
    "cursor_agent",
    "cursor_api_key_available",
    "cursor_cancel",
    "cursor_models",
    "cursor_repositories",
    "cursor_resume",
    "cursor_run",
    "cursor_session_send",
    "cursor_start",
    "cursor_status",
    "list_tools",
]
