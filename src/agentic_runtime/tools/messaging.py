from __future__ import annotations

import sqlite3
from typing import Any

from . import Tool
from ..ids import new_id
from ..time import utc_now_iso


class PostMessage(Tool):
    name = "post_message"
    description = "Post a message to the project chat, visible to the user."
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The message text to post.",
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
        **_: Any,
    ) -> str:
        message_id = new_id("msg")
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO goal_messages
                (message_id, goal_id, project_id, author_type, source_channel,
                 content, message_ts, created_at)
            VALUES (?, ?, ?, 'system', 'web', ?, ?, ?)
            """,
            (message_id, task["goal_id"], task["project_id"], content, now, now),
        )
        conn.commit()
        return "Message posted."
