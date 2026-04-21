from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from . import Tool
from ..ids import new_id
from ..time import utc_now_iso

logger = logging.getLogger(__name__)

_BINARY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg",
                      ".pdf", ".zip", ".tar", ".gz", ".bin", ".mp3", ".mp4", ".wav"}


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

        # File-based artifact — read content if it's a text file
        fpath = Path(row["file_path"])
        if not fpath.exists():
            return f"Artifact file missing: {fpath} (version {row['version']})"

        if fpath.suffix.lower() in _BINARY_EXTENSIONS:
            return f"[Binary artifact: {fpath.name}] (version {row['version']}, path: {fpath})"

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            if len(content) > 50_000:
                content = content[:50_000] + "\n… (truncated, full file at: " + str(fpath) + ")"
            return content
        except Exception as exc:
            return f"Error reading artifact file {fpath}: {exc}"


class WriteArtifact(Tool):
    name = "write_artifact"
    description = (
        "Write an artifact for this project. The value is persisted as a file "
        "in the task workspace by default (for traceability and human review). "
        "You can also reference a file you already wrote via file_path."
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
            "file_path": {
                "type": "string",
                "description": (
                    "Path to an already-written file in the workspace (relative to "
                    "workspace root). Use this instead of value when the artifact is "
                    "a file you already created (e.g. a screenshot, generated image)."
                ),
            },
            "status": {
                "type": "string",
                "enum": ["active", "approved"],
                "description": "Artifact status. Default: active.",
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
        value: Any = None,
        file_path: str | None = None,
        status: str = "active",
        **_: Any,
    ) -> str:
        if value is None and file_path is None:
            return "Error: either 'value' or 'file_path' must be provided."

        artifact_id = new_id("artifact")
        now = utc_now_iso()
        workspace = task.get("workspace_path")

        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM artifacts WHERE project_id = ? AND artifact_key = ?",
            (task["project_id"], artifact_key),
        ).fetchone()
        version = int(row[0]) + 1

        stored_file_path: str | None = None
        stored_value_json: str | None = None

        if file_path is not None:
            # Mode A: reference an already-written file
            if workspace:
                abs_path = Path(workspace) / file_path
            else:
                abs_path = Path(file_path)
            if not abs_path.exists():
                return f"Error: file not found at '{abs_path}'."
            stored_file_path = str(abs_path.resolve())

        elif workspace:
            # Mode B (default): persist value as a file in the workspace
            artifacts_dir = Path(workspace) / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            # Sanitise the key for use as a filename
            safe_key = artifact_key.replace("/", "_").replace("\\", "_").replace(" ", "_")
            if isinstance(value, str):
                ext = ".txt"
                file_content = value.encode("utf-8")
            else:
                ext = ".json"
                file_content = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            filename = f"{safe_key}_v{version}{ext}"
            dest = artifacts_dir / filename
            dest.write_bytes(file_content)
            stored_file_path = str(dest.resolve())
            logger.debug("Artifact '%s' v%d written to %s", artifact_key, version, stored_file_path)

        else:
            # Fallback: no workspace available — store inline in DB
            stored_value_json = json.dumps(value, ensure_ascii=False)
            logger.warning("No workspace for task %s — storing artifact '%s' inline in DB.", task.get("task_id"), artifact_key)

        conn.execute(
            """
            INSERT INTO artifacts
                (artifact_id, project_id, goal_id, artifact_key, type, status, version,
                 produced_by_task_id, value_json, file_path, metadata_json,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 'structured', ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                artifact_id,
                task["project_id"],
                task["goal_id"],
                artifact_key,
                status,
                version,
                task["task_id"],
                stored_value_json,
                stored_file_path,
                now,
                now,
            ),
        )
        conn.commit()
        where = f"file {stored_file_path}" if stored_file_path else "database"
        return f"Artifact '{artifact_key}' written (version {version}, status {status}, stored in {where})."
