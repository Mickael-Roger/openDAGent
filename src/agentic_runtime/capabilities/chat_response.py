from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import new_id
from ..llm import complete
from ..time import utc_now_iso


def execute(
    connection: sqlite3.Connection,
    task: dict[str, Any],
    llm_config: dict[str, Any],
) -> None:
    goal_id: str = task["goal_id"]
    project_id: str = task["project_id"]

    goal_row = connection.execute(
        "SELECT title, description FROM goals WHERE goal_id = ?",
        (goal_id,),
    ).fetchone()
    goal_title = goal_row["title"] if goal_row else "this project"
    goal_description = (goal_row["description"] or "") if goal_row else ""

    message_rows = connection.execute(
        """
        SELECT author_type, content
        FROM goal_messages
        WHERE goal_id = ?
        ORDER BY message_ts ASC, created_at ASC
        """,
        (goal_id,),
    ).fetchall()

    llm_messages: list[dict[str, str]] = []
    for row in message_rows:
        role = "user" if row["author_type"] == "user" else "assistant"
        llm_messages.append({"role": role, "content": row["content"]})

    default_provider_id = str(llm_config.get("default_provider", "openai"))
    default_model = str(llm_config.get("default_model", "gpt-4.1"))
    providers = {p["id"]: p for p in llm_config.get("providers", [])}
    provider = providers.get(default_provider_id) or next(iter(providers.values()), {})

    desc_line = f"\nProject description: {goal_description}" if goal_description else ""
    system = (
        "You are openDAGent, an AI assistant that helps users design and build software projects.\n"
        f"You are discussing the project: {goal_title}.{desc_line}\n"
        "Help the user clarify requirements, think through architecture, break down work into tasks, "
        "and plan next steps. Be concise, practical, and actionable."
    )

    response_text = complete(llm_messages, provider, default_model, system=system)

    message_id = new_id("msg")
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO goal_messages
            (message_id, goal_id, project_id, author_type, source_channel,
             content, message_ts, created_at)
        VALUES (?, ?, ?, 'system', 'web', ?, ?, ?)
        """,
        (message_id, goal_id, project_id, response_text, now, now),
    )
    connection.commit()
