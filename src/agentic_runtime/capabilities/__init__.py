from __future__ import annotations

import json
import sqlite3

from ..time import utc_now_iso

# All built-in capabilities registered on startup.
BUILTIN_CAPABILITIES: dict[str, dict] = {
    "chat_response": {
        "version": "1.0.0",
        "category": "communication",
        "risk_level": "low",
        "requires_approval": 0,
        "definition": {
            "description": "Generate a conversational LLM response to the user in the goal chat.",
        },
    },
}


def register_builtins(connection: sqlite3.Connection) -> None:
    now = utc_now_iso()
    for name, meta in BUILTIN_CAPABILITIES.items():
        connection.execute(
            """
            INSERT INTO capabilities
                (capability_name, version, category, risk_level, requires_approval,
                 enabled, definition_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(capability_name) DO UPDATE SET
                version        = excluded.version,
                category       = excluded.category,
                risk_level     = excluded.risk_level,
                definition_json = excluded.definition_json,
                updated_at     = excluded.updated_at
            """,
            (
                name,
                meta["version"],
                meta["category"],
                meta["risk_level"],
                meta["requires_approval"],
                json.dumps(meta["definition"]),
                now,
                now,
            ),
        )
    connection.commit()
