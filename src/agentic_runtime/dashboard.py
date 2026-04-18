from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from .ids import new_id
from .time import utc_now_iso


def get_dashboard_data(connection: sqlite3.Connection) -> dict[str, Any]:
    projects = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                projects.project_id,
                projects.slug,
                projects.title,
                projects.state,
                projects.updated_at,
                COUNT(DISTINCT goals.goal_id) AS goal_count,
                COUNT(DISTINCT tasks.task_id) AS task_count,
                COUNT(DISTINCT CASE WHEN tasks.state = 'done' THEN tasks.task_id END) AS done_task_count,
                COUNT(DISTINCT artifacts.artifact_id) AS artifact_count
            FROM projects
            LEFT JOIN goals ON goals.project_id = projects.project_id
            LEFT JOIN tasks ON tasks.project_id = projects.project_id
            LEFT JOIN artifacts ON artifacts.project_id = projects.project_id
            GROUP BY projects.project_id
            ORDER BY projects.updated_at DESC, projects.created_at DESC
            """
        ).fetchall()
    ]

    task_state_counts = Counter(
        row[0]
        for row in connection.execute("SELECT state FROM tasks").fetchall()
    )
    project_state_counts = Counter(
        row[0]
        for row in connection.execute("SELECT state FROM projects").fetchall()
    )

    return {
        "page_title": "Projects",
        "projects": projects,
        "project_count": len(projects),
        "task_state_counts": dict(task_state_counts),
        "project_state_counts": dict(project_state_counts),
    }


def get_project_detail_data(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any] | None:
    project_row = connection.execute(
        "SELECT * FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if project_row is None:
        return None

    goals = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM goals WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()
    ]
    tasks = [
        dict(row)
        for row in connection.execute(
            """
            SELECT
                tasks.task_id,
                tasks.goal_id,
                goals.title AS goal_title,
                tasks.title,
                tasks.capability_name,
                tasks.state,
                tasks.priority,
                tasks.updated_at
            FROM tasks
            JOIN goals ON goals.goal_id = tasks.goal_id
            WHERE tasks.project_id = ?
            ORDER BY tasks.priority DESC, tasks.created_at ASC
            """,
            (project_id,),
        ).fetchall()
    ]
    graph = get_project_graph_data(connection, project_id)
    recent_artifacts = [
        dict(row)
        for row in connection.execute(
            """
            SELECT artifact_id, goal_id, artifact_key, type, status, version, produced_by_task_id, file_path, updated_at
            FROM artifacts
            WHERE project_id = ?
            ORDER BY updated_at DESC, version DESC
            LIMIT 12
            """,
            (project_id,),
        ).fetchall()
    ]

    return {
        "page_title": dict(project_row)["title"],
        "project": dict(project_row),
        "goals": goals,
        "tasks": tasks,
        "recent_artifacts": recent_artifacts,
        "graph": graph,
        "graph_json": json.dumps(graph),
    }


def get_project_graph_data(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any] | None:
    project_exists = connection.execute(
        "SELECT 1 FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if project_exists is None:
        return None

    task_rows = connection.execute(
        """
        SELECT tasks.task_id, tasks.goal_id, tasks.title, tasks.state, tasks.priority, goals.title AS goal_title
        FROM tasks
        JOIN goals ON goals.goal_id = tasks.goal_id
        WHERE tasks.project_id = ?
        ORDER BY tasks.priority DESC, tasks.created_at ASC
        """,
        (project_id,),
    ).fetchall()
    nodes = [dict(row) for row in task_rows]

    edge_rows = connection.execute(
        """
        SELECT
            producer.task_id AS source,
            consumer.task_id AS target,
            required.artifact_key AS artifact_key,
            required.required_status AS required_status
        FROM task_required_artifacts AS required
        JOIN tasks AS consumer ON consumer.task_id = required.task_id
        JOIN task_produced_artifacts AS produced
          ON produced.artifact_key = required.artifact_key
        JOIN tasks AS producer ON producer.task_id = produced.task_id
        WHERE consumer.project_id = ?
          AND producer.project_id = ?
          AND consumer.goal_id = producer.goal_id
          AND consumer.task_id != producer.task_id
        ORDER BY producer.task_id ASC, consumer.task_id ASC, required.artifact_key ASC
        """,
        (project_id, project_id),
    ).fetchall()

    deduped_edges: dict[tuple[str, str], dict[str, Any]] = {}
    for row in edge_rows:
        key = (row["source"], row["target"])
        edge = deduped_edges.setdefault(
            key,
            {
                "source": row["source"],
                "target": row["target"],
                "artifacts": [],
            },
        )
        edge["artifacts"].append(
            {
                "artifact_key": row["artifact_key"],
                "required_status": row["required_status"],
            }
        )

    return {
        "nodes": nodes,
        "edges": list(deduped_edges.values()),
    }


def get_task_detail_data(
    connection: sqlite3.Connection,
    task_id: str,
) -> dict[str, Any] | None:
    task_row = connection.execute(
        """
        SELECT tasks.*, goals.title AS goal_title, projects.title AS project_title, projects.slug AS project_slug
        FROM tasks
        JOIN goals ON goals.goal_id = tasks.goal_id
        JOIN projects ON projects.project_id = tasks.project_id
        WHERE tasks.task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if task_row is None:
        return None

    required_artifacts = [
        dict(row)
        for row in connection.execute(
            """
            SELECT artifact_key, required_status
            FROM task_required_artifacts
            WHERE task_id = ?
            ORDER BY artifact_key ASC
            """,
            (task_id,),
        ).fetchall()
    ]
    produced_artifacts = [
        dict(row)
        for row in connection.execute(
            """
            SELECT artifact_key, artifact_type, delivery_mode
            FROM task_produced_artifacts
            WHERE task_id = ?
            ORDER BY artifact_key ASC
            """,
            (task_id,),
        ).fetchall()
    ]
    resolved_requirements = []
    for requirement in required_artifacts:
        artifact_row = connection.execute(
            """
            SELECT artifact_id, status, version, file_path, value_json, updated_at
            FROM artifacts
            WHERE project_id = ?
              AND goal_id = ?
              AND artifact_key = ?
              AND status = ?
            ORDER BY version DESC, updated_at DESC
            LIMIT 1
            """,
            (
                task_row["project_id"],
                task_row["goal_id"],
                requirement["artifact_key"],
                requirement["required_status"],
            ),
        ).fetchone()
        resolved_requirements.append(
            {
                **requirement,
                "resolved_artifact": dict(artifact_row) if artifact_row is not None else None,
            }
        )

    output_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT artifact_id, artifact_key, type, status, version, file_path, value_json, updated_at
            FROM artifacts
            WHERE produced_by_task_id = ?
            ORDER BY updated_at DESC, version DESC
            """,
            (task_id,),
        ).fetchall()
    ]
    attempts = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM task_attempts WHERE task_id = ? ORDER BY started_at DESC",
            (task_id,),
        ).fetchall()
    ]

    upstream_links = _task_neighbors(connection, task_row["project_id"], task_id, direction="upstream")
    downstream_links = _task_neighbors(connection, task_row["project_id"], task_id, direction="downstream")

    return {
        "page_title": task_row["title"],
        "task": dict(task_row),
        "required_artifacts": required_artifacts,
        "produced_artifacts": produced_artifacts,
        "resolved_requirements": resolved_requirements,
        "output_artifacts": output_rows,
        "attempts": attempts,
        "upstream_links": upstream_links,
        "downstream_links": downstream_links,
    }


def _task_neighbors(
    connection: sqlite3.Connection,
    project_id: str,
    task_id: str,
    direction: str,
) -> list[dict[str, Any]]:
    if direction == "upstream":
        join_clause = "producer.task_id = produced.task_id AND consumer.task_id = required.task_id"
        filter_clause = "consumer.task_id = ?"
        select_task = "producer.task_id AS task_id, producer.title, producer.state"
    else:
        join_clause = "producer.task_id = produced.task_id AND consumer.task_id = required.task_id"
        filter_clause = "producer.task_id = ?"
        select_task = "consumer.task_id AS task_id, consumer.title, consumer.state"

    rows = connection.execute(
        f"""
        SELECT DISTINCT {select_task}, required.artifact_key
        FROM task_required_artifacts AS required
        JOIN task_produced_artifacts AS produced ON produced.artifact_key = required.artifact_key
        JOIN tasks AS producer ON producer.task_id = produced.task_id
        JOIN tasks AS consumer ON consumer.task_id = required.task_id
        WHERE producer.project_id = ?
          AND consumer.project_id = ?
          AND producer.goal_id = consumer.goal_id
          AND {filter_clause}
          AND producer.task_id != consumer.task_id
        ORDER BY task_id ASC, required.artifact_key ASC
        """,
        (project_id, project_id, task_id),
    ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = grouped.setdefault(
            row["task_id"],
            {
                "task_id": row["task_id"],
                "title": row["title"],
                "state": row["state"],
                "artifacts": [],
            },
        )
        item["artifacts"].append(row["artifact_key"])
    return list(grouped.values())


def _slugify(text: str) -> str:
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:64] or "project"


def create_project_with_goal(
    connection: sqlite3.Connection,
    title: str,
    description: str,
) -> tuple[str, str]:
    project_id = new_id("proj")
    goal_id = new_id("goal")
    now = utc_now_iso()

    base_slug = _slugify(title)
    existing_slugs = {
        row[0]
        for row in connection.execute(
            "SELECT slug FROM projects WHERE slug = ? OR slug LIKE ?",
            (base_slug, base_slug + "-%"),
        ).fetchall()
    }
    slug = base_slug
    counter = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1

    connection.execute(
        """
        INSERT INTO projects
            (project_id, slug, title, description, state, local_repo_path,
             default_branch, visibility, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'draft', '', 'main', 'private', ?, ?)
        """,
        (project_id, slug, title, description, now, now),
    )
    connection.execute(
        """
        INSERT INTO goals
            (goal_id, project_id, title, description, source_channel, state,
             priority, approval_mode, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'web', 'active', 50, 'human_for_external_actions', ?, ?)
        """,
        (goal_id, project_id, title, description, now, now),
    )
    connection.commit()
    return project_id, goal_id


def get_project_chat_data(
    connection: sqlite3.Connection,
    project_id: str,
) -> dict[str, Any] | None:
    project_row = connection.execute(
        "SELECT * FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if project_row is None:
        return None

    goal_row = connection.execute(
        "SELECT * FROM goals WHERE project_id = ? ORDER BY created_at ASC LIMIT 1",
        (project_id,),
    ).fetchone()

    messages: list[dict[str, Any]] = []
    if goal_row is not None:
        messages = [
            dict(row)
            for row in connection.execute(
                """
                SELECT message_id, author_type, content, message_ts, created_at
                FROM goal_messages
                WHERE goal_id = ?
                ORDER BY message_ts ASC, created_at ASC
                """,
                (goal_row["goal_id"],),
            ).fetchall()
        ]

    return {
        "page_title": dict(project_row)["title"],
        "project": dict(project_row),
        "goal": dict(goal_row) if goal_row is not None else None,
        "messages": messages,
    }


def add_user_message(
    connection: sqlite3.Connection,
    project_id: str,
    goal_id: str,
    content: str,
) -> dict[str, Any]:
    message_id = new_id("msg")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO goal_messages
            (message_id, goal_id, project_id, author_type, source_channel,
             content, message_ts, created_at)
        VALUES (?, ?, ?, 'user', 'web', ?, ?, ?)
        """,
        (message_id, goal_id, project_id, content, now, now),
    )
    connection.commit()
    return {
        "message_id": message_id,
        "author_type": "user",
        "content": content,
        "message_ts": now,
        "created_at": now,
    }
