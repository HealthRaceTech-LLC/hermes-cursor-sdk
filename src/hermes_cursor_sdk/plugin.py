"""Hermes plugin entry point for Cursor SDK tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hermes_cursor_sdk.schemas import TOOL_SCHEMAS
from hermes_cursor_sdk.tools import HANDLERS, cursor_api_key_available


def register(ctx: Any) -> None:
    """Register Cursor SDK tools and the bundled skill with Hermes.

    Registration must not perform network I/O or fail when credentials are absent.
    """
    for schema in TOOL_SCHEMAS:
        name = schema["name"]
        ctx.register_tool(
            name=name,
            toolset="cursor",
            schema=schema,
            handler=HANDLERS[name],
            check_fn=cursor_api_key_available,
            requires_env=["CURSOR_API_KEY"],
            description=schema.get("description", ""),
        )

    skill_path = Path(__file__).parent / "skills" / "cursor-sdk" / "SKILL.md"
    # Hermes PluginContext.register_skill accepts (name, path) or kwargs depending
    # on version; try the documented positional form first.
    try:
        ctx.register_skill("cursor-sdk", skill_path)
    except TypeError:
        ctx.register_skill(name="cursor-sdk", path=skill_path)


__all__ = ["register"]
