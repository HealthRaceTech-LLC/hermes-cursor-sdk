"""Tests for the Cursor context engine plugin."""

from __future__ import annotations

from hermes_cursor_sdk.hermes.engine import CursorContextEngine


class TestCursorContextEngine:
    def test_name(self) -> None:
        engine = CursorContextEngine()
        assert engine.name == "cursor"

    def test_never_compresses(self) -> None:
        engine = CursorContextEngine()
        assert engine.should_compress() is False
        assert engine.should_compress(prompt_tokens=50000) is False

    def test_compress_is_identity(self) -> None:
        engine = CursorContextEngine()
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = engine.compress(messages)
        assert result is messages

    def test_update_from_response_populates_tokens(self) -> None:
        engine = CursorContextEngine()
        engine.update_from_response(
            {"prompt_tokens": 3500, "completion_tokens": 500, "total_tokens": 4000}
        )
        assert engine.last_prompt_tokens == 3500
        assert engine.last_completion_tokens == 500
        assert engine.last_total_tokens == 4000

    def test_update_from_response_prefers_canonical_keys(self) -> None:
        engine = CursorContextEngine()
        engine.update_from_response(
            {"input_tokens": 7000, "output_tokens": 1200, "total_tokens": 8200}
        )
        assert engine.last_prompt_tokens == 7000
        assert engine.last_completion_tokens == 1200
        assert engine.last_total_tokens == 8200

    def test_update_from_response_handles_empty(self) -> None:
        engine = CursorContextEngine()
        engine.update_from_response({})
        assert engine.last_prompt_tokens == 0
        assert engine.last_completion_tokens == 0
        assert engine.last_total_tokens == 0

    def test_get_status_discloses_cursor_managed(self) -> None:
        engine = CursorContextEngine()
        status = engine.get_status()
        assert status["compression_state"] == "unknown"
        assert status["context_manager"] == "cursor"

    def test_get_status_computes_usage_percent(self) -> None:
        engine = CursorContextEngine()
        engine.update_model("cursor-grok-4.5", 200000)
        engine.update_from_response(
            {"prompt_tokens": 100000, "completion_tokens": 5000, "total_tokens": 105000}
        )
        status = engine.get_status()
        assert status["usage_percent"] == 50
        assert status["context_length"] == 200000
        assert status["last_prompt_tokens"] == 100000

    def test_update_model_sets_context_and_threshold(self) -> None:
        engine = CursorContextEngine()
        engine.update_model("composer-2.5", 200000)
        assert engine.context_length == 200000
        assert engine.threshold_tokens == 200000  # threshold_percent=1.0

    def test_registration_is_safe_when_hermes_absent(self) -> None:
        """The hermes module must import without agent.context_engine."""
        import importlib

        import hermes_cursor_sdk.hermes as hermes_mod

        importlib.reload(hermes_mod)
        assert hasattr(hermes_mod, "register")

    def test_context_length_clamps_negative_prompt(self) -> None:
        engine = CursorContextEngine()
        engine.last_prompt_tokens = -1
        status = engine.get_status()
        assert status["last_prompt_tokens"] == 0
