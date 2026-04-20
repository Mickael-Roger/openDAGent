from __future__ import annotations

import sqlite3
from typing import Any

from . import Tool
from ..exceptions import TaskBlocked
from ..ids import new_id
from ..time import utc_now_iso


class CreateTask(Tool):
    name = "create_task"
    description = (
        "Create a new task in the current project for a given capability. "
        "Declare required_artifacts to block the task until those artifacts exist, "
        "and produced_artifacts to register the artifacts this task will write. "
        "These declarations form the DAG that drives automatic scheduling."
    )
    parameters = {
        "type": "object",
        "properties": {
            "capability_name": {
                "type": "string",
                "description": "The capability that will execute the task.",
            },
            "title": {
                "type": "string",
                "description": "Short human-readable task title.",
            },
            "description": {
                "type": "string",
                "description": "Full description of what the task should do.",
            },
            "priority": {
                "type": "integer",
                "description": "Task priority 0–100. Default 50.",
            },
            "required_artifacts": {
                "type": "array",
                "description": (
                    "Artifacts that must exist before this task can run. "
                    "Each item: {\"artifact_key\": \"<key>\", \"required_status\": \"active\"|\"approved\"}."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_key": {"type": "string"},
                        "required_status": {"type": "string", "enum": ["active", "approved"]},
                    },
                    "required": ["artifact_key", "required_status"],
                },
            },
            "produced_artifacts": {
                "type": "array",
                "description": (
                    "Artifacts this task will write via write_artifact. "
                    "Each item: {\"artifact_key\": \"<key>\", \"artifact_type\": \"structured\"|\"file\", "
                    "\"delivery_mode\": \"value\"|\"file\"}."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_key": {"type": "string"},
                        "artifact_type": {"type": "string", "enum": ["structured", "file"]},
                        "delivery_mode": {"type": "string", "enum": ["value", "file"]},
                    },
                    "required": ["artifact_key", "artifact_type", "delivery_mode"],
                },
            },
        },
        "required": ["capability_name", "title"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        capability_name: str,
        title: str,
        description: str = "",
        priority: int = 50,
        required_artifacts: list[dict[str, Any]] | None = None,
        produced_artifacts: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> str:
        # Verify the capability exists
        cap_row = conn.execute(
            "SELECT 1 FROM capabilities WHERE capability_name = ? AND enabled = 1",
            (capability_name,),
        ).fetchone()
        if cap_row is None:
            return f"Error: unknown or disabled capability '{capability_name}'."

        task_id = new_id("task")
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO tasks
                (task_id, goal_id, project_id, capability_name, title, description,
                 state, priority, retry_count, allowed_paths_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'created', ?, 0, '[]', ?, ?)
            """,
            (
                task_id,
                task["goal_id"],
                task["project_id"],
                capability_name,
                title,
                description,
                max(0, min(100, int(priority))),
                now,
                now,
            ),
        )

        for req in required_artifacts or []:
            conn.execute(
                """
                INSERT INTO task_required_artifacts
                    (requirement_id, task_id, artifact_key, required_status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (new_id("req"), task_id, req["artifact_key"], req.get("required_status", "active"), now),
            )

        for prod in produced_artifacts or []:
            conn.execute(
                """
                INSERT INTO task_produced_artifacts
                    (production_id, task_id, artifact_key, artifact_type, delivery_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("prod"),
                    task_id,
                    prod["artifact_key"],
                    prod.get("artifact_type", "structured"),
                    prod.get("delivery_mode", "value"),
                    now,
                ),
            )

        conn.commit()

        parts = [f"Task '{title}' created (id={task_id})"]
        if required_artifacts:
            parts.append(f"requires: {[r['artifact_key'] for r in required_artifacts]}")
        if produced_artifacts:
            parts.append(f"produces: {[p['artifact_key'] for p in produced_artifacts]}")
        return ", ".join(parts) + "."


class SpawnSubtask(Tool):
    name = "spawn_subtask"
    description = (
        "Create a subtask that runs on a DIFFERENT capability and BLOCK this task "
        "until the subtask finishes. Use this when the current task discovers it "
        "needs work from another capability (e.g. a webbrowsing task needs to send "
        "an email via the mail capability). The current task will pause, the subtask "
        "will execute, and this task will resume with the subtask result."
    )
    parameters = {
        "type": "object",
        "properties": {
            "capability_name": {
                "type": "string",
                "description": "The capability that will execute the subtask.",
            },
            "title": {
                "type": "string",
                "description": "Short human-readable subtask title.",
            },
            "description": {
                "type": "string",
                "description": "Full description of what the subtask should do.",
            },
            "produced_artifacts": {
                "type": "array",
                "description": (
                    "Artifacts the subtask will produce. "
                    "Each item: {\"artifact_key\": \"<key>\", \"artifact_type\": \"structured\"|\"file\", "
                    "\"delivery_mode\": \"value\"|\"file\"}."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "artifact_key": {"type": "string"},
                        "artifact_type": {"type": "string", "enum": ["structured", "file"]},
                        "delivery_mode": {"type": "string", "enum": ["value", "file"]},
                    },
                    "required": ["artifact_key", "artifact_type", "delivery_mode"],
                },
            },
        },
        "required": ["capability_name", "title", "description"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        capability_name: str,
        title: str,
        description: str,
        produced_artifacts: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> str:
        # Verify the capability exists
        cap_row = conn.execute(
            "SELECT 1 FROM capabilities WHERE capability_name = ? AND enabled = 1",
            (capability_name,),
        ).fetchone()
        if cap_row is None:
            return f"Error: unknown or disabled capability '{capability_name}'."

        child_task_id = new_id("task")
        now = utc_now_iso()

        # Create the child task with parent_task_id pointing back to us
        conn.execute(
            """
            INSERT INTO tasks
                (task_id, goal_id, project_id, parent_task_id, capability_name,
                 title, description, state, priority, retry_count,
                 allowed_paths_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, 0, '[]', ?, ?)
            """,
            (
                child_task_id,
                task["goal_id"],
                task["project_id"],
                task["task_id"],
                capability_name,
                title,
                description,
                task.get("priority", 50),
                now,
                now,
            ),
        )

        for prod in produced_artifacts or []:
            conn.execute(
                """
                INSERT INTO task_produced_artifacts
                    (production_id, task_id, artifact_key, artifact_type, delivery_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("prod"),
                    child_task_id,
                    prod["artifact_key"],
                    prod.get("artifact_type", "structured"),
                    prod.get("delivery_mode", "value"),
                    now,
                ),
            )

        # Mark parent as blocked by this child
        conn.execute(
            "UPDATE tasks SET blocked_by_task_id = ?, updated_at = ? WHERE task_id = ?",
            (child_task_id, now, task["task_id"]),
        )
        conn.commit()

        # Raise to interrupt the capability loop — messages will be saved
        raise TaskBlocked(child_task_id)


class ListCapabilities(Tool):
    name = "list_capabilities"
    description = (
        "Return the list of all currently enabled capabilities with their names and descriptions. "
        "Call this before create_task to know which capability_name values are valid and what each one does."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        **_: Any,
    ) -> str:
        import json as _json
        rows = conn.execute(
            "SELECT capability_name, definition_json FROM capabilities WHERE enabled = 1 ORDER BY capability_name"
        ).fetchall()
        lines: list[str] = []
        for row in rows:
            cap_name = row["capability_name"]
            try:
                defn = _json.loads(row["definition_json"])
                desc = defn.get("description", "").strip().replace("\n", " ")
            except Exception:
                desc = ""
            lines.append(f"- {cap_name}: {desc}" if desc else f"- {cap_name}")
        if not lines:
            return "No capabilities are currently enabled."
        return "Enabled capabilities:\n" + "\n".join(lines)


class AskUser(Tool):
    name = "ask_user"
    description = (
        "Post a question to the user and end the current task. "
        "A new response task will be created automatically when the user replies."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
        },
        "required": ["question"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        question: str,
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
            (message_id, task["goal_id"], task["project_id"], question, now, now),
        )
        conn.commit()
        return "Question posted. Task will complete; a new task will be triggered when the user replies."
