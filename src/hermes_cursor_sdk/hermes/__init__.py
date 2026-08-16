"""Cursor-managed context engine for Hermes.

Registers as a Hermes context engine via ``ctx.register_context_engine()``.
Cursor agents manage their own context window — this engine surfaces that
reality to Hermes without attempting any client-side compression.

Select it with ``context.engine: cursor`` in ``~/.hermes/config.yaml``.

Entry point::
    [project.entry-points."hermes_agent.plugins"]
    cursor_context = "hermes_cursor_sdk.hermes"
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hermes_cursor_sdk.hermes.engine import CursorContextEngine

if TYPE_CHECKING:
    from typing import Any


def register(ctx: Any) -> None:
    """Register the Cursor context engine with Hermes.

    The engine is a pass-through: Cursor's own agent runtime manages
    context internally, so ``should_compress()`` always returns False
    and ``compress()`` is a safe no-op. The engine surfaces
    ``compression_state = "unknown"`` and "Cursor-managed context"
    disclosure via ``get_status()`` so Hermes status bars display
    honest readings.
    """
    ctx.register_context_engine(CursorContextEngine())
