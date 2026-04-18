"""Core package for the agentic runtime."""

from typing import Any

from .artifacts import is_task_executable, register_task_output_artifacts, resolve_latest_artifact
from .config import AppConfig, load_app_config
from .db import initialize_database
from .scheduler import queue_ready_tasks


def create_app(*args: Any, **kwargs: Any) -> object:
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)

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
