"""
OpenTelemetry-like tracing for task execution.

Stores traces in a separate SQLite database (traces.db) to avoid polluting
the main runtime database.  Each task execution produces one *trace* composed
of hierarchical *spans* (capability execution → LLM calls → tool dispatches).

Usage
-----
    from agentic_runtime.tracing import init_trace_db, Tracer

    init_trace_db("/path/to/traces.db")

    tracer = Tracer.current()          # module-level singleton
    with tracer.trace(task_id=..., goal_id=..., project_id=..., capability=...) as t:
        with t.span("llm_call", attributes={...}) as s:
            ...                        # perform LLM call
            s.set_attribute("llm.tokens.prompt", 1234)
        with t.span("tool_call", attributes={"tool.name": "write_artifact"}) as s:
            ...
"""

from __future__ import annotations

import contextvars
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

from .ids import new_id
from .time import utc_now_iso

logger = logging.getLogger(__name__)

# ── Module-level state ───────────────────────────────────────────────────────

_db_path: str | None = None
_db_lock = threading.Lock()

# Context variable carrying the active trace for the current thread/task.
_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "current_trace", default=None,
)

# ── Schema ───────────────────────────────────────────────────────────────────

_TRACE_PRAGMAS = (
    "PRAGMA journal_mode = WAL;",
    "PRAGMA foreign_keys = ON;",
    "PRAGMA synchronous = NORMAL;",
)

_TRACE_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS traces (
        trace_id            TEXT PRIMARY KEY,
        task_id             TEXT NOT NULL,
        goal_id             TEXT,
        project_id          TEXT,
        capability_name     TEXT,
        status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'ok', 'error')),
        start_time          TEXT NOT NULL,
        end_time            TEXT,
        attributes_json     TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_traces_task ON traces(task_id);",
    "CREATE INDEX IF NOT EXISTS idx_traces_start ON traces(start_time DESC);",
    """
    CREATE TABLE IF NOT EXISTS spans (
        span_id             TEXT PRIMARY KEY,
        trace_id            TEXT NOT NULL REFERENCES traces(trace_id) ON DELETE CASCADE,
        parent_span_id      TEXT REFERENCES spans(span_id) ON DELETE SET NULL,
        name                TEXT NOT NULL,
        kind                TEXT NOT NULL DEFAULT 'internal'
                            CHECK (kind IN ('internal', 'llm_call', 'tool_call')),
        status              TEXT NOT NULL DEFAULT 'running'
                            CHECK (status IN ('running', 'ok', 'error')),
        start_time          TEXT NOT NULL,
        end_time            TEXT,
        attributes_json     TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id, start_time);",
    "CREATE INDEX IF NOT EXISTS idx_spans_parent ON spans(parent_span_id);",
    """
    CREATE TABLE IF NOT EXISTS span_events (
        event_id            TEXT PRIMARY KEY,
        span_id             TEXT NOT NULL REFERENCES spans(span_id) ON DELETE CASCADE,
        name                TEXT NOT NULL,
        timestamp           TEXT NOT NULL,
        attributes_json     TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_span_events_span ON span_events(span_id, timestamp);",
)

# ── DB helpers ───────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("Trace database not initialised — call init_trace_db() first.")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    for pragma in _TRACE_PRAGMAS:
        conn.execute(pragma)
    return conn


def init_trace_db(db_path: str | Path) -> None:
    """Create the trace database and apply schema (idempotent)."""
    global _db_path
    resolved = Path(db_path).resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _db_path = str(resolved)

    conn = _connect()
    try:
        for stmt in _TRACE_SCHEMA:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    logger.debug("Trace database initialised at %s", _db_path)


def trace_db_path() -> str | None:
    """Return the configured trace DB path, or None if not initialised."""
    return _db_path


# ── Span ─────────────────────────────────────────────────────────────────────


