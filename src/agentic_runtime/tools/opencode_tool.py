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
        if server is None:
            return "ERROR: opencode server was not initialised. Check startup logs."
        if not server.is_alive():
            return "ERROR: opencode server process has exited. Coding capabilities are unavailable."

        prompt: str = kwargs["prompt"]
        working_directory: str | None = kwargs.get("working_directory")

        if working_directory:
            full_prompt = f"Working directory: {working_directory}\n\n{prompt}"
        else:
            full_prompt = prompt

        session_id: str | None = None
        try:
            session_id = server.create_session()
            logger.info(
                "opencode session %s: sending prompt (%d chars) for task %s",
                session_id, len(full_prompt), task.get("task_id", "?"),
            )
            reply = server.send_message(session_id, full_prompt)
            if reply:
                logger.info(
                    "opencode session %s: received reply (%d chars)",
                    session_id, len(reply),
                )
            else:
                logger.warning("opencode session %s: empty reply", session_id)
            return reply or "(no response from opencode)"
        except Exception as exc:
            logger.error(
                "opencode tool error for task %s: %s",
                task.get("task_id", "?"), exc,
                exc_info=True,
            )
            return f"ERROR: opencode request failed: {exc}"
        finally:
            if session_id is not None:
                server.delete_session(session_id)
