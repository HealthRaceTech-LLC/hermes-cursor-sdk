from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hermes_cursor_sdk.results import (
    error_result,
    extract_assistant_text,
    ok_result,
    to_openai_usage,
    truncate_text,
)


@dataclass
class Block:
    text: str
    type: str = "text"


@dataclass
class Payload:
    content: list[Any]


@dataclass
class Message:
    message: Payload
    type: str = "assistant"


def test_truncate_text_over_limit() -> None:
    truncated = truncate_text("abcdef", limit=5)

    assert truncated.endswith("[truncated 1 characters]")
    assert len(truncated) > 5


def test_extract_assistant_text_uses_run_result_field() -> None:
    @dataclass
    class RunResult:
        result: str
        status: str = "finished"

    assert extract_assistant_text(RunResult(result="cursor-bridge-ok")) == "cursor-bridge-ok"
    assert extract_assistant_text({"result_text": "from mapping"}) == "from mapping"


def test_extract_assistant_text_falls_back_to_messages() -> None:
    class Run:
        def text(self) -> str:
            raise RuntimeError("text unavailable")

        def messages(self) -> list[Any]:
            return [
                {"type": "user", "message": {"content": "ignored"}},
                Message(Payload([Block("hello "), {"type": "text", "content": "world"}])),
            ]

    assert extract_assistant_text(Run()) == "hello world"


def test_extract_assistant_text_uses_conversation_fallback() -> None:
    run = {
        "conversation": [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "from dict"}]}}
        ]
    }

    assert extract_assistant_text(run) == "from dict"


def test_ok_result_normalizes_usage_git_cost_and_metadata() -> None:
    result = ok_result(
        agent_id="agent-1",
        run_id="run-1",
        result_text="done",
        usage={"prompt_tokens": 2, "completion_tokens": 3},
        git={"sha": "abc", "pull_request_url": "https://example.test/pr/1"},
        cost={"charged_cents": 1.5, "pending": True},
        metadata={"finish_reason": "stop"},
    )

    assert result["usage"]["total_tokens"] == 5
    assert result["git"]["commit"] == "abc"
    assert result["git"]["pr_url"] == "https://example.test/pr/1"
    assert result["cost"]["charged_cents"] == 1.5
    assert result["cost"]["pending"] is True
    assert result["metadata"] == {"finish_reason": "stop"}


def test_to_openai_usage_matches_hermes_canonical_prompt_tokens() -> None:
    usage = to_openai_usage(
        {
            "input_tokens": 1000,
            "cache_read_tokens": 200,
            "cache_write_tokens": 0,
            "output_tokens": 50,
        }
    )

    assert usage is not None
    assert usage["prompt_tokens"] == 1200
    assert usage["completion_tokens"] == 50
    assert usage["total_tokens"] == 1250
    assert usage["input_tokens"] == 1000
    assert usage["cache_read_tokens"] == 200


def test_to_openai_usage_omits_missing_and_zero_counts() -> None:
    assert to_openai_usage(None) is None
    assert to_openai_usage({}) is None
    assert to_openai_usage({"total_tokens": 2}) is None
    assert to_openai_usage({"input_tokens": 0, "output_tokens": 0}) is None


def test_error_result_fields_and_defaults() -> None:
    result = error_result(
        {"message": "failed", "code": "capacity", "retryable": True, "status_code": 503},
        result_text="partial",
        metadata={"request_id": "req-1"},
    )

    assert result["ok"] is False
    assert result["code"] == "capacity"
    assert result["error"] == {
        "message": "failed",
        "code": "capacity",
        "retryable": True,
        "retry_after": None,
        "request_id": None,
        "status_code": 503,
        "help_url": None,
    }
    assert result["result_text"] == "partial"
    assert result["metadata"] == {"request_id": "req-1"}
