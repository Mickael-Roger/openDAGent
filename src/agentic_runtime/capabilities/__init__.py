from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .base import BaseCapability
from ..time import utc_now_iso

logger = logging.getLogger(__name__)

# LLM features that a capability may require
LLM_FEATURES = [
    "vision",             # can process images in the prompt (multimodal input)
    "reasoning",          # extended thinking / chain-of-thought (o1, claude-thinking)
    "json_mode",          # guaranteed structured JSON output
    "long_context",       # 100k+ token context window
    "code",               # specialised code generation
    "image_generation",   # can generate images (any diffusion model via compatible API)
    "native_web_search",  # built-in web search (Perplexity, GPT-4o browsing, etc.)
]

RISK_LEVELS = ["low", "medium", "high", "critical"]


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
    llm_features: list[str] = field(default_factory=list)
    # Optional availability gate: if non-empty, at least one condition must be
    # satisfied at startup for this capability to be registered.
    # Format: "env:VAR_NAME"     → env var VAR_NAME must be non-empty
    #         "feature:FEAT"     → LLM feature FEAT must be in supported set
    availability_conditions: list[str] = field(default_factory=list)


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
            cap = _cap_from_dict(data)
            found[cap.name] = cap
            logger.debug("Loaded capability '%s' from %s.", cap.name, yaml_file.name)
        except Exception:
            logger.exception("Failed to load capability file %s.", yaml_file)
    return found


def _cap_from_dict(data: dict[str, Any]) -> CapabilityDef:
    return CapabilityDef(
        name=str(data["name"]),
        description=str(data.get("description", "")),
        risk_level=str(data.get("risk_level", "low")),
        system_prompt=str(data.get("system_prompt", "")),
        tools=list(data.get("tools", [])),
        mcp_servers=list(data.get("mcp_servers", [])),
        max_iterations=int(data.get("max_iterations", 20)),
        llm_features=list(data.get("llm_features", [])),
        availability_conditions=list(data.get("availability_conditions", [])),
    )


# ── Persistence helpers ───────────────────────────────────────────────────────

def save_user_capability(defn: CapabilityDef, user_caps_dir: Path) -> Path:
    """Write a CapabilityDef as YAML into user_caps_dir and return the file path."""
    user_caps_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "name": defn.name,
        "description": defn.description,
        "risk_level": defn.risk_level,
        "max_iterations": defn.max_iterations,
        "llm_features": defn.llm_features,
        "availability_conditions": defn.availability_conditions,
        "tools": defn.tools,
        "mcp_servers": defn.mcp_servers,
        "system_prompt": defn.system_prompt,
    }
    yaml_path = user_caps_dir / f"{defn.name}.yaml"
    yaml_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return yaml_path


def delete_user_capability_file(name: str, user_caps_dir: Path) -> bool:
    """Remove a user capability YAML file. Returns True if deleted."""
    yaml_path = user_caps_dir / f"{name}.yaml"
    if yaml_path.exists():
        yaml_path.unlink()
        return True
    return False


def is_user_capability(name: str, user_caps_dir: Path | None) -> bool:
    if user_caps_dir is None:
        return False
    return (user_caps_dir / f"{name}.yaml").exists()


# ── DB upsert ─────────────────────────────────────────────────────────────────

def _upsert_capability(connection: sqlite3.Connection, defn: CapabilityDef, now: str) -> None:
    definition_json = json.dumps({
        "description": defn.description,
        "system_prompt": defn.system_prompt,
        "tools": defn.tools,
        "mcp_servers": defn.mcp_servers,
        "max_iterations": defn.max_iterations,
        "llm_features": defn.llm_features,
        "availability_conditions": defn.availability_conditions,
    })
    connection.execute(
        """
        INSERT INTO capabilities
            (capability_name, version, category, risk_level, requires_approval,
             enabled, definition_json, created_at, updated_at)
        VALUES (?, '1.0.0', 'general', ?, 0, 1, ?, ?, ?)
        ON CONFLICT(capability_name) DO UPDATE SET
            risk_level      = excluded.risk_level,
            definition_json = excluded.definition_json,
            updated_at      = excluded.updated_at
        """,
        (defn.name, defn.risk_level, definition_json, now, now),
    )


