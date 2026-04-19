from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .base import BaseCapability
from ..time import utc_now_iso

logger = logging.getLogger(__name__)


# ── Capability definition ──────────────────────────────────────────────────────

@dataclass
class CapabilityDef:
    name: str
    description: str
    risk_level: str
    system_prompt: str
    tools: list[str]
    mcp_servers: list[str]
    max_iterations: int = 20


# ── In-memory registry (populated by load_and_register) ───────────────────────

_REGISTRY: dict[str, CapabilityDef] = {}


# ── YAML loading ──────────────────────────────────────────────────────────────

def _load_yaml_dir(directory: Path) -> dict[str, CapabilityDef]:
    found: dict[str, CapabilityDef] = {}
    if not directory.exists():
        return found
    for yaml_file in sorted(directory.glob("*.yaml")):
        try:
            data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "name" not in data:
                logger.warning("Skipping capability file %s: missing 'name' field.", yaml_file)
                continue
            cap = CapabilityDef(
                name=str(data["name"]),
                description=str(data.get("description", "")),
                risk_level=str(data.get("risk_level", "low")),
                system_prompt=str(data.get("system_prompt", "")),
                tools=list(data.get("tools", [])),
                mcp_servers=list(data.get("mcp_servers", [])),
                max_iterations=int(data.get("max_iterations", 20)),
            )
            found[cap.name] = cap
            logger.debug("Loaded capability '%s' from %s.", cap.name, yaml_file.name)
        except Exception:
            logger.exception("Failed to load capability file %s.", yaml_file)
    return found


def load_and_register(
    connection: sqlite3.Connection,
    extra_dirs: list[Path] | None = None,
) -> None:
    """
    Load capability YAML files from the bundled defaults dir and any extra dirs,
    upsert them into the capabilities DB table, and populate the in-memory registry.

    Later dirs override earlier ones (same capability name).
    """
    global _REGISTRY

    defaults_dir = Path(__file__).resolve().parent.parent / "defaults" / "capabilities"
    dirs = [defaults_dir]
    if extra_dirs:
        dirs.extend(extra_dirs)

    merged: dict[str, CapabilityDef] = {}
    for d in dirs:
        merged.update(_load_yaml_dir(d))

    now = utc_now_iso()
    for defn in merged.values():
        definition_json = json.dumps({
            "description": defn.description,
            "system_prompt": defn.system_prompt,
            "tools": defn.tools,
            "mcp_servers": defn.mcp_servers,
            "max_iterations": defn.max_iterations,
        })
        connection.execute(
            """
            INSERT INTO capabilities
                (capability_name, version, category, risk_level, requires_approval,
                 enabled, definition_json, created_at, updated_at)
            VALUES (?, '1.0.0', 'general', ?, 0, 1, ?, ?, ?)
            ON CONFLICT(capability_name) DO UPDATE SET
                risk_level     = excluded.risk_level,
                definition_json = excluded.definition_json,
                updated_at     = excluded.updated_at
            """,
            (defn.name, defn.risk_level, definition_json, now, now),
        )

    connection.commit()
    _REGISTRY = merged
    logger.info("Registered %d capability/capabilities.", len(_REGISTRY))


def get_executor(
    capability_name: str,
    connection: sqlite3.Connection | None = None,
) -> BaseCapability | None:
    """
    Return a BaseCapability instance for the given name.

    Tries the in-memory registry first; falls back to reading definition_json
    from the DB if a connection is provided (useful when the worker was started
    before load_and_register was called in the current process).
    """
    defn = _REGISTRY.get(capability_name)

    if defn is None and connection is not None:
        row = connection.execute(
            "SELECT definition_json, risk_level FROM capabilities WHERE capability_name = ? AND enabled = 1",
            (capability_name,),
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["definition_json"])
        defn = CapabilityDef(
            name=capability_name,
            description=data.get("description", ""),
            risk_level=row["risk_level"],
            system_prompt=data.get("system_prompt", ""),
            tools=data.get("tools", []),
            mcp_servers=data.get("mcp_servers", []),
            max_iterations=data.get("max_iterations", 20),
        )

    if defn is None:
        return None

    cap = BaseCapability()
    cap.name = defn.name
    cap.description = defn.description
    cap.risk_level = defn.risk_level
    cap.system_prompt = defn.system_prompt
    cap.tools = list(defn.tools)
    cap.mcp_servers = list(defn.mcp_servers)
    cap.max_iterations = defn.max_iterations
    return cap


# ── Backward-compatible alias (used by app.py and cli.py) ─────────────────────

def register_builtins(connection: sqlite3.Connection) -> None:
    """Load defaults-only; kept for backward compatibility."""
    load_and_register(connection)
