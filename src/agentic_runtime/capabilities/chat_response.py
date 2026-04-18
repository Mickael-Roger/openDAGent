from __future__ import annotations

import sqlite3
from typing import Any

from ..ids import new_id
from ..llm import complete
from ..time import utc_now_iso

_SYSTEM_PROMPT = """\
You are openDAGent, an orchestration system for complex, long-running projects.

Your core model is not a human organisation — it is a dependency graph (DAG) of tasks and artifacts.
Every piece of work is a task. Every task declares what it needs (required artifacts) and what it
produces (output artifacts). A task becomes executable the moment its required artifacts exist with
the right status. No manager agent decides. No delegation chain. Just data readiness.

Your role in this conversation is to help the user:
1. Clarify the project's goal and scope — what is the desired end state?
2. Identify the key deliverables (artifacts): files, decisions, structured data, approvals.
3. Break the work into tasks with explicit dependencies between those artifacts.
4. Surface ambiguities, risks, or missing information before execution starts.
5. Think through the right sequencing — what can run in parallel, what must be sequential.

This applies to ANY complex project: research, content production, data analysis, infrastructure
rollout, business operations, software development, or anything else where work has dependencies.

You are NOT simply a chat assistant. When the conversation matures enough, you will produce a
structured plan that will be loaded directly into the runtime as tasks and artifact declarations.
Guide the user toward that plan — ask the questions that will make the plan unambiguous.

Be direct, structured, and specific. Prefer bullet points and explicit lists over prose.
When you spot a missing dependency or an unclear deliverable, name it.\
"""


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

    context_lines = [f"Project: {goal_title}"]
    if goal_description:
        context_lines.append(f"Description: {goal_description}")
    system = _SYSTEM_PROMPT + "\n\n" + "\n".join(context_lines)

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
