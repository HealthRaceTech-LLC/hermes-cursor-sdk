"""Hermes plugin entry point (tools land in a later stacked PR)."""

from __future__ import annotations

from typing import Any


def register(ctx: Any) -> None:
    """No-op until Phase 1 tools PR lands."""
    return None


__all__ = ["register"]
