"""OpenAI-compatible HTTP bridge for Cursor SDK-backed chat completions."""

from __future__ import annotations

import argparse
import hmac
import inspect
import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse
from uuid import uuid4

from hermes_cursor_sdk.client import CursorClient, CursorSDKClient
from hermes_cursor_sdk.config import Settings, load_settings
from hermes_cursor_sdk.errors import map_exception
from hermes_cursor_sdk.results import extract_assistant_text

LOGGER = logging.getLogger("hermes_cursor_sdk.bridge")

OPENAI_PARAM_FIELDS = {
    "frequency_penalty",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "n",
    "presence_penalty",
    "reasoning_effort",
    "response_format",
    "seed",
    "stop",
    "store",
    "temperature",
    "top_logprobs",
    "top_p",
    "user",
}

NON_STREAM_TYPES = (str, bytes, bytearray, dict)


class BridgeError(Exception):
    """OpenAI-shaped error response."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        error_type: str = "invalid_request_error",
        code: str | None = None,
        param: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.error_type = error_type
        self.code = code
        self.param = param

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


class SessionState:
    """Mutable state for a Cursor session guarded by a per-session lock."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.lock = threading.Lock()
        self.last_used = time.monotonic()


class SessionRuntime:
    """Track local bridge sessions, enforce max sessions, and evict idle entries."""

    def __init__(self, *, max_sessions: int, idle_timeout_seconds: float) -> None:
        self.max_sessions = max(1, max_sessions)
        self.idle_timeout_seconds = max(1.0, idle_timeout_seconds)
        self._sessions: dict[str, SessionState] = {}
        self._guard = threading.Lock()

    def acquire(self, session_id: str) -> tuple[SessionState | None, bool, bool]:
        """Return state, acquired, capacity_exceeded."""
        now = time.monotonic()
        with self._guard:
            self._evict_locked(now)
            state = self._sessions.get(session_id)
            if state is None:
                if len(self._sessions) >= self.max_sessions:
                    return None, False, True
                state = SessionState(session_id)
                self._sessions[session_id] = state
            state.last_used = now

        if not state.lock.acquire(blocking=False):
            return state, False, False
        return state, True, False

    def release(self, state: SessionState) -> None:
        state.last_used = time.monotonic()
        state.lock.release()

    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            return {
                "active_sessions": len(self._sessions),
                "max_sessions": self.max_sessions,
                "idle_timeout_seconds": self.idle_timeout_seconds,
            }

    def _evict_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if not state.lock.locked() and now - state.last_used > self.idle_timeout_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)


class BridgeHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server carrying bridge dependencies for request handlers."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        settings: Settings,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(server_address, BridgeRequestHandler)
        self.settings = settings
        self.sessions = SessionRuntime(
            max_sessions=get_setting(
                settings, "bridge_max_sessions", "gateway_max_sessions", default=8
            ),
            idle_timeout_seconds=get_setting(
                settings,
                "bridge_idle_timeout_seconds",
                "gateway_idle_seconds",
                default=1800,
            ),
        )
        self.client_factory = client_factory or (lambda: CursorSDKClient(settings))
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    @property
    def client(self) -> Any:
        with self._client_lock:
            if self._client is None:
                self._client = self.client_factory()
            return self._client


