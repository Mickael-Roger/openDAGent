from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from typing import Any

from .ids import new_id
from .models import ArtifactRecord, ArtifactRequirement, ProducedArtifact, TaskRecord

DEFAULT_VALID_ARTIFACT_STATUSES = ("approved", "active")


def resolve_latest_artifact(
    connection: sqlite3.Connection,
    project_id: str,
    artifact_key: str,
    goal_id: str | None = None,
    valid_statuses: Sequence[str] = DEFAULT_VALID_ARTIFACT_STATUSES,
) -> ArtifactRecord | None:
    if not valid_statuses:
        raise ValueError("valid_statuses must not be empty")

    placeholders = ", ".join("?" for _ in valid_statuses)
    parameters: list[Any] = [project_id, artifact_key, *valid_statuses]
    goal_clause = ""
    if goal_id is not None:
        goal_clause = "AND goal_id = ?"
        parameters.append(goal_id)
    row = connection.execute(
        f"""
        SELECT artifact_id, artifact_key, type, status, version, produced_by_task_id, value_json, file_path
        FROM artifacts
        WHERE project_id = ?
          AND artifact_key = ?
          AND status IN ({placeholders})
          {goal_clause}
        ORDER BY version DESC, created_at DESC
        LIMIT 1
        """,
        parameters,
    ).fetchone()
    if row is None:
        return None
    return _artifact_from_row(row)


def get_task_required_artifacts(
    connection: sqlite3.Connection,
    task_id: str,
) -> list[ArtifactRequirement]:
    rows = connection.execute(
        """
        SELECT artifact_key, required_status
        FROM task_required_artifacts
        WHERE task_id = ?
        ORDER BY created_at ASC, artifact_key ASC
        """,
        (task_id,),
    ).fetchall()
    return [
        ArtifactRequirement(
            artifact_key=row["artifact_key"],
            required_status=row["required_status"],
        )
        for row in rows
    ]


def get_task_produced_artifacts(
    connection: sqlite3.Connection,
    task_id: str,
) -> list[ProducedArtifact]:
    rows = connection.execute(
        """
        SELECT artifact_key, artifact_type, delivery_mode
        FROM task_produced_artifacts
        WHERE task_id = ?
        ORDER BY created_at ASC, artifact_key ASC
        """,
        (task_id,),
    ).fetchall()
    return [
        ProducedArtifact(
            artifact_key=row["artifact_key"],
            artifact_type=row["artifact_type"],
            delivery_mode=row["delivery_mode"],
        )
        for row in rows
    ]


