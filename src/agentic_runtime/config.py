from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


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
    loaded = _parse_yaml_mapping(resolved_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML root must be a mapping in {resolved_path}")
    return loaded


def load_app_config(path: str | Path) -> AppConfig:
    resolved_path = Path(path)
    return AppConfig(path=resolved_path, data=load_yaml_file(resolved_path))


def _parse_yaml_mapping(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    value, index = _parse_block(lines, 0, 0)
    while index < len(lines):
        if _is_meaningful(lines[index]):
            raise ValueError(f"Unexpected trailing YAML content on line {index + 1}")
        index += 1
    if not isinstance(value, dict):
        raise ValueError("YAML root must be a mapping")
    return value


def _parse_block(lines: list[str], start_index: int, indent: int) -> tuple[Any, int]:
    index = _skip_ignored(lines, start_index)
    if index >= len(lines):
        return {}, index

    current_indent = _indent_of(lines[index])
    if current_indent < indent:
        return {}, index
    if current_indent != indent:
        raise ValueError(f"Invalid indentation on line {index + 1}")

    stripped = lines[index].strip()
    if stripped.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: list[str], start_index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    index = start_index

    while index < len(lines):
        if not _is_meaningful(lines[index]):
            index += 1
            continue

        current_indent = _indent_of(lines[index])
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError(f"Invalid indentation on line {index + 1}")

        stripped = lines[index].strip()
        if stripped.startswith("- "):
            raise ValueError(f"Unexpected list item on line {index + 1}")

        key, raw_value = _split_mapping_entry(stripped, index)
        index += 1

        if raw_value == "":
            next_index = _skip_ignored(lines, index)
            if next_index < len(lines) and _indent_of(lines[next_index]) > indent:
                value, index = _parse_block(lines, next_index, indent + 2)
            else:
                value = None
        else:
            value = _parse_scalar(raw_value)

        result[key] = value

    return result, index


def _parse_list(lines: list[str], start_index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    index = start_index

    while index < len(lines):
        if not _is_meaningful(lines[index]):
            index += 1
            continue

        current_indent = _indent_of(lines[index])
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError(f"Invalid indentation on line {index + 1}")

        stripped = lines[index].strip()
        if not stripped.startswith("- "):
            break

        item_text = stripped[2:].strip()
        index += 1
        list_item: Any

        if item_text == "":
            next_index = _skip_ignored(lines, index)
            if next_index < len(lines) and _indent_of(lines[next_index]) > indent:
                list_item, index = _parse_block(lines, next_index, indent + 2)
            else:
                list_item = None
            items.append(list_item)
            continue

        if _looks_like_mapping_entry(item_text):
            key, raw_value = _split_mapping_entry(item_text, index - 1)
            mapping_item: dict[str, Any] = {}

            if raw_value == "":
                next_index = _skip_ignored(lines, index)
                if next_index < len(lines) and _indent_of(lines[next_index]) > indent:
                    nested_value, index = _parse_block(lines, next_index, indent + 2)
                else:
                    nested_value = None
                mapping_item[key] = nested_value
            else:
                mapping_item[key] = _parse_scalar(raw_value)

            next_index = _skip_ignored(lines, index)
            if next_index < len(lines) and _indent_of(lines[next_index]) > indent:
                nested_mapping, index = _parse_mapping(lines, next_index, indent + 2)
                mapping_item.update(nested_mapping)

            items.append(mapping_item)
            continue

        items.append(_parse_scalar(item_text))

    return items, index


def _split_mapping_entry(text: str, index: int) -> tuple[str, str]:
    match = re.match(r"^(?P<key>[^:]+):(\s*(?P<value>.*))?$", text)
    if not match:
        raise ValueError(f"Invalid mapping entry on line {index + 1}")
    key = match.group("key").strip()
    value = (match.group("value") or "").strip()
    if not key:
        raise ValueError(f"Empty YAML key on line {index + 1}")
    return key, value


def _parse_scalar(value: str) -> Any:
    value = _strip_inline_comment(value).strip()
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "None", "~"}:
        return None
    if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _looks_like_mapping_entry(value: str) -> bool:
    return bool(re.match(r"^[^:]+:\s*.*$", value))


def _strip_inline_comment(value: str) -> str:
    in_single_quote = False
    in_double_quote = False

    for index, char in enumerate(value):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue
        if char == "#" and not in_single_quote and not in_double_quote:
            if index == 0 or value[index - 1].isspace():
                return value[:index]

    return value


def _skip_ignored(lines: list[str], start_index: int) -> int:
    index = start_index
    while index < len(lines) and not _is_meaningful(lines[index]):
        index += 1
    return index


def _is_meaningful(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))
