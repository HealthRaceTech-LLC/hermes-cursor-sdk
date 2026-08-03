"""Result dictionaries returned by the Hermes Cursor SDK connector."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class UsageDict(TypedDict):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    total_tokens: int
    reasoning_tokens: int


class GitDict(TypedDict):
    branch: str | None
    commit: str | None
    pr_url: str | None


class CostDict(TypedDict):
    raw_cost_cents: float | None
    charged_cents: float | None
    pending: bool


class ErrorDict(TypedDict):
    message: str
    code: str
    retryable: bool
    retry_after: int | float | str | None
    request_id: str | None
    status_code: int | None
    help_url: str | None


class ResultDict(TypedDict):
    ok: bool
    code: str | None
    agent_id: str | None
    run_id: str | None
    runtime: str | None
    status: str | None
    result_text: str | None
    model: str | dict[str, Any] | None
    usage: UsageDict
    git: GitDict
    cost: CostDict
    error: ErrorDict | None
    metadata: NotRequired[dict[str, Any]]


def truncate_text(text: str | None, limit: int = 100_000) -> str:
    """Return text capped at limit characters with a clear truncation suffix."""

    if not text:
        return ""
    if len(text) <= limit:
        return text
    suffix = f"\n\n[truncated {len(text) - limit} characters]"
    return text[: max(0, limit - len(suffix))] + suffix


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            value = getattr(obj, name)
            return value() if callable(value) and name.startswith("get_") else value
    return default


def _usage_dict(usage: Any | None = None) -> UsageDict:
    input_tokens = int(_value(usage, "input_tokens", "prompt_tokens", default=0) or 0)
    output_tokens = int(_value(usage, "output_tokens", "completion_tokens", default=0) or 0)
    cache_read_tokens = int(
        _value(usage, "cache_read_tokens", "cache_read_input_tokens", default=0) or 0
    )
    cache_write_tokens = int(
        _value(usage, "cache_write_tokens", "cache_creation_input_tokens", default=0) or 0
    )
    reasoning_tokens = int(_value(usage, "reasoning_tokens", default=0) or 0)
    total_tokens = int(_value(usage, "total_tokens", default=0) or 0)
    if not total_tokens:
        total_tokens = (
            input_tokens + output_tokens + cache_read_tokens + cache_write_tokens + reasoning_tokens
        )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def to_openai_usage(usage: Any | None = None) -> dict[str, Any] | None:
    """OpenAI-shaped usage for Hermes context meters.

    Hermes ``normalize_usage`` / statusbars read ``prompt_tokens`` and
    ``completion_tokens``. Match Hermes CanonicalUsage: prompt = input +
    cache_read + cache_write. Return ``None`` when counts are missing/zero so
    clients do not latch a fake 0% occupancy.
    """

    if usage is None:
        return None
    cursor = _usage_dict(usage)
    prompt_tokens = (
        cursor["input_tokens"] + cursor["cache_read_tokens"] + cursor["cache_write_tokens"]
    )
    completion_tokens = cursor["output_tokens"]
    # Hermes meters need prompt_tokens; total-only payloads are not usable.
    if prompt_tokens == 0 and completion_tokens == 0:
        return None
    return {
        **cursor,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _git_dict(git: Any | None = None) -> GitDict:
    return {
        "branch": _value(git, "branch"),
        "commit": _value(git, "commit", "sha"),
        "pr_url": _value(git, "pr_url", "pull_request_url"),
    }


def _cost_dict(cost: Any | None = None) -> CostDict:
    return {
        "raw_cost_cents": _value(cost, "raw_cost_cents"),
        "charged_cents": _value(cost, "charged_cents"),
        "pending": bool(_value(cost, "pending", default=False)),
    }


def _message_text(message: Any) -> str:
    parts: list[str] = []
    if isinstance(message, str):
        return message
    if _value(message, "type") not in {None, "assistant"}:
        return ""
    payload = _value(message, "message", default=message)
    content = _value(payload, "content", default=[])
    if isinstance(content, str):
        return content
    for block in content or []:
        block_type = _value(block, "type")
        if block_type in {None, "text"}:
            text = _value(block, "text", "content")
            if text:
                parts.append(str(text))
    return "".join(parts)


def extract_assistant_text(run: Any) -> str:
    """Extract final assistant text, preferring SDK run.text() when available."""

    text_attr = getattr(run, "text", None)
    if callable(text_attr):
        try:
            value = text_attr()
            if value:
                return str(value)
        except Exception:
            # Some SDK objects expose text() before it is ready; fall back to messages.
            pass
    elif text_attr:
        return str(text_attr)

    # cursor_sdk.RunResult exposes the final assistant reply on `.result`.
    for name in ("result", "result_text", "output"):
        value = _value(run, name)
        if isinstance(value, str) and value:
            return value

    messages_attr = getattr(run, "messages", None)
    messages = messages_attr() if callable(messages_attr) else messages_attr
    if messages is None:
        messages = _value(run, "conversation", "content", default=[])
    return "".join(_message_text(message) for message in messages or [])


def ok_result(
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
    runtime: str | None = None,
    status: str | None = "finished",
    result_text: str | None = "",
    model: str | dict[str, Any] | None = None,
    usage: Any | None = None,
    git: Any | None = None,
    cost: Any | None = None,
    code: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ResultDict:
    result: ResultDict = {
        "ok": True,
        "code": code,
        "agent_id": agent_id,
        "run_id": run_id,
        "runtime": runtime,
        "status": status,
        "result_text": truncate_text(result_text),
        "model": model,
        "usage": _usage_dict(usage),
        "git": _git_dict(git),
        "cost": _cost_dict(cost),
        "error": None,
    }
    if metadata:
        result["metadata"] = metadata
    return result


def error_result(
    error: ErrorDict | dict[str, Any],
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
    runtime: str | None = None,
    status: str | None = "error",
    result_text: str | None = None,
    model: str | dict[str, Any] | None = None,
    usage: Any | None = None,
    git: Any | None = None,
    cost: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> ResultDict:
    normalized_error: ErrorDict = {
        "message": str(error.get("message", "Cursor SDK request failed")),
        "code": str(error.get("code", "internal")),
        "retryable": bool(error.get("retryable", False)),
        "retry_after": error.get("retry_after"),
        "request_id": error.get("request_id"),
        "status_code": error.get("status_code"),
        "help_url": error.get("help_url"),
    }
    result: ResultDict = {
        "ok": False,
        "code": normalized_error["code"],
        "agent_id": agent_id,
        "run_id": run_id,
        "runtime": runtime,
        "status": status,
        "result_text": truncate_text(result_text),
        "model": model,
        "usage": _usage_dict(usage),
        "git": _git_dict(git),
        "cost": _cost_dict(cost),
        "error": normalized_error,
    }
    if metadata:
        result["metadata"] = metadata
    return result
