from __future__ import annotations

import uuid


def new_id(prefix: str) -> str:
    normalized_prefix = prefix.strip().lower()
    if not normalized_prefix:
        raise ValueError("prefix must not be empty")
    return f"{normalized_prefix}_{uuid.uuid4().hex[:12]}"
