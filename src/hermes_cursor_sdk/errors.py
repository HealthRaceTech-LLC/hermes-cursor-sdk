"""Error normalization for Hermes Cursor SDK operations."""

from __future__ import annotations

import builtins
import re
import socket
from typing import Any

try:  # pragma: no cover - exercised only when the real SDK is installed
    import cursor_sdk as _cursor_sdk
except ImportError:  # pragma: no cover - tests use the stubs below
    _cursor_sdk = None


class _StubCursorSDKError(Exception):
    """Fallback SDK error for tests when cursor_sdk is unavailable."""


CursorSDKError = getattr(_cursor_sdk, "CursorSDKError", _StubCursorSDKError)
CursorAgentError = getattr(_cursor_sdk, "CursorAgentError", CursorSDKError)
AuthenticationError = getattr(_cursor_sdk, "AuthenticationError", CursorSDKError)
AuthorizationError = getattr(_cursor_sdk, "AuthorizationError", CursorSDKError)
NotFoundError = getattr(_cursor_sdk, "NotFoundError", CursorSDKError)
RateLimitError = getattr(_cursor_sdk, "RateLimitError", CursorSDKError)
BadRequestError = getattr(_cursor_sdk, "BadRequestError", CursorSDKError)

ERROR_CODES = {
    "auth_missing",
    "agent_startup",
    "auth_invalid",
    "auth_expired",
    "integration_missing",
    "sdk_incompatible",
    "unsupported_runtime",
    "capacity",
    "invalid_repository",
    "artifact_not_found",
    "run_failed",
    "not_found",
    "invalid_args",
    "unsupported",
    "busy",
    "rate_limited",
    "run_expired",
    "internal",
    "timeout",
}

HELP_URLS = {
    "auth_missing": "https://cursor.com/dashboard/integrations",
    "auth_invalid": "https://cursor.com/dashboard/integrations",
    "auth_expired": "https://cursor.com/dashboard/integrations",
    "integration_missing": "https://cursor.com/docs/sdk",
    "sdk_incompatible": "https://cursor.com/docs/sdk/python",
    "unsupported_runtime": "https://cursor.com/docs/sdk/python",
    "invalid_repository": "https://cursor.com/docs/sdk/python",
    "rate_limited": "https://cursor.com/docs/sdk/python",
}

_SECRET_PATTERNS = [
    re.compile(r"cursor_[A-Za-z0-9_\-]+"),
    re.compile(r"(authorization|api[-_ ]?key|token|secret)\s*[:=]\s*[^\s,;]+", re.I),
    re.compile(r"(headers?)\s*[:=]\s*\{.*?\}", re.I),
]


def _safe_message(
    exc: BaseException | str | None, fallback: str = "Cursor SDK request failed"
) -> str:
    message = str(exc or "").strip() or fallback
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub(
            lambda match: f"{match.group(1)}=<redacted>" if match.lastindex else "<redacted>",
            message,
        )
    return message[:1000]


def _get_attr(exc: BaseException, *names: str) -> Any:
    for name in names:
        if hasattr(exc, name):
            value = getattr(exc, name)
            return value() if callable(value) and name.startswith("get_") else value
    return None


class HermesCursorSDKError(Exception):
    """Base exception with normalized Hermes error metadata."""

    code = "internal"
    retryable = False
    status_code: int | None = None

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retryable: bool | None = None,
        retry_after: int | float | str | None = None,
        request_id: str | None = None,
        status_code: int | None = None,
        help_url: str | None = None,
    ) -> None:
        super().__init__(_safe_message(message))
        self.code = code or self.code
        self.retryable = self.retryable if retryable is None else bool(retryable)
        self.retry_after = retry_after
        self.request_id = request_id
        self.status_code = status_code if status_code is not None else self.status_code
        self.help_url = help_url


class ConfigurationError(HermesCursorSDKError):
    """Raised when plugin configuration is invalid."""

    code = "invalid_args"


class AuthMissingError(HermesCursorSDKError):
    code = "auth_missing"
    status_code = 401


class AgentStartupError(HermesCursorSDKError):
    code = "agent_startup"
    retryable = True


class AuthInvalidError(HermesCursorSDKError):
    code = "auth_invalid"
    status_code = 401


class AuthExpiredError(HermesCursorSDKError):
    code = "auth_expired"
    status_code = 401


class IntegrationMissingError(HermesCursorSDKError):
    code = "integration_missing"
    status_code = 403


class CursorSDKUnavailableError(HermesCursorSDKError):
    """Raised when the Cursor SDK cannot be imported or initialized."""

    code = "sdk_incompatible"


class CursorExecutionError(HermesCursorSDKError):
    """Raised when a Cursor SDK call fails."""

    code = "run_failed"


