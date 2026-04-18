from __future__ import annotations

import logging
import threading
import time

from .db import connect
from .planner import create_chat_response_task, should_plan
from .scheduler import queue_ready_tasks

logger = logging.getLogger(__name__)


def _process_active_goals(connection) -> None:
    """Find goals that need a response task created, then queue ready tasks."""
    rows = connection.execute(
        """
        SELECT g.goal_id, g.project_id
        FROM goals g
        JOIN projects p ON p.project_id = g.project_id
        WHERE g.state = 'active'
          AND p.state IN ('draft', 'activated')
        """,
    ).fetchall()

    for row in rows:
        goal_id: str = row["goal_id"]
        project_id: str = row["project_id"]
        if should_plan(connection, goal_id):
            logger.info("Creating chat_response task for goal %s", goal_id)
            create_chat_response_task(connection, project_id, goal_id)

    queue_ready_tasks(connection)


def _ingress_loop(db_path: str, poll_interval: float) -> None:
    logger.info("Ingress loop started")
    while True:
        try:
            connection = connect(db_path)
            try:
                _process_active_goals(connection)
            finally:
                connection.close()
        except Exception:
            logger.exception("Ingress loop error")
        time.sleep(poll_interval)


def start_ingress_thread(db_path: str, poll_interval: float = 2.0) -> threading.Thread:
    thread = threading.Thread(
        target=_ingress_loop,
        args=(db_path, poll_interval),
        daemon=True,
        name="opendagent-ingress",
    )
    thread.start()
    return thread