class BridgeRequestHandler(BaseHTTPRequestHandler):
    """HTTP handlers for health, models, and chat completions."""

    protocol_version = "HTTP/1.1"
    server: BridgeHTTPServer

    def do_GET(self) -> None:
        self._dispatch(self._handle_get)

    def do_POST(self) -> None:
        self._dispatch(self._handle_post)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _dispatch(self, handler: Callable[[], int]) -> None:
        started = time.monotonic()
        request_id = self.headers.get("X-Request-Id") or uuid4().hex
        status = HTTPStatus.INTERNAL_SERVER_ERROR
        try:
            status = handler()
        except BridgeError as exc:
            status = HTTPStatus(exc.status)
            self._send_json(exc.status, exc.payload(), request_id=request_id)
        except Exception as exc:
            mapped = map_exception(exc)
            status_code = int(mapped.get("status_code") or HTTPStatus.INTERNAL_SERVER_ERROR)
            try:
                status = HTTPStatus(status_code)
            except ValueError:
                status_code = int(HTTPStatus.INTERNAL_SERVER_ERROR)
                status = HTTPStatus.INTERNAL_SERVER_ERROR
            log_event(
                "bridge_request_failed",
                request_id=request_id,
                path=urlparse(self.path).path,
                error=exc.__class__.__name__,
            )
            error = BridgeError(
                status_code,
                str(mapped.get("message") or "Internal bridge error"),
                error_type="server_error",
                code=str(mapped.get("code") or "internal_error"),
            )
            self._send_json(error.status, error.payload(), request_id=request_id)
        finally:
            log_event(
                "http_request",
                method=self.command,
                path=urlparse(self.path).path,
                status=int(status),
                request_id=request_id,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
            )

    def _handle_get(self) -> int:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            return self._send_json(HTTPStatus.OK, {"status": "ok"})
        if path == "/v1/models":
            self._require_auth()
            models = build_models_payload(self.server.client, self.server.settings)
            return self._send_json(HTTPStatus.OK, models)
        raise BridgeError(HTTPStatus.NOT_FOUND, "Unknown endpoint", code="not_found")

    def _handle_post(self) -> int:
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path != "/v1/chat/completions":
            raise BridgeError(HTTPStatus.NOT_FOUND, "Unknown endpoint", code="not_found")

        self._require_auth()
        payload = self._read_json_body()
        if "tools" in payload or "tool_choice" in payload:
            raise BridgeError(
                HTTPStatus.BAD_REQUEST,
                "Cursor bridge does not support tools or tool_choice",
                code="unsupported_tools",
                param="tools",
            )

        cursor = parse_cursor_extension(payload.get("cursor"))
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise BridgeError(
                HTTPStatus.BAD_REQUEST,
                "messages must be an array",
                code="invalid_messages",
                param="messages",
            )

        stream = bool(payload.get("stream", False))
        session_id = cursor.get("session_id")
        if session_id:
            response = self._handle_session_chat(session_id, payload, cursor, messages, stream)
        else:
            response = send_stateless(self.server.client, payload, cursor, messages, stream)
        raise_for_result_error(response)

        if stream:
            return self._send_stream(response, payload)
        return self._send_json(HTTPStatus.OK, build_completion_payload(response, payload))

    def _handle_session_chat(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        cursor: Mapping[str, Any],
        messages: Sequence[Mapping[str, Any]],
        stream: bool,
    ) -> Any:
        state, acquired, capacity_exceeded = self.server.sessions.acquire(session_id)
        if capacity_exceeded:
            raise BridgeError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Bridge session capacity exceeded",
                error_type="server_error",
                code="session_capacity_exceeded",
            )
        if not acquired or state is None:
            raise BridgeError(
                HTTPStatus.CONFLICT,
                "Session already has an in-flight request",
                code="session_concurrent_request",
            )

        try:
            return send_session(self.server.client, session_id, payload, cursor, messages, stream)
        finally:
            self.server.sessions.release(state)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise BridgeError(
                HTTPStatus.LENGTH_REQUIRED,
                "Content-Length is required",
                code="content_length_required",
            )
        try:
            length = int(content_length)
        except ValueError as exc:
            raise BridgeError(
                HTTPStatus.BAD_REQUEST,
                "Content-Length must be an integer",
                code="invalid_content_length",
            ) from exc
        request_size_limit = get_setting(
            self.server.settings,
            "bridge_request_size_limit",
            "max_request_bytes",
            default=1_048_576,
        )
        if length > request_size_limit:
            raise BridgeError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Request body exceeds bridge size limit",
                code="request_too_large",
            )

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BridgeError(HTTPStatus.BAD_REQUEST, "Request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise BridgeError(HTTPStatus.BAD_REQUEST, "Request JSON must be an object")
        return payload

    def _require_auth(self) -> None:
        expected = self.server.settings.bridge_token or ""
        auth_header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        presented = auth_header[len(prefix) :] if auth_header.startswith(prefix) else ""
        if not expected or not hmac.compare_digest(presented, expected):
            raise BridgeError(
                HTTPStatus.UNAUTHORIZED,
                "Missing or invalid bearer token",
                error_type="authentication_error",
                code="unauthorized",
            )

    def _send_json(
        self,
        status: int | HTTPStatus,
        payload: Mapping[str, Any],
        *,
        request_id: str | None = None,
    ) -> int:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if request_id:
            self.send_header("X-Request-Id", request_id)
        self.end_headers()
        self.wfile.write(body)
        return int(status)

    def _send_stream(self, response: Any, payload: Mapping[str, Any]) -> int:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        completion_id = f"chatcmpl-{uuid4().hex}"
        created = int(time.time())
        model = str(payload.get("model") or "cursor")
        chunks = response if is_stream_response(response) else [response]

        for chunk in chunks:
            text = extract_text(chunk)
            if not text:
                continue
            event = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            }
            self._write_sse(event)

        done = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        self._write_sse(done)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        return int(HTTPStatus.OK)

    def _write_sse(self, event: Mapping[str, Any]) -> None:
        self.wfile.write(f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode())
        self.wfile.flush()


