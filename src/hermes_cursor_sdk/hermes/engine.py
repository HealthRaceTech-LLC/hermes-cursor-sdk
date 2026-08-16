"""Cursor-managed context engine.

Cursor agents run with their own internal context management (budget
allocation, proactive compaction, cache-tier eviction). This engine
surfaces that reality to Hermes: it reports token counts from the
cursor-sdk bridge, tells Hermes compression is handled by Cursor, and
discloses "Cursor-managed context" in status bars.

The engine is a *read-only observer* in Hermes's compression pipeline —
it never compacts or mutates the message list. Hermes's ``run_agent``
loop reads ``should_compress()`` and ``compress()`` from whatever engine
is active; returning ``False`` + an identity passthrough means Cursor's
internal compaction path operates unimpeded.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Hermes ContextEngine ABC — available when this module is loaded by
# a Hermes Python process. Falls back to ``object`` in dev/test.
# ---------------------------------------------------------------------------
try:
    from agent.context_engine import (  # ty: ignore[unresolved-import]
        ContextEngine,  # type: ignore[import-untyped]
    )

    _BASE: type = ContextEngine
except ImportError:
    _BASE = object


# ---------------------------------------------------------------------------
# Status labels
# ---------------------------------------------------------------------------
COMPRESSION_STATE_LABELS: dict[int, str] = {
    0: "unknown",
    1: "uncompressed",
    2: "compressing",
    3: "compressed",
}


class CursorContextEngine(_BASE):  # ty: ignore[unsupported-base]
    """Pass-through context engine for Cursor-managed agents.

    Cursor agents handle context internally — there is nothing for
    Hermes to compact or budget. This engine reports token usage so
    Hermes status bars show the current fill, but always declines
    compression and surfaces the honest disclosure that context is
    Cursor-managed.
    """

    def __init__(self) -> None:
        # Identity
        self.name: str = "cursor"

        # Token state (read by run_agent.py for display/logging)
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0
        self.threshold_tokens: int = 0
        self.context_length: int = 0
        self.compression_count: int = 0

        # Compaction parameters
        self.threshold_percent: float = 1.0
        self.protect_first_n: int = 3
        self.protect_last_n: int = 6
        self.emit_automatic_compaction_status: bool = False

        # Cursor-specific disclosure
        self.compression_state: int = 0
        self._compression_state_label = COMPRESSION_STATE_LABELS[0]

    # ---- Core interface --------------------------------------------------

    def update_from_response(self, usage: dict[str, Any]) -> None:
        """Ingest token usage from a completed Cursor turn.

        The Cursor bridge delivers OpenAI-shaped usage via SSE. We map
        ``prompt_tokens`` (the negotiated key for every Hermes backend)
        and the newer canonical buckets (input_tokens, output_tokens,
        cache_read_tokens, cache_write_tokens, reasoning_tokens).
        """
        if not usage:
            return
        self.last_prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        self.last_completion_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        self.last_total_tokens = int(usage.get("total_tokens") or 0)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        """Never request compression — Cursor manages it internally."""
        return False

    def compress(  # type: ignore[override]
        self,
        messages: list[dict[str, Any]],
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        force: bool = False,
        memory_context: str = "",
    ) -> list[dict[str, Any]]:
        """Safe no-op: return messages unchanged."""
        return messages

    # ---- Optional hooks ------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return status dict with Cursor-managed disclosure.

        Includes ``compression_state: "unknown"`` and
        ``context_manager: "cursor"`` so Hermes status bars show
        honest "Cursor-managed context" labels.
        """
        last_prompt = self.last_prompt_tokens if self.last_prompt_tokens > 0 else 0
        return {
            "last_prompt_tokens": last_prompt,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, last_prompt / self.context_length * 100) if self.context_length else 0
            ),
            "compression_count": self.compression_count,
            # Cursor-specific disclosure fields
            "compression_state": self._compression_state_label,
            "context_manager": "cursor",
        }

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        """Update context window on model switch / fallback."""
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)

    def should_compress_info(self, prompt_tokens: int | None = None) -> tuple[bool, str | None]:
        return False, None
