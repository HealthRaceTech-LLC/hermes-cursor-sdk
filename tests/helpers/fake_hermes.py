"""Small Hermes test doubles."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PluginContext:
    """Minimal Hermes plugin context that records registrations."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self.skills: list[dict[str, Any]] = []

    def register_tool(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        # Support both keyword-only and mixed Hermes call styles.
        if args:
            raise TypeError("register_tool expects keyword arguments")
        self.tools.append(dict(kwargs))
        return self.tools[-1]

    def register_skill(
        self, name: str | None = None, path: Path | str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        payload = dict(kwargs)
        if name is not None:
            payload["name"] = name
        if path is not None:
            payload["path"] = path
        self.skills.append(payload)
        return self.skills[-1]
