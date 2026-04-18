from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ArtifactRequirement:
    artifact_key: str
    required_status: str


@dataclass(slots=True)
class ProducedArtifact:
    artifact_key: str
    artifact_type: str
    delivery_mode: str


@dataclass(slots=True)
class ArtifactRecord:
    artifact_id: str
    artifact_key: str
    artifact_type: str
    status: str
    version: int
    produced_by_task_id: str | None
    value_json: Any = None
    file_path: str | None = None


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    goal_id: str
    project_id: str
    capability_name: str
    state: str
    priority: int
    branch_name: str | None = None
    workspace_path: str | None = None
    base_commit_sha: str | None = None
    result_commit_sha: str | None = None
    allowed_paths: list[str] = field(default_factory=list)
    required_artifacts: list[ArtifactRequirement] = field(default_factory=list)
    produced_artifacts: list[ProducedArtifact] = field(default_factory=list)
    model_pool_hint: str | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None


@dataclass(slots=True)
class ExecutionContext:
    db: Any
    repo_path: str
    workspace_path: str
    project_id: str
    goal_id: str
    worker_id: str
    model_pool: str
    now_iso: str


@dataclass(slots=True)
class ExecutionResult:
    changed_files: list[str]
    output_artifacts: list[ArtifactRecord]
    summary: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
