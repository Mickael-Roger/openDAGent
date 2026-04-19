from __future__ import annotations

import sqlite3
from typing import Any


class Tool:
    """Base class for all native Python tools."""

    name: str = ""
    description: str = ""
    # JSON Schema for the arguments the LLM must pass
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}

    def run(self, conn: sqlite3.Connection, task: dict[str, Any], **kwargs: Any) -> str:
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        """Return the tool schema in the unified format consumed by llm.chat()."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# ── Registry ──────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Tool] = {}


def _register(*tools: Tool) -> None:
    for t in tools:
        _REGISTRY[t.name] = t


def get(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_names() -> list[str]:
    return list(_REGISTRY.keys())


# ── Auto-load built-in tools on first import ──────────────────────────────────

def _load_builtins() -> None:
    from .messaging import PostMessage
    from .artifacts import ReadArtifact, WriteArtifact
    from .files import ReadFile, WriteFile
    from .runtime import CreateTask, AskUser, ListCapabilities
    from .web import WebFetch, WebSearch
    from .image import GenerateImage
    from .opencode_tool import OpencodeTool
    from .email import (
        ListEmails, ReadEmail, SendEmail, SearchEmails, MoveEmail, DeleteEmail,
    )

    _register(
        PostMessage(),
        ReadArtifact(),
        WriteArtifact(),
        ReadFile(),
        WriteFile(),
        CreateTask(),
        ListCapabilities(),
        AskUser(),
        WebFetch(),
        WebSearch(),
        GenerateImage(),
        OpencodeTool(),
        ListEmails(),
        ReadEmail(),
        SendEmail(),
        SearchEmails(),
        MoveEmail(),
        DeleteEmail(),
    )


_load_builtins()