@dataclass
class Span:
    """A single unit of work within a trace."""

    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: str  # internal | llm_call | tool_call
    start_time: str
    end_time: str | None = None
    status: str = "running"
    attributes: dict[str, Any] = field(default_factory=dict)
    _children: list[Span] = field(default_factory=list, repr=False)
    _trace_ref: Trace | None = field(default=None, repr=False)

    # ── Mutation ─────────────────────────────────────────────────────────────

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: str, message: str | None = None) -> None:
        self.status = status
        if message:
            self.attributes["error.message"] = message

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Record a timestamped event within this span."""
        event_id = new_id("evt")
        ts = utc_now_iso()
        attrs_json = json.dumps(attributes) if attributes else None
        try:
            conn = _connect()
            try:
                conn.execute(
                    "INSERT INTO span_events (event_id, span_id, name, timestamp, attributes_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event_id, self.span_id, name, ts, attrs_json),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to persist span event %s", event_id, exc_info=True)

    # ── Finish ───────────────────────────────────────────────────────────────

    def end(self, status: str | None = None) -> None:
        self.end_time = utc_now_iso()
        if status:
            self.status = status
        elif self.status == "running":
            self.status = "ok"
        self._persist()

    def _persist(self) -> None:
        attrs_json = json.dumps(self.attributes) if self.attributes else None
        try:
            conn = _connect()
            try:
                conn.execute(
                    """
                    UPDATE spans
                    SET end_time = ?, status = ?, attributes_json = ?
                    WHERE span_id = ?
                    """,
                    (self.end_time, self.status, attrs_json, self.span_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to persist span %s", self.span_id, exc_info=True)


# ── Trace ────────────────────────────────────────────────────────────────────


@dataclass
class Trace:
    """A trace groups all spans produced during a single task execution."""

    trace_id: str
    task_id: str
    goal_id: str | None
    project_id: str | None
    capability_name: str | None
    start_time: str
    end_time: str | None = None
    status: str = "running"
    attributes: dict[str, Any] = field(default_factory=dict)
    _span_stack: list[Span] = field(default_factory=list, repr=False)
    _token: contextvars.Token | None = field(default=None, repr=False)

    # ── Span creation ────────────────────────────────────────────────────────

    @contextmanager
    def span(
        self,
        name: str,
        kind: str = "internal",
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Start a child span scoped to the with-block."""
        parent_id = self._span_stack[-1].span_id if self._span_stack else None
        s = self._create_span(name, kind, parent_id, attributes)
        self._span_stack.append(s)
        try:
            yield s
        except Exception as exc:
            s.set_status("error", str(exc)[:2000])
            raise
        finally:
            self._span_stack.pop()
            s.end()

    def _create_span(
        self,
        name: str,
        kind: str,
        parent_span_id: str | None,
        attributes: dict[str, Any] | None,
    ) -> Span:
        span_id = new_id("span")
        start_time = utc_now_iso()
        s = Span(
            span_id=span_id,
            trace_id=self.trace_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            start_time=start_time,
            attributes=dict(attributes) if attributes else {},
            _trace_ref=self,
        )
        attrs_json = json.dumps(s.attributes) if s.attributes else None
        try:
            conn = _connect()
            try:
                conn.execute(
                    """
                    INSERT INTO spans
                        (span_id, trace_id, parent_span_id, name, kind, status,
                         start_time, attributes_json)
                    VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (span_id, self.trace_id, parent_span_id, name, kind,
                     start_time, attrs_json),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to persist span %s", span_id, exc_info=True)
        return s

    # ── Current span ─────────────────────────────────────────────────────────

    @property
    def current_span(self) -> Span | None:
        return self._span_stack[-1] if self._span_stack else None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def end(self, status: str | None = None) -> None:
        self.end_time = utc_now_iso()
        if status:
            self.status = status
        elif self.status == "running":
            self.status = "ok"
        self._persist_end()
        # Restore context
        if self._token is not None:
            _current_trace.reset(self._token)
            self._token = None

    def _persist_end(self) -> None:
        attrs_json = json.dumps(self.attributes) if self.attributes else None
        try:
            conn = _connect()
            try:
                conn.execute(
                    """
                    UPDATE traces
                    SET end_time = ?, status = ?, attributes_json = ?
                    WHERE trace_id = ?
                    """,
                    (self.end_time, self.status, attrs_json, self.trace_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to persist trace %s", self.trace_id, exc_info=True)


# ── Tracer (factory) ────────────────────────────────────────────────────────


class Tracer:
    """Factory for creating traces.  Intended as a module-level singleton."""

    _instance: Tracer | None = None

    @classmethod
    def current(cls) -> Tracer:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Active trace access ──────────────────────────────────────────────────

    @staticmethod
    def active_trace() -> Trace | None:
        return _current_trace.get()

    # ── Trace creation ───────────────────────────────────────────────────────

    @contextmanager
    def trace(
        self,
        task_id: str,
        *,
        goal_id: str | None = None,
        project_id: str | None = None,
        capability: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Trace, None, None]:
        """Start a new trace scoped to the with-block and set it as current."""
        if _db_path is None:
            # Tracing not initialised — yield a no-op trace.
            yield _NoOpTrace()  # type: ignore[arg-type]
            return

        trace_id = new_id("trace")
        start_time = utc_now_iso()
        t = Trace(
            trace_id=trace_id,
            task_id=task_id,
            goal_id=goal_id,
            project_id=project_id,
            capability_name=capability,
            start_time=start_time,
            attributes=dict(attributes) if attributes else {},
        )
        # Set as current trace in context
        t._token = _current_trace.set(t)

        # Persist to DB
        attrs_json = json.dumps(t.attributes) if t.attributes else None
        try:
            conn = _connect()
            try:
                conn.execute(
                    """
                    INSERT INTO traces
                        (trace_id, task_id, goal_id, project_id, capability_name,
                         status, start_time, attributes_json)
                    VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (trace_id, task_id, goal_id, project_id, capability,
                     start_time, attrs_json),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("Failed to persist trace %s", trace_id, exc_info=True)

        try:
            yield t
        except Exception as exc:
            t.status = "error"
            t.attributes["error.type"] = type(exc).__name__
            t.attributes["error.message"] = str(exc)[:2000]
            raise
        finally:
            t.end()


# ── No-op fallback ──────────────────────────────────────────────────────────


class _NoOpSpan:
    """Span stub used when tracing is disabled."""

    span_id = ""
    trace_id = ""
    parent_span_id = None
    name = ""
    kind = "internal"
    status = "ok"
    attributes: dict[str, Any] = {}
    start_time = ""
    end_time = None

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: str, message: str | None = None) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def end(self, status: str | None = None) -> None:
        pass


