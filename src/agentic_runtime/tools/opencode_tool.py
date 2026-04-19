"""
OpencodeTool — delegates coding work to an opencode session via the persistent
opencode serve API managed by agentic_runtime.opencode.server.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from . import Tool
from ..opencode.server import get_server

logger = logging.getLogger(__name__)


class OpencodeTool(Tool):
    name = "opencode"
    description = (
        "Execute a coding task using the opencode AI coding agent. "
        "Provide a clear, detailed prompt describing what code to write, refactor, "
        "test, review, or debug. Returns the agent's complete response."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The coding task to perform. Be specific: include file paths, "
                               "language, requirements, and any relevant context.",
            },
            "working_directory": {
                "type": "string",
                "description": "Optional working directory path to pass as context in the prompt.",
            },
        },
        "required": ["prompt"],
    }

    def run(self, conn: sqlite3.Connection, task: dict[str, Any], **kwargs: Any) -> str:
        server = get_server()
        if server is None or not server.is_alive():
            return "ERROR: opencode server is not running. Coding capabilities are unavailable."

        prompt: str = kwargs["prompt"]
        working_directory: str | None = kwargs.get("working_directory")

        if working_directory:
            full_prompt = f"Working directory: {working_directory}\n\n{prompt}"
        else:
            full_prompt = prompt

        session_id: str | None = None
        try:
            session_id = server.create_session()
            reply = server.send_message(session_id, full_prompt)
            return reply or "(no response from opencode)"
        except Exception as exc:
            logger.error("opencode tool error: %s", exc)
            return f"ERROR: opencode request failed: {exc}"
        finally:
            if session_id is not None:
                server.delete_session(session_id)