def parse_cursor_extension(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {"session_id": None, "cwd": None, "params": {}}
    if not isinstance(value, Mapping):
        raise BridgeError(
            HTTPStatus.BAD_REQUEST, "cursor extension must be an object", param="cursor"
        )

    session_id = value.get("session_id")
    if session_id in ("", None):
        session_id = None
    elif not isinstance(session_id, str):
        raise BridgeError(
            HTTPStatus.BAD_REQUEST,
            "cursor.session_id must be a string",
            param="cursor.session_id",
        )

    cwd = value.get("cwd")
    if cwd in ("", None):
        cwd = None
    elif not isinstance(cwd, str):
        raise BridgeError(HTTPStatus.BAD_REQUEST, "cursor.cwd must be a string", param="cursor.cwd")

    params = value.get("params") or {}
    if not isinstance(params, Mapping):
        raise BridgeError(
            HTTPStatus.BAD_REQUEST,
            "cursor.params must be an object",
            param="cursor.params",
        )

    return {"session_id": session_id, "cwd": cwd, "params": dict(params)}


def request_params(payload: Mapping[str, Any], cursor: Mapping[str, Any]) -> dict[str, Any]:
    params = {key: payload[key] for key in OPENAI_PARAM_FIELDS if key in payload}
    params.update(dict(cursor.get("params") or {}))
    return params


def send_session(
    client: Any,
    session_id: str,
    payload: Mapping[str, Any],
    cursor: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    stream: bool,
) -> Any:
    params = request_params(payload, cursor)
    ensure = getattr(client, "session_ensure_local", None)
    if callable(ensure):
        call_method(
            ensure,
            session_id=session_id,
            session_key=session_id,
            cwd=cursor.get("cwd"),
            model=payload.get("model"),
            params=params,
        )

    sender = getattr(client, "session_send", None)
    if callable(sender):
        return call_method(
            sender,
            session_id=session_id,
            session_key=session_id,
            messages=list(messages),
            prompt=messages_to_prompt(messages),
            model=payload.get("model"),
            cwd=cursor.get("cwd"),
            params=params,
            stream=stream,
            wait=not stream,
        )
    return send_stateless(client, payload, cursor, messages, stream)


def send_stateless(
    client: Any,
    payload: Mapping[str, Any],
    cursor: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    stream: bool,
) -> Any:
    params = request_params(payload, cursor)
    for name in ("chat_completions", "chat_completion", "complete"):
        method = getattr(client, name, None)
        if callable(method):
            return call_method(
                method,
                messages=list(messages),
                model=payload.get("model"),
                cwd=cursor.get("cwd"),
                params=params,
                stream=stream,
            )

    local_runner = getattr(client, "run_local", None)
    if callable(local_runner):
        cwd = cursor.get("cwd") or get_setting(
            getattr(client, "settings", None), "bridge_cwd", default=None
        )
        cwd = cwd or Path.cwd()
        return call_method(
            local_runner,
            prompt=messages_to_prompt(messages),
            cwd=cwd,
            model=payload.get("model"),
            params=params,
        )

    runner = getattr(client, "run", None)
    if callable(runner):
        return call_method(
            runner, prompt=messages_to_prompt(messages), model=payload.get("model"), **params
        )

    raise BridgeError(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "Cursor client does not expose a chat method",
        error_type="server_error",
        code="client_unavailable",
    )


def call_method(method: Callable[..., Any], **kwargs: Any) -> Any:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(**kwargs)

    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    if accepts_kwargs:
        return method(**kwargs)

    filtered = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return method(**filtered)


def messages_to_prompt(messages: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(extract_text(part) for part in content)
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


def build_models_payload(client: Any, settings: Settings) -> dict[str, Any]:
    models = client_models(client, settings)
    data = []
    for model in models:
        model_id = model.get("id") if isinstance(model, Mapping) else model
        data.append(
            {
                "id": str(model_id),
                "object": "model",
                "context_length": settings.bridge_context_length,
                "context_source": "connector_budget",
                "max_completion_tokens": get_setting(
                    settings,
                    "bridge_max_completion_tokens",
                    "bridge_max_output_tokens",
                    default=8192,
                ),
            }
        )
    return {"object": "list", "data": data}


def client_models(client: Any, settings: Settings) -> list[Any]:
    provider = getattr(client, "list_models", None)
    if callable(provider):
        try:
            models = provider()
            if isinstance(models, list) and models:
                return models
        except Exception as exc:
            log_event("model_list_failed", error=exc.__class__.__name__)
    return [get_setting(settings, "default_model", default="composer-2.5")]


def build_completion_payload(response: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    text = extract_text(response)
    metadata = extract_metadata(response)
    body: dict[str, Any] = {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(payload.get("model") or "cursor"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": metadata.get("finish_reason", "stop"),
            }
        ],
    }
    usage = metadata.get("usage")
    if isinstance(usage, Mapping):
        body["usage"] = dict(usage)
    return body


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        if value.get("result_text") is not None:
            return str(value["result_text"])
        choices = value.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, Mapping):
                delta = first.get("delta")
                if isinstance(delta, Mapping) and delta.get("content") is not None:
                    return str(delta["content"])
                message = first.get("message")
                if isinstance(message, Mapping) and message.get("content") is not None:
                    return str(message["content"])
        for key in ("text", "content", "message"):
            if value.get(key) is not None:
                return str(value[key])
        return ""

    for attr in ("text", "content", "message"):
        text = getattr(value, attr, None)
        if text is not None:
            return str(text)

    try:
        return extract_assistant_text(value)
    except Exception:
        return str(value)


def extract_metadata(value: Any) -> dict[str, Any]:
    metadata = getattr(value, "metadata", None)
    if isinstance(metadata, Mapping):
        return dict(metadata)
    if isinstance(value, Mapping) and isinstance(value.get("metadata"), Mapping):
        result = dict(value["metadata"])
        if isinstance(value.get("usage"), Mapping):
            result.setdefault("usage", value["usage"])
        return result
    if isinstance(value, Mapping) and isinstance(value.get("usage"), Mapping):
        return {"usage": value["usage"]}
    return {}


def is_stream_response(value: Any) -> bool:
    return isinstance(value, Iterable) and not isinstance(value, NON_STREAM_TYPES)


def raise_for_result_error(value: Any) -> None:
    if not isinstance(value, Mapping) or value.get("ok", True):
        return
    error = value.get("error") if isinstance(value.get("error"), Mapping) else {}
    status = int(error.get("status_code") or HTTPStatus.BAD_GATEWAY)
    raise BridgeError(
        status,
        str(error.get("message") or "Cursor SDK request failed"),
        error_type="server_error",
        code=str(error.get("code") or "cursor_error"),
    )


def get_setting(settings: Any, *names: str, default: Any) -> Any:
    if settings is None:
        return default
    for name in names:
        if hasattr(settings, name):
            value = getattr(settings, name)
            return value() if callable(value) and name.startswith("get_") else value
    return default


def log_event(event: str, **fields: Any) -> None:
    record = {"event": event, **fields}
    LOGGER.info(json.dumps(record, separators=(",", ":"), sort_keys=True))


def create_server(
    settings: Settings,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> BridgeHTTPServer:
    host = settings.bridge_host if settings.bridge_expose else "127.0.0.1"
    return BridgeHTTPServer((host, settings.bridge_port), settings, client_factory=client_factory)


def serve_http(
    settings: Settings,
    *,
    client_factory: Callable[[], Any] | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    httpd = create_server(settings, client_factory=client_factory)
    stop_lock = threading.Lock()
    stopped = False

    def request_shutdown(signum: int, _frame: Any) -> None:
        nonlocal stopped
        with stop_lock:
            if stopped:
                return
            stopped = True
        log_event("shutdown_requested", signal=signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_shutdown)

    host, port = httpd.server_address[:2]
    log_event("bridge_started", host=host, port=port, expose=settings.bridge_expose)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        log_event("bridge_stopped", host=host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Hermes Cursor OpenAI-compatible bridge; "
            "legacy stdio bridge helpers remain importable."
        ),
    )
    parser.add_argument(
        "--env-file", type=Path, help="Bridge env file containing HERMES_CURSOR_* values."
    )
    parser.add_argument("--host", help="Host to bind. Ignored unless bridge expose is enabled.")
    parser.add_argument("--port", type=int, help="Port to bind.")
    parser.add_argument("--token", help="Bearer token for /v1 endpoints.")
    parser.add_argument("--expose", action="store_true", help="Allow binding outside 127.0.0.1.")
    parser.add_argument("--max-sessions", type=int, help="Maximum concurrently tracked sessions.")
    parser.add_argument("--idle-timeout-seconds", type=float, help="Idle session eviction timeout.")
    parser.add_argument(
        "--request-size-limit", type=int, help="Maximum JSON request body size in bytes."
    )
    parser.add_argument("--context-length", type=int, help="Advertised model context length.")
    parser.add_argument(
        "--max-completion-tokens", type=int, help="Advertised max completion tokens."
    )
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    settings = load_settings(args.env_file)
    updates: dict[str, Any] = {}
    if args.port is not None:
        updates["bridge_port"] = args.port
    if args.token is not None:
        updates["bridge_token"] = args.token
    if args.expose:
        updates["bridge_expose"] = True
    if args.max_sessions is not None:
        updates["gateway_max_sessions"] = args.max_sessions
    if args.idle_timeout_seconds is not None:
        updates["gateway_idle_seconds"] = int(args.idle_timeout_seconds)
    if args.request_size_limit is not None:
        updates["max_request_bytes"] = args.request_size_limit
    if args.context_length is not None:
        updates["bridge_context_length"] = args.context_length
    if args.max_completion_tokens is not None:
        updates["bridge_max_output_tokens"] = args.max_completion_tokens

    requested_host = args.host or settings.bridge_host
    expose = updates.get("bridge_expose", settings.bridge_expose)
    updates["bridge_host"] = requested_host if expose else "127.0.0.1"
    return replace(settings, **updates)


def serve(
    stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout
) -> int:  # pragma: no cover - legacy stdio bridge
    """Legacy newline-delimited JSON bridge retained for older tests/tools."""
    client = CursorClient(load_settings())
    for line in stdin:
        if not line.strip():
            continue
        payload = json.loads(line)
        result = client.run(payload["prompt"], model=payload.get("model"))
        stdout.write(json.dumps({"text": result.text, "status": result.status}) + "\n")
        stdout.flush()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    if argv is not None and len(argv) == 0:
        parser.print_help()
        return 0
    args = parser.parse_args(argv)
    return serve_http(settings_from_args(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
