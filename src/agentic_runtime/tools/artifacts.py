from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import Tool
from ..ids import new_id
from ..time import utc_now_iso


class ReadArtifact(Tool):
    name = "read_artifact"
    description = (
        "Read the latest active or approved artifact value for a given key in this project."
    )
    parameters = {
        "type": "object",
        "properties": {
            "artifact_key": {
                "type": "string",
                "description": "The artifact key to read.",
            },
        },
        "required": ["artifact_key"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        artifact_key: str,
        **_: Any,
    ) -> str:
        row = conn.execute(
            """
            SELECT value_json, file_path, status, version
            FROM artifacts
            WHERE project_id = ? AND artifact_key = ?
              AND status IN ('active', 'approved')
            ORDER BY version DESC, updated_at DESC
            LIMIT 1
            """,
            (task["project_id"], artifact_key),
        ).fetchone()
        if row is None:
            return f"Artifact '{artifact_key}' not found."
        if row["value_json"]:
            return row["value_json"]
        return f"File artifact at: {row['file_path']} (version {row['version']}, status {row['status']})"


class WriteArtifact(Tool):
    name = "write_artifact"
    description = (
        "Write a structured value artifact for this project. "
        "Use this to record decisions, plans, computed results, and approvals."
    )
    parameters = {
        "type": "object",
        "properties": {
            "artifact_key": {
                "type": "string",
                "description": "Unique key for this artifact (e.g. 'project.scope', 'plan.tasks').",
            },
            "value": {
                "description": "The artifact value — any JSON-serializable object.",
            },
            "status": {
                "type": "string",
                "enum": ["active", "approved"],
                "description": "Artifact status. Default: active.",
            },
        },
        "required": ["artifact_key", "value"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        artifact_key: str,
        value: Any,
        status: str = "active",
        **_: Any,
    ) -> str:
        artifact_id = new_id("artifact")
        now = utc_now_iso()

        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM artifacts WHERE project_id = ? AND artifact_key = ?",
            (task["project_id"], artifact_key),
        ).fetchone()
        version = int(row[0]) + 1

        conn.execute(
            """
            INSERT INTO artifacts
                (artifact_id, project_id, goal_id, artifact_key, type, status, version,
                 produced_by_task_id, value_json, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'structured', ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                artifact_id,
                task["project_id"],
                task["goal_id"],
                artifact_key,
                status,
                version,
                task["task_id"],
                json.dumps(value, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
        return f"Artifact '{artifact_key}' written (version {version}, status {status})."
