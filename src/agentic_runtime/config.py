from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class AppConfig:
    path: Path
    data: dict[str, Any]

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    @property
    def runtime(self) -> dict[str, Any]:
        return self.data.get("runtime", {})

    @property
    def server(self) -> dict[str, Any]:
        return self.data.get("server", {})

    @property
    def inputs(self) -> dict[str, Any]:
        return self.data.get("inputs", {})

    @property
    def llm(self) -> dict[str, Any]:
        return self.data.get("llm", {})

    @property
    def git(self) -> dict[str, Any]:
        return self.data.get("git", {})

    @property
    def planner(self) -> dict[str, Any]:
        return self.data.get("planner", {})

    @property
    def scheduler(self) -> dict[str, Any]:
        return self.data.get("scheduler", {})

    @property
    def approvals(self) -> dict[str, Any]:
        return self.data.get("approvals", {})

    @property
    def mcp(self) -> dict[str, Any]:
        return self.data.get("mcp", {"servers": []})

    @property
    def opencode(self) -> dict[str, Any]:
        return self.data.get("opencode", {})

    def runtime_workdir(self) -> Path:
        return self.resolve_path(self.runtime.get("workdir", "."), base=self.base_dir)

    def runtime_db_path(self) -> Path:
        return self.resolve_path(
            self.runtime.get("db_path", "runtime/runtime.db"),
            base=self.runtime_workdir(),
        )

    def server_enabled(self) -> bool:
        return bool(self.server.get("enabled", True))

    def server_host(self) -> str:
        return str(self.server.get("host", "127.0.0.1"))

    def server_port(self) -> int:
        return int(self.server.get("port", 8080))

    def resolve_path(self, value: str | Path, base: Path | None = None) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (base or self.base_dir) / path


def load_yaml_file(path: str | Path) -> dict[str, Any]:
    resolved_path = Path(path)
    loaded = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML root must be a mapping in {resolved_path}")
    return loaded


def load_app_config(path: str | Path) -> AppConfig:
    resolved_path = Path(path)
    return AppConfig(path=resolved_path, data=load_yaml_file(resolved_path))
