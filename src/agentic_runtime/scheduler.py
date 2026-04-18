from __future__ import annotations

import sqlite3

from .artifacts import is_task_executable
from .time import utc_now_iso


def queue_ready_tasks(
    connection: sqlite3.Connection,
    now_iso: str | None = None,
) -> list[str]:
    queued_task_ids: list[str] = []
    effective_now_iso = now_iso or utc_now_iso()
    rows = connection.execute(
        """
        SELECT tasks.task_id
        FROM tasks
        JOIN goals ON goals.goal_id = tasks.goal_id
        JOIN projects ON projects.project_id = tasks.project_id
        WHERE tasks.state = 'created'
          AND goals.state = 'active'
          AND projects.state = 'activated'
        ORDER BY tasks.priority DESC, tasks.created_at ASC
        """
    ).fetchall()

    for row in rows:
        task_id = row["task_id"]
        if not is_task_executable(connection, task_id):
            continue
        result = connection.execute(
            "UPDATE tasks SET state = 'queued', updated_at = ? WHERE task_id = ? AND state = 'created'",
            (effective_now_iso, task_id),
        )
        if result.rowcount != 1:
            continue
        queued_task_ids.append(task_id)

    connection.commit()
    return queued_task_ids
