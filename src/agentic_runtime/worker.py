from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .db import connect
from .ids import new_id
from .time import utc_now_iso

logger = logging.getLogger(__name__)


def _claim_task(connection: Any, worker_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT task_id, goal_id, project_id, capability_name
        FROM tasks
        WHERE state = 'queued'
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
        """,
    ).fetchone()
    if row is None:
        return None

    task = dict(row)
    now = utc_now_iso()
    updated = connection.execute(
        """
        UPDATE tasks
        SET state = 'claimed', lease_owner_worker_id = ?, updated_at = ?
        WHERE task_id = ? AND state = 'queued'
        """,
        (worker_id, now, task["task_id"]),
    ).rowcount
    connection.commit()
    return task if updated == 1 else None


def _run_task(
    connection: Any,
    task: dict[str, Any],
    worker_id: str,
    app_config: dict[str, Any],
) -> None:
    task_id: str = task["task_id"]
    attempt_id = new_id("att")
    now = utc_now_iso()

    connection.execute(
        "UPDATE tasks SET state = 'running', started_at = ?, updated_at = ? WHERE task_id = ?",
        (now, now, task_id),
    )
    connection.execute(
        "INSERT INTO task_attempts (attempt_id, task_id, worker_id, status, started_at) VALUES (?, ?, ?, 'running', ?)",
        (attempt_id, task_id, worker_id, now),
    )
    connection.commit()

    try:
        from .capabilities import get_executor
        executor = get_executor(task["capability_name"], connection)
        if executor is None:
            raise NotImplementedError(f"Capability not found: {task['capability_name']!r}")

        executor.execute(
            connection,
            task,
            app_config.get("llm", {}),
            mcp_config=app_config.get("mcp", {}),
        )

        now = utc_now_iso()
        connection.execute(
            "UPDATE tasks SET state = 'done', completed_at = ?, updated_at = ? WHERE task_id = ?",
            (now, now, task_id),
        )
        connection.execute(
            "UPDATE task_attempts SET status = 'done', ended_at = ? WHERE attempt_id = ?",
            (now, attempt_id),
        )
        connection.commit()
        logger.info("Task %s completed.", task_id)

    except Exception as exc:
        logger.exception("Task %s failed.", task_id)
        now = utc_now_iso()
        connection.execute(
            "UPDATE tasks SET state = 'failed', completed_at = ?, updated_at = ? WHERE task_id = ?",
            (now, now, task_id),
        )
        connection.execute(
            """
            UPDATE task_attempts
            SET status = 'failed', ended_at = ?, error_type = ?, error_message = ?
            WHERE attempt_id = ?
            """,
            (now, type(exc).__name__, str(exc)[:2000], attempt_id),
        )
        connection.commit()


def recover_interrupted_tasks(connection: Any) -> None:
    """
    Reset tasks left in 'running' or 'claimed' state from a previous process.
    Called once at worker startup so tasks are not silently lost on restart.
    """
    now = utc_now_iso()
    result = connection.execute(
        """
        UPDATE tasks
        SET state = 'queued',
            lease_owner_worker_id = NULL,
            lease_expires_at = NULL,
            updated_at = ?
        WHERE state IN ('running', 'claimed')
        """,
        (now,),
    )
    # Close orphaned attempt records so the task_attempts history is clean
    connection.execute(
        "UPDATE task_attempts SET status = 'failed', ended_at = ? WHERE status = 'running'",
        (now,),
    )
    connection.commit()
    if result.rowcount:
        logger.info(
            "Restart recovery: re-queued %d interrupted task(s).",
            result.rowcount,
        )


def _worker_loop(db_path: str, app_config: dict[str, Any], poll_interval: float) -> None:
    worker_id = new_id("wrk")
    logger.info("Worker %s started.", worker_id)
    # Recover tasks that were running when the process last exited
    startup_conn = connect(db_path)
    try:
        recover_interrupted_tasks(startup_conn)
    finally:
        startup_conn.close()
    while True:
        try:
            connection = connect(db_path)
            try:
                task = _claim_task(connection, worker_id)
                if task:
                    logger.info(
                        "Worker %s claimed task %s (%s).",
                        worker_id, task["task_id"], task["capability_name"],
                    )
                    _run_task(connection, task, worker_id, app_config)
            finally:
                connection.close()
        except Exception:
            logger.exception("Worker loop error.")
        time.sleep(poll_interval)


def start_worker_thread(
    db_path: str,
    app_config: dict[str, Any],
    poll_interval: float = 1.0,
) -> threading.Thread:
    thread = threading.Thread(
        target=_worker_loop,
        args=(db_path, app_config, poll_interval),
        daemon=True,
        name="opendagent-worker",
    )
    thread.start()
    return thread
