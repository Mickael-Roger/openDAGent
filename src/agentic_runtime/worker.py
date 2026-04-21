from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .db import connect
from .exceptions import TaskBlocked
from .ids import new_id
from .scheduler import queue_ready_tasks, unblock_completed_subtasks
from .time import utc_now_iso

logger = logging.getLogger(__name__)

_SUPERVISOR_SKIP = frozenset({"dag_supervisor", "chat_response", "plan_project"})


def _maybe_create_supervisor_review(connection: Any, task: dict[str, Any]) -> None:
    """Create a dag_supervisor task if conditions are met after a work task finishes."""
    cap_name = task.get("capability_name", "")
    goal_id = task.get("goal_id")

    # Skip if the completed task is itself a supervisor, chat, or planner
    if cap_name in _SUPERVISOR_SKIP or not goal_id:
        return

    # Check the goal is still active
    goal = connection.execute(
        "SELECT state FROM goals WHERE goal_id = ?", (goal_id,)
    ).fetchone()
    if not goal or goal["state"] != "active":
        return

    # Check no supervisor task already pending/running/claimed for this goal
    existing = connection.execute(
        """SELECT 1 FROM tasks
           WHERE goal_id = ? AND capability_name = 'dag_supervisor'
             AND state IN ('created', 'queued', 'claimed', 'running')
           LIMIT 1""",
        (goal_id,),
    ).fetchone()
    if existing:
        return

    # Check the dag_supervisor capability is registered
    from .capabilities import get_executor
    if get_executor("dag_supervisor", connection) is None:
        return

    # Check at least one non-planner project task has completed
    has_completed = connection.execute(
        """SELECT 1 FROM tasks
           WHERE goal_id = ? AND capability_name NOT IN ('plan_project', 'chat_response', 'dag_supervisor')
             AND state IN ('done', 'failed')
           LIMIT 1""",
        (goal_id,),
    ).fetchone()
    if not has_completed:
        return

    # Create the supervisor task
    task_id = new_id("tsk")
    now = utc_now_iso()
    trigger_state = "done" if task.get("state_after") != "failed" else "failed"
    description = (
        f"Triggered by: task '{task.get('title', task['task_id'])}' completed ({trigger_state}). "
        "Review DAG progress and decide if adaptations are needed."
    )
    connection.execute(
        """INSERT INTO tasks
              (task_id, goal_id, project_id, capability_name, title, description,
               priority, state, task_kind, created_at, updated_at)
           VALUES (?, ?, ?, 'dag_supervisor', 'DAG Review', ?, 90, 'queued', 'internal', ?, ?)""",
        (task_id, goal_id, task["project_id"], description, now, now),
    )
    connection.commit()
    logger.info("Created supervisor review %s for goal %s.", task_id, goal_id)


def _verify_produced_artifacts(connection: Any, task_id: str) -> None:
    """Raise RuntimeError if the task declared artifacts it did not produce."""
    declared = connection.execute(
        "SELECT artifact_key FROM task_produced_artifacts WHERE task_id = ?",
        (task_id,),
    ).fetchall()
    if not declared:
        return
    missing = []
    for row in declared:
        found = connection.execute(
            """SELECT 1 FROM artifacts
               WHERE produced_by_task_id = ? AND artifact_key = ?
                 AND status IN ('active', 'approved')
               LIMIT 1""",
            (task_id, row["artifact_key"]),
        ).fetchone()
        if not found:
            missing.append(row["artifact_key"])
    if missing:
        raise RuntimeError(
            f"Task {task_id} declared but did not produce artifacts: {missing}"
        )


def _claim_task(connection: Any, worker_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT task_id, goal_id, project_id, capability_name, title, description
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

        # Snapshot the latest goal message timestamp before execution so we
        # can detect if the capability posted a response via post_message.
        pre_exec_latest = None
        if task.get("goal_id"):
            row = connection.execute(
                "SELECT message_ts FROM goal_messages WHERE goal_id = ? ORDER BY message_ts DESC LIMIT 1",
                (task["goal_id"],),
            ).fetchone()
            pre_exec_latest = row["message_ts"] if row else None

        final_content = executor.execute(
            connection,
            task,
            app_config.get("llm", {}),
            mcp_config=app_config.get("mcp", {}),
            app_config=app_config,
        )

        # Safety net: if the capability finished without posting any goal
        # message, auto-post the LLM's final text (or a fallback).
        if task.get("goal_id"):
            post_exec_latest = connection.execute(
                "SELECT message_ts FROM goal_messages WHERE goal_id = ? ORDER BY message_ts DESC LIMIT 1",
                (task["goal_id"],),
            ).fetchone()
            post_ts = post_exec_latest["message_ts"] if post_exec_latest else None
            if post_ts == pre_exec_latest:
                content = (final_content or "").strip()
                if not content:
                    content = "(The assistant processed your message but produced no visible reply.)"
                _now = utc_now_iso()
                connection.execute(
                    """
                    INSERT INTO goal_messages
                        (message_id, goal_id, project_id, author_type, source_channel,
                         content, message_ts, created_at)
                    VALUES (?, ?, ?, 'system', 'web', ?, ?, ?)
                    """,
                    (new_id("msg"), task["goal_id"], task["project_id"],
                     content, _now, _now),
                )
                connection.commit()
                logger.info("Task %s: auto-posted LLM response (%d chars).", task_id, len(content))

        # Verify that all declared artifacts were actually produced
        _verify_produced_artifacts(connection, task_id)

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

        # Immediately check if any downstream tasks are now unblocked
        # (don't wait for the next ingress poll cycle).
        try:
            unblocked = unblock_completed_subtasks(connection)
            queued = queue_ready_tasks(connection)
            if unblocked:
                logger.info("Resumed %d blocked parent(s) after %s: %s", len(unblocked), task_id, unblocked)
            if queued:
                logger.info("Unblocked %d task(s) after %s: %s", len(queued), task_id, queued)
        except Exception:
            logger.debug("queue_ready_tasks after completion failed (will retry via ingress).", exc_info=True)

        # Trigger supervisor review if applicable
        try:
            _maybe_create_supervisor_review(connection, task)
        except Exception:
            logger.debug("Supervisor review creation failed (non-critical).", exc_info=True)

    except TaskBlocked as exc:
        now = utc_now_iso()
        connection.execute(
            "UPDATE tasks SET state = 'blocked', updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )
        connection.execute(
            "UPDATE task_attempts SET status = 'done', ended_at = ? WHERE attempt_id = ?",
            (now, attempt_id),
        )
        connection.commit()
        logger.info("Task %s blocked waiting for subtask %s.", task_id, exc.child_task_id)

        # Queue the child subtask immediately
        try:
            queued = queue_ready_tasks(connection)
            if queued:
                logger.info("Queued subtask(s) after blocking %s: %s", task_id, queued)
        except Exception:
            logger.debug("queue_ready_tasks after block failed (will retry via ingress).", exc_info=True)

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

        # Trigger supervisor review on failure too
        try:
            task["state_after"] = "failed"
            _maybe_create_supervisor_review(connection, task)
        except Exception:
            logger.debug("Supervisor review creation failed (non-critical).", exc_info=True)


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
