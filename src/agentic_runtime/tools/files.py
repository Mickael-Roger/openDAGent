from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import Tool


class ReadFile(Tool):
    name = "read_file"
    description = (
        "Read a file from the task workspace. "
        "The path is relative to the task workspace root."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace root.",
            },
        },
        "required": ["path"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        path: str,
        **_: Any,
    ) -> str:
        workspace = task.get("workspace_path")
        if not workspace:
            return "Error: no workspace is attached to this task."
        target = (Path(workspace) / path).resolve()
        if not str(target).startswith(str(Path(workspace).resolve())):
            return "Error: path escapes the workspace."
        if not target.exists():
            return f"Error: '{path}' does not exist in the workspace."
        try:
            return target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"Error reading file: {exc}"


class WriteFile(Tool):
    name = "write_file"
    description = (
        "Write content to a file in the task workspace. "
        "Creates parent directories as needed. "
        "The path is relative to the task workspace root."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the workspace root.",
            },
            "content": {
                "type": "string",
                "description": "Content to write.",
            },
        },
        "required": ["path", "content"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        path: str,
        content: str,
        **_: Any,
    ) -> str:
        workspace = task.get("workspace_path")
        if not workspace:
            return "Error: no workspace is attached to this task."
        target = (Path(workspace) / path).resolve()
        if not str(target).startswith(str(Path(workspace).resolve())):
            return "Error: path escapes the workspace."
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Written: {path}"
        except OSError as exc:
            return f"Error writing file: {exc}"
