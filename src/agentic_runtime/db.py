from __future__ import annotations

import sqlite3
from pathlib import Path

PRAGMAS = (
    "PRAGMA journal_mode = WAL;",
    "PRAGMA foreign_keys = ON;",
    "PRAGMA synchronous = NORMAL;",
)

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        description TEXT,
        state TEXT NOT NULL CHECK (state IN ('draft','activated','paused','archived','failed')),
        local_repo_path TEXT NOT NULL,
        default_branch TEXT NOT NULL DEFAULT 'main',
        github_owner TEXT,
        github_repo TEXT,
        github_repo_url TEXT,
        github_repo_status TEXT NOT NULL DEFAULT 'not_created',
        visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','public','internal')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS goals (
        goal_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        parent_goal_id TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        description TEXT,
        source_channel TEXT NOT NULL,
        source_thread_ref TEXT,
        state TEXT NOT NULL CHECK (state IN ('draft','active','paused','completed','cancelled','failed')),
        priority INTEGER NOT NULL DEFAULT 50,
        approval_mode TEXT NOT NULL DEFAULT 'human_for_external_actions',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_goals_project_state ON goals(project_id, state);",
    """
    CREATE TABLE IF NOT EXISTS goal_messages (
        message_id TEXT PRIMARY KEY,
        goal_id TEXT REFERENCES goals(goal_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        author_type TEXT NOT NULL CHECK (author_type IN ('user','system','worker')),
        source_channel TEXT NOT NULL,
        source_message_ref TEXT,
        content TEXT NOT NULL,
        message_ts TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_goal_messages_goal_ts ON goal_messages(goal_id, message_ts);",
    """
    CREATE TABLE IF NOT EXISTS capabilities (
        capability_name TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        category TEXT NOT NULL,
        risk_level TEXT NOT NULL CHECK (risk_level IN ('low','medium','high','critical')),
        requires_approval INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1,
        definition_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL REFERENCES goals(goal_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        parent_task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
        originating_change_request_id TEXT REFERENCES change_requests(change_request_id) ON DELETE SET NULL,
        capability_name TEXT NOT NULL REFERENCES capabilities(capability_name),
        title TEXT NOT NULL,
        description TEXT,
        state TEXT NOT NULL CHECK (
            state IN (
                'created','queued','claimed','running',
                'paused_pending_change_review','paused_manual',
                'blocked','done','failed','cancelled',
                'superseded','rollback_pending','rolled_back'
            )
        ),
        priority INTEGER NOT NULL DEFAULT 50,
        branch_name TEXT,
        workspace_path TEXT,
        base_commit_sha TEXT,
        result_commit_sha TEXT,
        allowed_paths_json TEXT NOT NULL,
        model_pool_hint TEXT,
        max_tokens INTEGER,
        max_cost_usd REAL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        max_retries INTEGER NOT NULL DEFAULT 2,
        lease_owner_worker_id TEXT,
        lease_expires_at TEXT,
        started_at TEXT,
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_goal_state_priority ON tasks(goal_id, state, priority DESC, created_at ASC);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_capability_state ON tasks(capability_name, state, priority DESC, created_at ASC);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_worker_lease ON tasks(lease_owner_worker_id, lease_expires_at);",
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        goal_id TEXT REFERENCES goals(goal_id) ON DELETE CASCADE,
        artifact_key TEXT NOT NULL,
        type TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','active','approved','superseded','rejected','archived')),
        version INTEGER NOT NULL,
        produced_by_task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
        value_json TEXT,
        file_path TEXT,
        metadata_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            (value_json IS NOT NULL AND file_path IS NULL)
            OR (value_json IS NULL AND file_path IS NOT NULL)
        ),
        UNIQUE(project_id, artifact_key, version)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_project_key ON artifacts(project_id, artifact_key, version DESC);",
    "CREATE INDEX IF NOT EXISTS idx_artifacts_key_status ON artifacts(project_id, artifact_key, status, version DESC);",
    """
    CREATE TABLE IF NOT EXISTS task_required_artifacts (
        requirement_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        artifact_key TEXT NOT NULL,
        required_status TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_required_artifacts_task ON task_required_artifacts(task_id);",
    "CREATE INDEX IF NOT EXISTS idx_task_required_artifacts_key ON task_required_artifacts(artifact_key, required_status);",
    """
    CREATE TABLE IF NOT EXISTS task_produced_artifacts (
        production_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        artifact_key TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('value','file')),
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_produced_artifacts_task ON task_produced_artifacts(task_id);",
    """
    CREATE TABLE IF NOT EXISTS task_attempts (
        attempt_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        worker_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('running','done','failed','cancelled')),
        started_at TEXT NOT NULL,
        ended_at TEXT,
        input_snapshot_json TEXT,
        output_summary_json TEXT,
        error_type TEXT,
        error_message TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_attempts_task ON task_attempts(task_id, started_at);",
    """
    CREATE TABLE IF NOT EXISTS workers (
        worker_id TEXT PRIMARY KEY,
        worker_type TEXT NOT NULL,
        hostname TEXT,
        pid INTEGER,
        supported_capabilities_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('idle','busy','heartbeat_lost','disabled')),
        current_task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
        heartbeat_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
        goal_id TEXT REFERENCES goals(goal_id) ON DELETE CASCADE,
        task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
        worker_id TEXT REFERENCES workers(worker_id) ON DELETE SET NULL,
        event_type TEXT NOT NULL,
        event_payload_json TEXT,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_goal_created ON events(goal_id, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_events_task_created ON events(task_id, created_at);",
    """
    CREATE TABLE IF NOT EXISTS baselines (
        baseline_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        goal_id TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
        label TEXT NOT NULL,
        main_commit_sha TEXT NOT NULL,
        validated_artifacts_json TEXT NOT NULL,
        open_tasks_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_baselines_project_created ON baselines(project_id, created_at DESC);",
    """
    CREATE TABLE IF NOT EXISTS change_requests (
        change_request_id TEXT PRIMARY KEY,
        goal_id TEXT NOT NULL REFERENCES goals(goal_id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        source_message_id TEXT REFERENCES goal_messages(message_id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN (
                'received','freeze_started','frozen',
                'impact_analysis_running','impact_analyzed',
                'replan_running','replanned',
                'resume_running','applied','rejected'
            )
        ),
        analysis_artifact_id TEXT REFERENCES artifacts(artifact_id) ON DELETE SET NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS change_task_decisions (
        decision_id TEXT PRIMARY KEY,
        change_request_id TEXT NOT NULL REFERENCES change_requests(change_request_id) ON DELETE CASCADE,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        decision TEXT NOT NULL CHECK (
            decision IN (
                'continue_as_is',
                'continue_then_reuse',
                'stop_and_rollback',
                'fork_output',
                'cancel'
            )
        ),
        rationale TEXT,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_change_task_decisions_change ON change_task_decisions(change_request_id);",
    """
    CREATE TABLE IF NOT EXISTS approvals (
        approval_id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
        goal_id TEXT REFERENCES goals(goal_id) ON DELETE CASCADE,
        task_id TEXT REFERENCES tasks(task_id) ON DELETE CASCADE,
        scope TEXT NOT NULL,
        request_payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','expired')),
        requested_at TEXT NOT NULL,
        decided_at TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS model_pools (
        pool_name TEXT PRIMARY KEY,
        provider TEXT NOT NULL,
        category TEXT NOT NULL,
        daily_token_budget INTEGER,
        monthly_token_budget INTEGER,
        daily_tokens_used INTEGER NOT NULL DEFAULT 0,
        monthly_tokens_used INTEGER NOT NULL DEFAULT 0,
        max_parallel_tasks INTEGER NOT NULL DEFAULT 2,
        enabled INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS task_costs (
        cost_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
        pool_name TEXT REFERENCES model_pools(pool_name) ON DELETE SET NULL,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        estimated_cost_usd REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_costs_task ON task_costs(task_id);",
)


def connect(db_path: str | Path) -> sqlite3.Connection:
    resolved_path = Path(db_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(resolved_path)
    connection.row_factory = sqlite3.Row
    apply_pragmas(connection)
    return connection


def apply_pragmas(connection: sqlite3.Connection) -> None:
    for pragma in PRAGMAS:
        connection.execute(pragma)


def initialize_database(db_path: str | Path) -> sqlite3.Connection:
    connection = connect(db_path)
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()
    except Exception:
        connection.rollback()
        connection.close()
        raise
    return connection