class _NoOpTrace:
    """Trace stub used when tracing is disabled."""

    trace_id = ""
    task_id = ""
    status = "ok"
    attributes: dict[str, Any] = {}

    @contextmanager
    def span(
        self,
        name: str,
        kind: str = "internal",
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Any, None, None]:
        yield _NoOpSpan()

    @property
    def current_span(self) -> None:
        return None

    def end(self, status: str | None = None) -> None:
        pass


# ── Query helpers (for dashboard / API) ─────────────────────────────────────


def list_traces(
    *,
    task_id: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent traces, optionally filtered by task or project."""
    if _db_path is None:
        return []
    conn = _connect()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM traces {where} ORDER BY start_time DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_trace(trace_id: str) -> dict[str, Any] | None:
    """Return a single trace with all its spans and events."""
    if _db_path is None:
        return None
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
        if row is None:
            return None
        trace = dict(row)
        spans = conn.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time ASC",
            (trace_id,),
        ).fetchall()
        trace["spans"] = []
        for span_row in spans:
            span = dict(span_row)
            events = conn.execute(
                "SELECT * FROM span_events WHERE span_id = ? ORDER BY timestamp ASC",
                (span["span_id"],),
            ).fetchall()
            span["events"] = [dict(e) for e in events]
            trace["spans"].append(span)
        return trace
    finally:
        conn.close()