def is_task_executable(connection: sqlite3.Connection, task_id: str) -> bool:
    task_row = connection.execute(
        "SELECT project_id, goal_id FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if task_row is None:
        raise ValueError(f"Unknown task_id: {task_id}")

    for requirement in get_task_required_artifacts(connection, task_id):
        artifact = resolve_latest_artifact(
            connection,
            project_id=task_row["project_id"],
            artifact_key=requirement.artifact_key,
            goal_id=task_row["goal_id"],
            valid_statuses=(requirement.required_status,),
        )
        if artifact is None:
            return False

    return True


def register_task_output_artifacts(
    connection: sqlite3.Connection,
    task: TaskRecord,
    artifacts: Sequence[ArtifactRecord],
    now_iso: str,
) -> list[ArtifactRecord]:
    persisted_artifacts: list[ArtifactRecord] = []
    declared_artifacts = {
        declaration.artifact_key: declaration
        for declaration in task.produced_artifacts
    }

    connection.execute("SAVEPOINT artifact_registration")
    try:
        for artifact in artifacts:
            _validate_artifact_payload(artifact)
            _validate_declared_output_artifact(artifact, declared_artifacts)
            next_version = _get_next_artifact_version(
                connection,
                project_id=task.project_id,
                artifact_key=artifact.artifact_key,
            )
            persisted_artifact = ArtifactRecord(
                artifact_id=artifact.artifact_id or new_id("artifact"),
                artifact_key=artifact.artifact_key,
                artifact_type=artifact.artifact_type,
                status=artifact.status,
                version=next_version,
                produced_by_task_id=task.task_id,
                value_json=artifact.value_json,
                file_path=artifact.file_path,
            )
            connection.execute(
                """
                INSERT INTO artifacts (
                    artifact_id,
                    project_id,
                    goal_id,
                    artifact_key,
                    type,
                    status,
                    version,
                    produced_by_task_id,
                    value_json,
                    file_path,
                    metadata_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    persisted_artifact.artifact_id,
                    task.project_id,
                    task.goal_id,
                    persisted_artifact.artifact_key,
                    persisted_artifact.artifact_type,
                    persisted_artifact.status,
                    persisted_artifact.version,
                    persisted_artifact.produced_by_task_id,
                    _serialize_value_json(persisted_artifact.value_json),
                    persisted_artifact.file_path,
                    None,
                    now_iso,
                    now_iso,
                ),
            )
            persisted_artifacts.append(persisted_artifact)
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT artifact_registration")
        connection.execute("RELEASE SAVEPOINT artifact_registration")
        raise

    connection.execute("RELEASE SAVEPOINT artifact_registration")

    return persisted_artifacts


def hydrate_task_record(connection: sqlite3.Connection, task_id: str) -> TaskRecord:
    row = connection.execute(
        """
        SELECT
            task_id,
            goal_id,
            project_id,
            capability_name,
            state,
            priority,
            branch_name,
            workspace_path,
            base_commit_sha,
            result_commit_sha,
            allowed_paths_json,
            model_pool_hint,
            max_tokens,
            max_cost_usd
        FROM tasks
        WHERE task_id = ?
        """,
        (task_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown task_id: {task_id}")

    return TaskRecord(
        task_id=row["task_id"],
        goal_id=row["goal_id"],
        project_id=row["project_id"],
        capability_name=row["capability_name"],
        state=row["state"],
        priority=row["priority"],
        branch_name=row["branch_name"],
        workspace_path=row["workspace_path"],
        base_commit_sha=row["base_commit_sha"],
        result_commit_sha=row["result_commit_sha"],
        allowed_paths=json.loads(row["allowed_paths_json"]),
        required_artifacts=get_task_required_artifacts(connection, task_id),
        produced_artifacts=get_task_produced_artifacts(connection, task_id),
        model_pool_hint=row["model_pool_hint"],
        max_tokens=row["max_tokens"],
        max_cost_usd=row["max_cost_usd"],
    )


def _get_next_artifact_version(
    connection: sqlite3.Connection,
    project_id: str,
    artifact_key: str,
) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM artifacts WHERE project_id = ? AND artifact_key = ?",
        (project_id, artifact_key),
    ).fetchone()
    return int(row[0]) + 1


def _validate_artifact_payload(artifact: ArtifactRecord) -> None:
    has_value = artifact.value_json is not None
    has_file = artifact.file_path is not None
    if has_value == has_file:
        raise ValueError(
            "Artifact must contain exactly one of value_json or file_path"
        )


def _validate_declared_output_artifact(
    artifact: ArtifactRecord,
    declared_artifacts: dict[str, ProducedArtifact],
) -> None:
    declaration = declared_artifacts.get(artifact.artifact_key)
    if declaration is None:
        raise ValueError(
            f"Artifact '{artifact.artifact_key}' was not declared by the task"
        )

    if declaration.artifact_type != artifact.artifact_type:
        raise ValueError(
            f"Artifact '{artifact.artifact_key}' type mismatch: expected "
            f"{declaration.artifact_type}, got {artifact.artifact_type}"
        )

    delivery_mode = "value" if artifact.value_json is not None else "file"
    if declaration.delivery_mode != delivery_mode:
        raise ValueError(
            f"Artifact '{artifact.artifact_key}' delivery mode mismatch: expected "
            f"{declaration.delivery_mode}, got {delivery_mode}"
        )


def _serialize_value_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True)


def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
    value_json = row["value_json"]
    return ArtifactRecord(
        artifact_id=row["artifact_id"],
        artifact_key=row["artifact_key"],
        artifact_type=row["type"],
        status=row["status"],
        version=row["version"],
        produced_by_task_id=row["produced_by_task_id"],
        value_json=json.loads(value_json) if value_json is not None else None,
        file_path=row["file_path"],
    )