class UnsupportedRuntimeError(HermesCursorSDKError):
    code = "unsupported_runtime"


class CapacityError(HermesCursorSDKError):
    code = "capacity"
    retryable = True
    status_code = 503


class InvalidRepositoryError(HermesCursorSDKError):
    code = "invalid_repository"
    status_code = 422


class ArtifactNotFoundError(HermesCursorSDKError):
    code = "artifact_not_found"
    status_code = 404


class RunFailedError(HermesCursorSDKError):
    code = "run_failed"


class ResourceNotFoundError(HermesCursorSDKError):
    code = "not_found"
    status_code = 404


class InvalidArgsError(HermesCursorSDKError):
    code = "invalid_args"
    status_code = 400


class UnsupportedError(HermesCursorSDKError):
    code = "unsupported"
    status_code = 400


class BusyError(HermesCursorSDKError):
    code = "busy"
    retryable = True
    status_code = 409


class RateLimitedError(HermesCursorSDKError):
    code = "rate_limited"
    retryable = True
    status_code = 429


class RunExpiredError(HermesCursorSDKError):
    code = "run_expired"
    status_code = 410


class InternalError(HermesCursorSDKError):
    code = "internal"
    retryable = True


class TimeoutError(HermesCursorSDKError):
    code = "timeout"
    retryable = True


def _class_name(exc: BaseException) -> str:
    return exc.__class__.__name__.lower()


def _code_from_status(status_code: int | None, message: str) -> str | None:
    lower = message.lower()
    if status_code == 400:
        return "invalid_args"
    if status_code == 401:
        return "auth_expired" if "expired" in lower else "auth_invalid"
    if status_code == 403:
        return "integration_missing"
    if status_code == 404:
        return "artifact_not_found" if "artifact" in lower else "not_found"
    if status_code == 409:
        return "busy"
    if status_code == 410:
        return "run_expired"
    if status_code == 422:
        return "invalid_repository" if "repo" in lower or "repository" in lower else "invalid_args"
    if status_code == 429:
        return "rate_limited"
    if status_code in {500, 502}:
        return "internal"
    if status_code in {503, 504}:
        return "capacity"
    return None


def _code_from_exception(exc: BaseException, status_code: int | None, message: str) -> str:
    name = _class_name(exc)
    lower = message.lower()
    status_code_match = _code_from_status(status_code, message)
    if status_code_match:
        return status_code_match
    if isinstance(exc, (TimeoutError, builtins.TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, RateLimitError) or "rate" in name:
        return "rate_limited"
    if isinstance(exc, AuthenticationError) or "auth" in name:
        return "auth_expired" if "expired" in lower else "auth_invalid"
    if isinstance(exc, AuthorizationError) or "forbidden" in name or "permission" in lower:
        return "integration_missing"
    if isinstance(exc, NotFoundError) or "notfound" in name or "not found" in lower:
        return "artifact_not_found" if "artifact" in lower else "not_found"
    if isinstance(exc, BadRequestError) or "validation" in name or "badrequest" in name:
        return "invalid_args"
    if "repository" in lower or "repo" in lower:
        return "invalid_repository"
    if "capacity" in lower or "unavailable" in lower or "overloaded" in lower:
        return "capacity"
    if "unsupported runtime" in lower:
        return "unsupported_runtime"
    if "unsupported" in lower:
        return "unsupported"
    if "busy" in lower or "conflict" in lower:
        return "busy"
    if "expired" in lower:
        return "run_expired"
    if isinstance(exc, CursorAgentError):
        return "agent_startup"
    return "internal"


def map_exception(exc: BaseException) -> dict[str, Any]:
    """Map an arbitrary exception to Hermes' stable error envelope."""

    message = _safe_message(exc)
    status_code = _get_attr(exc, "status_code", "status", "http_status")
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None

    if isinstance(exc, HermesCursorSDKError):
        code = exc.code
        retryable = exc.retryable
        retry_after = exc.retry_after
        request_id = exc.request_id
        status_code = exc.status_code if exc.status_code is not None else status_code
        help_url = exc.help_url or HELP_URLS.get(code)
    else:
        code = _code_from_exception(exc, status_code, message)
        retryable = bool(
            _get_attr(exc, "is_retryable", "retryable")
            or code in {"capacity", "rate_limited", "busy", "timeout", "internal"}
        )
        retry_after = _get_attr(exc, "retry_after")
        request_id = _get_attr(exc, "request_id", "x_request_id", "trace_id")
        help_url = HELP_URLS.get(code)

    if code not in ERROR_CODES:
        code = "internal"

    return {
        "message": message,
        "code": code,
        "retryable": bool(retryable),
        "retry_after": retry_after,
        "request_id": request_id,
        "status_code": status_code,
        "help_url": help_url,
    }
