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


class ViewDag(Tool):
    name = "view_dag"
    description = (
        "Return the full DAG state for this goal: all tasks with their states, "
        "all artifacts with statuses, dependency edges, and a progress summary. "
        "Use this to understand what work has been done, what is in progress, "
        "what has failed, and what is still pending before deciding next steps."
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

        goal_id = task["goal_id"]

        # Goal info
        goal_row = conn.execute(
            "SELECT goal_id, title, state FROM goals WHERE goal_id = ?",
            (goal_id,),
        ).fetchone()
        if goal_row is None:
            return "Error: goal not found."

        # All tasks for this goal
        task_rows = conn.execute(
            """
            SELECT task_id, title, capability_name, state, priority, task_kind
            FROM tasks
            WHERE goal_id = ?
            ORDER BY priority DESC, created_at ASC
            """,
            (goal_id,),
        ).fetchall()

        # Build tasks list with required/produced artifacts and error messages
        tasks_out = []
        progress: dict[str, int] = {}
        for tr in task_rows:
            state = tr["state"]
            progress[state] = progress.get(state, 0) + 1

            # Required artifacts
            req_rows = conn.execute(
                "SELECT artifact_key FROM task_required_artifacts WHERE task_id = ?",
                (tr["task_id"],),
            ).fetchall()

            # Produced artifacts
            prod_rows = conn.execute(
                "SELECT artifact_key FROM task_produced_artifacts WHERE task_id = ?",
                (tr["task_id"],),
            ).fetchall()

            # Error message for failed tasks
            error_msg = None
            if state == "failed":
                attempt = conn.execute(
                    "SELECT error_message FROM task_attempts WHERE task_id = ? ORDER BY started_at DESC LIMIT 1",
                    (tr["task_id"],),
                ).fetchone()
                if attempt:
                    error_msg = attempt["error_message"]

            tasks_out.append({
                "task_id": tr["task_id"],
                "title": tr["title"],
                "capability_name": tr["capability_name"],
                "state": state,
                "priority": tr["priority"],
                "task_kind": tr["task_kind"],
                "required_artifacts": [r["artifact_key"] for r in req_rows],
                "produced_artifacts": [p["artifact_key"] for p in prod_rows],
                "error_message": error_msg,
            })

        # Artifacts for this goal
        art_rows = conn.execute(
            """
            SELECT artifact_key, status, version, produced_by_task_id, type
            FROM artifacts
            WHERE goal_id = ? AND status IN ('active', 'approved')
            ORDER BY artifact_key, version DESC
            """,
            (goal_id,),
        ).fetchall()

        artifacts_out = []
        for ar in art_rows:
            artifacts_out.append({
                "artifact_key": ar["artifact_key"],
                "status": ar["status"],
                "version": ar["version"],
                "produced_by": ar["produced_by_task_id"],
                "type": ar["type"],
            })

        # Dependency edges
        edge_rows = conn.execute(
            """
            SELECT producer.task_id AS source,
                   consumer.task_id AS target,
                   required.artifact_key
            FROM task_required_artifacts AS required
            JOIN tasks AS consumer ON consumer.task_id = required.task_id
            JOIN task_produced_artifacts AS produced
              ON produced.artifact_key = required.artifact_key
            JOIN tasks AS producer ON producer.task_id = produced.task_id
            WHERE consumer.goal_id = ?
              AND producer.goal_id = ?
              AND consumer.task_id != producer.task_id
            """,
            (goal_id, goal_id),
        ).fetchall()

        edges_map: dict[tuple[str, str], list[str]] = {}
        for er in edge_rows:
            key = (er["source"], er["target"])
            edges_map.setdefault(key, []).append(er["artifact_key"])

        edges_out = [
            {"source": s, "target": t, "artifact_keys": keys}
            for (s, t), keys in edges_map.items()
        ]

        result = {
            "goal_id": goal_row["goal_id"],
            "goal_title": goal_row["title"],
            "goal_state": goal_row["state"],
            "progress": {"total": len(tasks_out), **progress},
            "tasks": tasks_out,
            "artifacts": artifacts_out,
            "edges": edges_out,
        }

        return _json.dumps(result, indent=2, default=str)


class CompleteGoal(Tool):
    name = "complete_goal"
    description = (
        "Mark the current goal as completed. Call this when all planned work is done "
        "and the goal has been achieved. This stops any further supervisor reviews "
        "and cancels remaining queued tasks. You should post a summary message to "
        "the user before calling this."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Brief summary of what was accomplished.",
            },
        },
        "required": ["summary"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        summary: str,
        **_: Any,
    ) -> str:
        goal_id = task["goal_id"]
        now = utc_now_iso()

        # Check goal is still active
        goal_row = conn.execute(
            "SELECT state FROM goals WHERE goal_id = ?", (goal_id,),
        ).fetchone()
        if goal_row is None:
            return "Error: goal not found."
        if goal_row["state"] != "active":
            return f"Error: goal is already in state '{goal_row['state']}', cannot complete."

        # Mark goal as completed
        conn.execute(
            "UPDATE goals SET state = 'completed', updated_at = ? WHERE goal_id = ?",
            (now, goal_id),
        )

        # Cancel remaining created/queued tasks for this goal
        cancelled = conn.execute(
            """
            UPDATE tasks SET state = 'cancelled', updated_at = ?
            WHERE goal_id = ? AND state IN ('created', 'queued')
            """,
            (now, goal_id),
        ).rowcount

        # Post summary as a goal message
        conn.execute(
            """
            INSERT INTO goal_messages
                (message_id, goal_id, project_id, author_type, source_channel,
                 content, message_ts, created_at)
            VALUES (?, ?, ?, 'system', 'web', ?, ?, ?)
            """,
            (new_id("msg"), goal_id, task["project_id"],
             f"Goal completed: {summary}", now, now),
        )
        conn.commit()

        parts = [f"Goal marked as completed."]
        if cancelled:
            parts.append(f"{cancelled} remaining task(s) cancelled.")
        return " ".join(parts)


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
