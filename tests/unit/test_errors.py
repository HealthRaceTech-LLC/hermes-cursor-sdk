from __future__ import annotations

import pytest

from hermes_cursor_sdk import errors
from hermes_cursor_sdk.errors import (
    AuthMissingError,
    HermesCursorSDKError,
    InvalidArgsError,
    map_exception,
)
from hermes_cursor_sdk.results import error_result


def test_map_exception_auth_missing() -> None:
    mapped = map_exception(AuthMissingError("missing cursor key"))

    assert mapped["code"] == "auth_missing"
    assert mapped["status_code"] == 401
    assert mapped["retryable"] is False
    assert mapped["help_url"]


def test_map_exception_invalid_args() -> None:
    mapped = map_exception(InvalidArgsError("bad input"))

    assert mapped["code"] == "invalid_args"
    assert mapped["status_code"] == 400
    assert mapped["message"] == "bad input"


def test_map_exception_generic_exception_redacts_secrets() -> None:
    mapped = map_exception(Exception("api_key=cursor_secret123 token=abc"))

    assert mapped["code"] == "internal"
    assert mapped["retryable"] is True
    assert "cursor_secret123" not in mapped["message"]
    assert "token=abc" not in mapped["message"]


def test_error_result_shape_has_required_keys_and_redacted_error() -> None:
    result = error_result(
        map_exception(Exception("Authorization: Bearer cursor_secret123")),
        agent_id="agent-1",
        run_id="run-1",
        runtime="local",
    )

    assert set(result) >= {
        "ok",
        "code",
        "agent_id",
        "run_id",
        "runtime",
        "status",
        "result_text",
        "model",
        "usage",
        "git",
        "cost",
        "error",
    }
    assert result["ok"] is False
    assert result["code"] == "internal"
    assert result["error"] is not None
    assert set(result["error"]) == {
        "message",
        "code",
        "retryable",
        "retry_after",
        "request_id",
        "status_code",
        "help_url",
    }
    assert "cursor_secret123" not in result["error"]["message"]


class StatusError(Exception):
    def __init__(self, message: str, status_code: int | str) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("status_code", "message", "code"),
    [
        (400, "bad", "invalid_args"),
        (401, "expired token", "auth_expired"),
        (401, "bad token", "auth_invalid"),
        (403, "forbidden", "integration_missing"),
        (404, "artifact missing", "artifact_not_found"),
        (404, "missing", "not_found"),
        (409, "conflict", "busy"),
        (410, "expired run", "run_expired"),
        (422, "repository invalid", "invalid_repository"),
        (422, "bad payload", "invalid_args"),
        (429, "rate limited", "rate_limited"),
        (500, "boom", "internal"),
        (503, "full", "capacity"),
    ],
)
def test_map_exception_status_codes(status_code: int, message: str, code: str) -> None:
    mapped = map_exception(StatusError(message, status_code))

    assert mapped["code"] == code
    assert mapped["status_code"] == status_code


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (TimeoutError("slow"), "timeout"),
        (TimeoutError("slow socket"), "timeout"),
        (type("RateLimitExceeded", (Exception,), {})("rate"), "rate_limited"),
        (type("AuthenticationFailed", (Exception,), {})("expired auth"), "auth_expired"),
        (type("ForbiddenThing", (Exception,), {})("permission denied"), "integration_missing"),
        (type("ValidationProblem", (Exception,), {})("bad"), "invalid_args"),
        (Exception("repository cannot be used"), "invalid_repository"),
        (Exception("service unavailable"), "capacity"),
        (Exception("unsupported runtime: cloud"), "unsupported_runtime"),
        (Exception("unsupported option"), "unsupported"),
        (Exception("busy conflict"), "busy"),
        (Exception("run expired"), "run_expired"),
        (errors.CursorAgentError("startup failed"), "agent_startup"),
    ],
)
def test_map_exception_by_type_or_message(exc: Exception, code: str) -> None:
    assert map_exception(exc)["code"] == code


def test_map_exception_handles_bad_status_and_unknown_custom_code() -> None:
    bad_status = map_exception(StatusError("bad status", "oops"))
    custom = map_exception(HermesCursorSDKError("custom", code="not-real"))

    assert bad_status["status_code"] is None
    assert bad_status["code"] == "internal"
    assert custom["code"] == "internal"


def test_map_exception_copies_retry_metadata() -> None:
    class Retryable(Exception):
        retryable = True
        retry_after = 3
        request_id = "req-1"
        status_code = 429

    mapped = map_exception(Retryable("rate limited"))

    assert mapped["code"] == "rate_limited"
    assert mapped["retryable"] is True
    assert mapped["retry_after"] == 3
    assert mapped["request_id"] == "req-1"