# ── LLM feature resolution ────────────────────────────────────────────────────

def _supported_features(llm_config: dict[str, Any] | None) -> set[str]:
    """
    Return the set of all LLM features declared across all configured models.
    Models declare features as: models: [{id: ..., features: [vision, ...]}]
    """
    if not llm_config:
        return set()
    features: set[str] = set()
    for provider in llm_config.get("providers", []):
        for model in provider.get("models", []):
            features.update(model.get("features", []))
    return features


def _capability_is_available(defn: CapabilityDef, supported: set[str]) -> bool:
    """
    A capability is available when:
    1. All llm_features requirements are satisfied by at least one configured model.
    2. If availability_conditions is non-empty, at least one condition is met:
       - "env:VAR"     → environment variable VAR is set and non-empty
       - "feature:F"   → LLM feature F is in the supported set
    """
    if not all(f in supported for f in defn.llm_features):
        return False
    if defn.availability_conditions:
        for cond in defn.availability_conditions:
            if cond.startswith("env:") and os.environ.get(cond[4:], "").strip():
                return True
            if cond.startswith("feature:") and cond[8:] in supported:
                return True
        return False
    return True


# ── load_and_register ─────────────────────────────────────────────────────────

def load_and_register(
    connection: sqlite3.Connection,
    extra_dirs: list[Path] | None = None,
    llm_config: dict[str, Any] | None = None,
) -> None:
    """
    Load capability YAML files from the bundled defaults dir and any extra dirs,
    upsert them into the capabilities DB table, and populate the in-memory registry.

    Capabilities whose llm_features requirements are not met by any configured
    LLM model are skipped (not registered).

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

    supported = _supported_features(llm_config)

    now = utc_now_iso()
    registered: dict[str, CapabilityDef] = {}
    skipped: list[str] = []

    for defn in merged.values():
        if not _capability_is_available(defn, supported):
            skipped.append(
                f"'{defn.name}' (requires: {defn.llm_features})"
            )
            continue
        _upsert_capability(connection, defn, now)
        registered[defn.name] = defn

    connection.commit()
    _REGISTRY = registered

    if skipped:
        logger.info(
            "Skipped %d capability/capabilities (LLM features not available): %s",
            len(skipped), ", ".join(skipped),
        )
    logger.info("Registered %d capability/capabilities.", len(_REGISTRY))


def register_capability(
    connection: sqlite3.Connection,
    defn: CapabilityDef,
) -> None:
    """Register (or update) a single capability in the DB and in-memory registry."""
    global _REGISTRY
    now = utc_now_iso()
    _upsert_capability(connection, defn, now)
    connection.commit()
    _REGISTRY[defn.name] = defn


def deregister_capability(
    connection: sqlite3.Connection,
    name: str,
) -> None:
    """Remove a capability from the DB and in-memory registry."""
    global _REGISTRY
    connection.execute("DELETE FROM capabilities WHERE capability_name = ?", (name,))
    connection.commit()
    _REGISTRY.pop(name, None)


# ── get_executor ──────────────────────────────────────────────────────────────

def get_executor(
    capability_name: str,
    connection: sqlite3.Connection | None = None,
) -> BaseCapability | None:
    """
    Return a BaseCapability instance for the given name.

    Tries the in-memory registry first; falls back to reading definition_json
    from the DB if a connection is provided.
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
            llm_features=data.get("llm_features", []),
            availability_conditions=data.get("availability_conditions", []),
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
    cap.llm_features = list(defn.llm_features)
    cap.availability_conditions = list(defn.availability_conditions)
    return cap


# ── Backward-compatible alias ──────────────────────────────────────────────────

def register_builtins(connection: sqlite3.Connection) -> None:
    load_and_register(connection)
