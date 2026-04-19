from __future__ import annotations

import sqlite3
from typing import Any

from . import Tool
from ..ids import new_id
from ..time import utc_now_iso


class PostMessage(Tool):
    name = "post_message"
    description = (
        "Post a message to the project chat, visible to the user. "
        "Optionally attach artifact IDs so they are displayed inline in the chat."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The message text to post (markdown supported).",
            },
            "artifact_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of artifact IDs to attach to the message.",
            },
        },
        "required": ["content"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        content: str,
        artifact_ids: list[str] | None = None,
        **_: Any,
    ) -> str:
        full_content = content
        if artifact_ids:
            refs = "".join(f"<!-- artifact:{aid} -->" for aid in artifact_ids)
            full_content = f"{content}\n{refs}"

        message_id = new_id("msg")
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO goal_messages
                (message_id, goal_id, project_id, author_type, source_channel,
                 content, message_ts, created_at)
            VALUES (?, ?, ?, 'system', 'web', ?, ?, ?)
            """,
            (message_id, task["goal_id"], task["project_id"], full_content, now, now),
        )
        conn.commit()
        return "Message posted."
