from __future__ import annotations

from importlib import import_module
from pathlib import Path


__path__ = [
    str(Path(__file__).resolve().parent.parent / "src" / "agentic_runtime")
]

_config = import_module("agentic_runtime.config")
_db = import_module("agentic_runtime.db")
_artifacts = import_module("agentic_runtime.artifacts")
_app = import_module("agentic_runtime.app")
_scheduler = import_module("agentic_runtime.scheduler")

AppConfig = _config.AppConfig
create_app = _app.create_app
load_app_config = _config.load_app_config
initialize_database = _db.initialize_database
resolve_latest_artifact = _artifacts.resolve_latest_artifact
register_task_output_artifacts = _artifacts.register_task_output_artifacts
is_task_executable = _artifacts.is_task_executable
queue_ready_tasks = _scheduler.queue_ready_tasks

__all__ = [
    "AppConfig",
    "create_app",
    "initialize_database",
    "is_task_executable",
    "load_app_config",
    "queue_ready_tasks",
    "register_task_output_artifacts",
    "resolve_latest_artifact",
]
