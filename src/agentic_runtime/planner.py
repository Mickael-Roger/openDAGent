from __future__ import annotations

import sqlite3

from .ids import new_id
from .time import utc_now_iso


def should_plan(connection: sqlite3.Connection, goal_id: str) -> bool:
    """Return True if the goal needs a new response task created."""
    # Skip if a task is already in flight for this goal
    active = connection.execute(
        """
        SELECT 1 FROM tasks
        WHERE goal_id = ? AND state IN ('created', 'queued', 'claimed', 'running')
        LIMIT 1
        """,
        (goal_id,),
    ).fetchone()
    if active is not None:
        return False

    # Only plan when the most recent message is from the user
    latest = connection.execute(
        """
        SELECT author_type FROM goal_messages
        WHERE goal_id = ?
        ORDER BY message_ts DESC, created_at DESC
        LIMIT 1
        """,
        (goal_id,),
    ).fetchone()
    return latest is not None and latest["author_type"] == "user"


def create_chat_response_task(
    connection: sqlite3.Connection,
    project_id: str,
    goal_id: str,
) -> str:
    task_id = new_id("task")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO tasks
            (task_id, goal_id, project_id, capability_name, title,
             task_kind, state, priority, retry_count, allowed_paths_json, created_at, updated_at)
        VALUES (?, ?, ?, 'chat_response', 'Generate chat response',
                'internal', 'created', 50, 0, '[]', ?, ?)
        """,
        (task_id, goal_id, project_id, now, now),
    )
    connection.commit()
    return task_id
