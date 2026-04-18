# Agentic AI Execution Framework — Implementation Specification
## Capability-Driven, Git-Centric, SQLite-Orchestrated System
### Hardcore Implementation Version

---

## 0. Document Purpose

This document is an implementation-oriented specification for a capability-driven agentic AI framework.  
It is written to be used directly by a coding agent or engineering team.

This is **not** a conceptual overview.  
It is a build specification.

The framework is designed for:
- long-running multi-step work,
- user-driven changes during execution,
- concurrent goals/projects,
- deterministic orchestration with selective AI reasoning,
- Git-based artifact production and rollback,
- SQLite-based runtime control.

---

## 1. Design Goals

### 1.1 Primary goals

The system must:

1. accept work from one or more input channels,
2. convert that work into explicit goals,
3. break goals into executable tasks,
4. execute tasks using constrained capabilities,
5. persist every meaningful output as files,
6. version those files with Git,
7. support partial rollback and branch-based iteration,
8. handle user changes while work is already in progress,
9. provide precise status reporting,
10. keep runtime orchestration understandable and inspectable.

### 1.2 Non-goals

The system is not intended to:
- simulate a human company,
- rely on endless chat between agents,
- store all state in prompts,
- use Git as the live runtime lock manager,
- let all capabilities act with unrestricted filesystem or external access.

### 1.3 Guiding principle

> The system is an execution engine with AI-powered planning and generation, not a roleplay environment.

---

## 2. High-Level Model

The framework operates on the following primitives:

- **Project**: a durable product/application container
- **Goal**: a user objective within a project
- **Task**: an executable work unit
- **Capability**: a constrained execution contract
- **Artifact**: a file output
- **Baseline**: a stable validated project snapshot
- **Change Request**: a scope mutation applied to an active goal
- **Worker**: a process able to execute one or more capabilities

### 2.1 Flow summary

1. user sends input,
2. input is normalized,
3. a goal is created,
4. a planner generates a task graph,
5. tasks are queued in SQLite,
6. workers claim tasks,
7. each task runs inside an isolated Git workspace,
8. artifacts are written and committed,
9. downstream tasks consume those artifacts,
10. status snapshots are generated from runtime facts,
11. change requests freeze future tasks and trigger impact analysis,
12. the plan is revised and execution resumes.

---

## 3. Core Architecture

### 3.1 Main services/processes

Minimal V1 processes:

1. **ingress service**
2. **goal manager**
3. **planner**
4. **scheduler**
5. **worker pool**
6. **change manager**
7. **status snapshot generator**
8. **GitHub integration capability runner**
9. **approval manager**
10. **CLI/admin tools**

### 3.2 Recommended runtime topology (V1)

Single machine deployment:

- `runtime.db` — SQLite database
- local filesystem for repos/worktrees/artifacts
- Python processes for services/workers
- Git CLI installed
- optional Docker for isolation
- external LLM APIs
- optional GitHub App credentials
- optional OVH/domain integrations later

### 3.3 Repository layout on disk

```text
/workspace/
  runtime/
    runtime.db
    logs/
    config/
      app.yaml
      model_routing.yaml
      capabilities/
        spec.product.refine.yaml
        design.system.webapp.yaml
        scm.github.repo.bootstrap.yaml
  projects/
    proj_001/
      project_repo/
      worktrees/
        task_t1_product_spec/
        task_t2_architecture/
        change_003_permissions/
      artifacts_cache/
      exports/
  temp/
  sandboxes/
```

### 3.4 Source of truth split

Use this split strictly:

- **SQLite** = runtime truth
- **Git repo** = project work truth
- **filesystem outside repo** = temporary execution data only
- **external systems** = side effects, mirrored in SQLite events

Do not invert this split.

---

## 4. Runtime State Machine

### 4.1 Project states

- `draft`
- `activated`
- `paused`
- `archived`
- `failed`

### 4.2 Goal states

- `draft`
- `active`
- `paused`
- `completed`
- `cancelled`
- `failed`

### 4.3 Task states

- `created`
- `queued`
- `claimed`
- `running`
- `paused_pending_change_review`
- `paused_manual`
- `blocked`
- `done`
- `failed`
- `cancelled`
- `superseded`
- `rollback_pending`
- `rolled_back`

### 4.4 Change request states

- `received`
- `freeze_started`
- `frozen`
- `impact_analysis_running`
- `impact_analyzed`
- `replan_running`
- `replanned`
- `resume_running`
- `applied`
- `rejected`

### 4.5 Worker states

- `idle`
- `busy`
- `heartbeat_lost`
- `disabled`

---

## 5. SQLite Schema

Use SQLite in WAL mode.

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
```

### 5.1 projects

```sql
CREATE TABLE projects (
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
```

### 5.2 goals

```sql
CREATE TABLE goals (
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
CREATE INDEX idx_goals_project_state ON goals(project_id, state);
```

### 5.3 goal_messages

```sql
CREATE TABLE goal_messages (
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
CREATE INDEX idx_goal_messages_goal_ts ON goal_messages(goal_id, message_ts);
```

### 5.4 artifacts

```sql
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    goal_id TEXT REFERENCES goals(goal_id) ON DELETE CASCADE,
    producing_task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
    logical_path TEXT NOT NULL,
    repo_relative_path TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','superseded','rejected','branched')),
    commit_sha TEXT,
    branch_name TEXT,
    content_hash TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, logical_path, version)
);
CREATE INDEX idx_artifacts_goal_path ON artifacts(goal_id, logical_path);
```

### 5.5 capabilities

```sql
CREATE TABLE capabilities (
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
```

### 5.6 tasks

```sql
CREATE TABLE tasks (
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
    depends_on_count INTEGER NOT NULL DEFAULT 0,
    unresolved_dependencies INTEGER NOT NULL DEFAULT 0,
    branch_name TEXT,
    workspace_path TEXT,
    base_commit_sha TEXT,
    result_commit_sha TEXT,
    allowed_paths_json TEXT NOT NULL,
    input_artifacts_json TEXT NOT NULL,
    output_artifacts_json TEXT NOT NULL,
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
CREATE INDEX idx_tasks_goal_state_priority ON tasks(goal_id, state, priority DESC, created_at ASC);
CREATE INDEX idx_tasks_capability_state ON tasks(capability_name, state, priority DESC, created_at ASC);
CREATE INDEX idx_tasks_worker_lease ON tasks(lease_owner_worker_id, lease_expires_at);
```

### 5.7 task_dependencies

```sql
CREATE TABLE task_dependencies (
    upstream_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    downstream_task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    PRIMARY KEY (upstream_task_id, downstream_task_id)
);
CREATE INDEX idx_task_deps_downstream ON task_dependencies(downstream_task_id);
```

### 5.8 task_attempts

```sql
CREATE TABLE task_attempts (
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
CREATE INDEX idx_task_attempts_task ON task_attempts(task_id, started_at);
```

### 5.9 workers

```sql
CREATE TABLE workers (
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
```

### 5.10 events

```sql
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    goal_id TEXT REFERENCES goals(goal_id) ON DELETE CASCADE,
    task_id TEXT REFERENCES tasks(task_id) ON DELETE SET NULL,
    worker_id TEXT REFERENCES workers(worker_id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    event_payload_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_events_goal_created ON events(goal_id, created_at);
CREATE INDEX idx_events_task_created ON events(task_id, created_at);
```

### 5.11 baselines

```sql
CREATE TABLE baselines (
    baseline_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    goal_id TEXT REFERENCES goals(goal_id) ON DELETE SET NULL,
    label TEXT NOT NULL,
    main_commit_sha TEXT NOT NULL,
    validated_artifacts_json TEXT NOT NULL,
    open_tasks_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_baselines_project_created ON baselines(project_id, created_at DESC);
```

### 5.12 change_requests

```sql
CREATE TABLE change_requests (
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
```

### 5.13 change_task_decisions

```sql
CREATE TABLE change_task_decisions (
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
CREATE INDEX idx_change_task_decisions_change ON change_task_decisions(change_request_id);
```

### 5.14 approvals

```sql
CREATE TABLE approvals (
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
```

### 5.15 model_pools

```sql
CREATE TABLE model_pools (
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
```

### 5.16 task_costs

```sql
CREATE TABLE task_costs (
    cost_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    pool_name TEXT REFERENCES model_pools(pool_name) ON DELETE SET NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_task_costs_task ON task_costs(task_id);
```

---

## 6. Capability Definitions

Capabilities must be defined in YAML files under:

```text
/workspace/runtime/config/capabilities/
```

### 6.1 Example capability file

```yaml
name: design.system.webapp
version: "1.0"
category: design
risk_level: low
requires_approval: false

inputs:
  required_artifacts:
    - product/product_brief.md
    - product/feature_list.md

outputs:
  expected_artifacts:
    - architecture/system_design.md
    - architecture/data_model.md

allowed_paths:
  - architecture/**
  - docs/**

forbidden_paths:
  - app/**
  - infra/**

tools:
  - file.read
  - file.write
  - git.status
  - git.add
  - git.commit

model_policy:
  preferred_pool: strong_reasoning
  fallback_pools:
    - balanced
    - cheap_fast

budgets:
  max_tokens: 24000
  max_cost_usd: 1.50
  max_runtime_seconds: 900

merge_policy:
  mode: review_required
  require_clean_worktree: true

task_contract:
  input_schema: DesignSystemRequestV1
  output_schema: DesignSystemResultV1
```

### 6.2 Required capability fields

Every capability definition must contain:

- `name`
- `version`
- `category`
- `risk_level`
- `requires_approval`
- `inputs`
- `outputs`
- `allowed_paths`
- `tools`
- `model_policy`
- `budgets`
- `task_contract`

### 6.3 Risk levels

- `low` — file generation, analysis, documentation
- `medium` — code modification, config generation, repo writes
- `high` — external APIs, repo creation, infra mutation
- `critical` — destructive or costly external side effects

---

## 7. Git Model

### 7.1 One repo per project

Each project has exactly one primary Git repository.

Path:

```text
/workspace/projects/{project_id}/project_repo/
```

### 7.2 Branching convention

Use branch per task or branch per change request.

Examples:

```text
goal_001/task_t1_product_spec
goal_001/task_t2_architecture
goal_001/change_cr_003_permissions_redesign
```

Do not use branch-per-capability.

### 7.3 Worktree convention

Each active task gets an isolated worktree.

Example:

```text
/workspace/projects/proj_001/worktrees/task_t2_architecture/
```

Command example:

```bash
git -C /workspace/projects/proj_001/project_repo worktree add   /workspace/projects/proj_001/worktrees/task_t2_architecture   -b goal_001/task_t2_architecture main
```

If the branch already exists, attach the existing branch instead of creating it.

### 7.4 Commit message convention

Format:

```text
[task:{task_id}] {commit_type}: {summary}
```

Allowed commit types:
- `checkpoint`
- `tool-output`
- `review-fix`
- `merge-ready`
- `rollback`
- `change-freeze`

Examples:

```text
[task:t1_product_spec] tool-output: initial product brief draft
[task:t2_architecture] checkpoint: initial system design complete
[task:t8_auth_recovery] review-fix: add password reset flow
```

### 7.5 Required per-task Git flow

1. checkout/create branch,
2. record base commit in SQLite,
3. write or modify files,
4. validate allowed paths,
5. git add only allowed files,
6. commit,
7. store resulting commit SHA,
8. optionally merge later according to policy.

### 7.6 Merge policy

Default for V1:
- task branches do not auto-merge into `main` immediately,
- they complete and remain reviewable,
- scheduler or a dedicated integration capability decides merge order.

Recommended integration order:
1. product/design docs
2. architecture/data model
3. code bootstrap
4. infra plans
5. deploy artifacts

### 7.7 Baselines

A baseline is a validated project state.

Create baselines at:
- after project initialization,
- after validated product brief,
- after validated architecture,
- after code bootstrap,
- before deployment,
- after any major change request is applied.

---

## 8. Runtime and Worker Coordination

### 8.1 Why SQLite is used

SQLite is the runtime control plane because it is:
- simple,
- local,
- inspectable,
- enough for V1.

It is not used because it is ideal at scale.  
It is used because it makes the system understandable.

### 8.2 Claim protocol

Workers must claim tasks atomically enough for single-node operation.

Recommended approach:
1. begin immediate transaction,
2. select one task in `queued` matching capability and not lease-held,
3. set state to `claimed`,
4. set lease owner and lease expiry,
5. commit.

Pseudo-SQL:

```sql
BEGIN IMMEDIATE;

SELECT task_id
FROM tasks
WHERE capability_name = :capability_name
  AND state = 'queued'
  AND unresolved_dependencies = 0
  AND (lease_expires_at IS NULL OR lease_expires_at < :now)
ORDER BY priority DESC, created_at ASC
LIMIT 1;
```

If a task is found:

```sql
UPDATE tasks
SET state = 'claimed',
    lease_owner_worker_id = :worker_id,
    lease_expires_at = :lease_expires_at,
    updated_at = :now
WHERE task_id = :task_id;
```

Then commit.

### 8.3 Lease renewal

Workers running long tasks must renew leases periodically.

Lease heartbeat interval:
- every 20–30 seconds

Lease duration:
- 90–120 seconds

On heartbeat:
- update `lease_expires_at`
- update worker heartbeat
- optionally emit `task.heartbeat` event

### 8.4 Lost workers

A maintenance loop should detect:
- tasks in `claimed` or `running`
- lease expired
- worker heartbeat stale

Recovery policy:
- first attempt: move task back to `queued`
- if repeated failures exceed threshold: `failed`
- if worktree exists with partial files: preserve and mark in event log

---

## 9. Planner Specification

### 9.1 Planner role

The planner is responsible for:
- reading normalized intent and existing artifacts,
- generating a task graph,
- selecting capabilities,
- estimating dependencies,
- assigning initial priority,
- specifying expected outputs.

The planner does not:
- directly execute tasks,
- directly modify external systems,
- hold runtime locks.

### 9.2 Planner inputs

- goal brief
- user messages for the goal
- current baseline
- existing artifacts
- active change requests
- enabled capabilities
- model pool state
- policy rules

### 9.3 Planner output artifact

`planning/execution_plan_v{n}.json`

Example schema:

```json
{
  "goal_id": "goal_001",
  "plan_version": 2,
  "tasks": [
    {
      "task_id": "t1_product_spec",
      "title": "Create product brief",
      "capability_name": "spec.product.refine",
      "depends_on": [],
      "priority": 90,
      "allowed_paths": ["product/**"],
      "input_artifacts": [
        "intake/normalized_intent_v2.json"
      ],
      "output_artifacts": [
        "product/product_brief.md",
        "product/feature_list.md"
      ]
    }
  ]
}
```

### 9.4 Planner constraints

The planner must:
- prefer reuse over regeneration,
- prefer partial replan over full reset,
- avoid creating too many tiny tasks,
- avoid too-broad tasks touching unrelated paths,
- keep task count bounded for V1.

Recommended initial limit:
- maximum 15 active tasks created per plan generation

---

## 10. Scheduler Specification

### 10.1 Scheduler responsibilities

The scheduler is the deterministic orchestration core.

It must:
- move tasks from `created` to `queued`,
- enforce dependency readiness,
- apply priority rules,
- route by model pool hints,
- freeze/resume tasks during change management,
- request approvals for risky actions,
- generate status snapshots.

### 10.2 Dependency resolution

When a task completes:
- decrement `unresolved_dependencies` for downstream tasks
- if it reaches zero and task state is `created`, move to `queued`

Pseudo-logic:

```python
for downstream in get_downstream_tasks(completed_task_id):
    decrement_unresolved_dependencies(downstream.task_id)
    if downstream.unresolved_dependencies == 0 and downstream.state == "created":
        queue_task(downstream.task_id)
```

### 10.3 Priority formula

Suggested V1 priority score:

```text
effective_priority =
  goal_priority
  + task_priority_modifier
  + urgency_modifier
  + blocked_dependency_release_bonus
  - estimated_cost_penalty
```

Keep it simple initially.

### 10.4 Approval-aware scheduling

If task capability requires approval:
- do not move task to `queued` until approval is granted
- instead move to `blocked`
- create approval record
- once approved, transition to `queued`

### 10.5 Quota-aware routing

Scheduler should select a model pool for a task using:
- capability preferred pool
- remaining budget
- pool max parallel tasks
- risk level
- task criticality

If preferred pool unavailable:
- use first allowed fallback
- record downgrade in events

---

## 11. Worker Execution Protocol

### 11.1 Worker responsibilities

Each worker process:
- registers itself,
- advertises supported capabilities,
- polls for tasks,
- claims one task,
- sets up worktree,
- loads inputs,
- invokes model/tool chain,
- validates outputs,
- commits work,
- updates SQLite state,
- emits events.

### 11.2 Worker loop pseudocode

```python
while True:
    heartbeat_worker()

    task = claim_next_task(worker_id, supported_capabilities)
    if not task:
        sleep(POLL_INTERVAL)
        continue

    try:
        mark_task_running(task.task_id)
        setup_task_workspace(task)
        load_input_artifacts(task)
        execute_capability(task)
        validate_allowed_paths(task)
        commit_task_outputs(task)
        register_artifacts(task)
        mark_task_done(task.task_id)
        release_dependencies(task.task_id)
    except RetryableError as e:
        handle_retryable_failure(task, e)
    except NonRetryableError as e:
        mark_task_failed(task.task_id, e)
    finally:
        detach_or_clean_workspace(task)
```

### 11.3 Workspace setup

For every task:
- derive branch name if absent,
- create or attach worktree,
- ensure clean Git state,
- write `task_manifest.yaml` into workspace root,
- copy/render non-repo temporary prompt inputs if needed.

### 11.4 Allowed path validation

Before committing:
- inspect all changed files,
- ensure every changed file matches `allowed_paths`,
- reject execution otherwise.

Pseudo-logic:

```python
changed_files = git_diff_name_only(workspace)
for path in changed_files:
    if not path_matches_any(path, task.allowed_paths):
        raise NonRetryableError(f"Path not allowed: {path}")
```

### 11.5 Artifact registration

After successful commit:
- each output artifact is inserted into `artifacts`
- link `producing_task_id`
- record `commit_sha`
- version increment logical path if artifact existed

---

## 12. Artifact Conventions

### 12.1 Mandatory top-level directories in each project repo

```text
/intake/
/planning/
/product/
/architecture/
/app/
/infra/
/status/
/reviews/
/manifests/
```

### 12.2 Standard artifact paths

Examples:

```text
/intake/normalized_intent_v1.json
/planning/goal_brief_v1.md
/planning/execution_plan_v1.json
/product/product_brief_v1.md
/product/feature_list_v1.md
/architecture/system_design_v1.md
/architecture/data_model_v1.md
/reviews/redteam_report_v1.md
/status/progress_snapshot_v3.json
/manifests/project_manifest.yaml
```

### 12.3 Artifact metadata

Every important generated artifact should include frontmatter or metadata block with:
- artifact id
- producing task id
- goal id
- version
- date
- status
- dependency references

Markdown frontmatter example:

```yaml
---
artifact_id: art_00123
goal_id: goal_001
task_id: t2_architecture
version: 2
status: active
created_at: 2026-04-18T10:14:00Z
---
```

---

## 13. Change Management Specification

This is one of the most important parts of the system.

### 13.1 Trigger

A change request is created when a user message linked to an active goal is classified as:
- scope change,
- requirement modification,
- architecture change,
- priority change.

### 13.2 Freeze protocol

Upon change request creation:

1. set change request state to `freeze_started`
2. all tasks for the goal in `created` or `queued` become `paused_pending_change_review`
3. emit `change.freeze_started`
4. collect running tasks snapshot
5. set change request state to `frozen`

SQL example:

```sql
UPDATE tasks
SET state = 'paused_pending_change_review',
    updated_at = :now
WHERE goal_id = :goal_id
  AND state IN ('created','queued');
```

### 13.3 Running task evaluation

Each currently running task must be analyzed and classified into one of:

- `continue_as_is`
- `continue_then_reuse`
- `stop_and_rollback`
- `fork_output`
- `cancel`

### 13.4 Impact analyzer inputs

- change request text
- current plan
- active tasks
- latest artifacts
- current baseline
- running task manifests
- open branches and worktrees

### 13.5 Impact analyzer outputs

Artifact:
`planning/change_request_{id}_analysis_v1.md`

Structured JSON:
`planning/change_request_{id}_decisions_v1.json`

Example decision object:

```json
{
  "task_id": "t3_repo_bootstrap",
  "decision": "stop_and_rollback",
  "reason": "repo bootstrap based on obsolete data model assumptions"
}
```

### 13.6 Stop and rollback protocol

For a task classified `stop_and_rollback`:
1. mark task `rollback_pending`
2. terminate worker if cooperative stop is supported
3. preserve current branch
4. reset branch or worktree to `base_commit_sha`
5. create rollback commit if needed
6. mark task `rolled_back` or `superseded`

### 13.7 Continue then reuse protocol

For `continue_then_reuse`:
- allow the task to finish,
- retain resulting artifacts,
- mark them as reusable but potentially superseded later,
- feed into replan.

### 13.8 Fork output protocol

For `fork_output`:
- keep the branch,
- label it as alternative,
- do not merge automatically,
- allow planner to compare options later.

### 13.9 Replan protocol

After impact analysis:
1. planner reads analysis and current valid artifacts
2. generate `execution_plan_v{n+1}.json`
3. insert new tasks
4. update superseded/cancelled tasks
5. resume eligible paused tasks

### 13.10 Resume protocol

Paused tasks become:
- `queued` if still valid,
- `cancelled` if invalid,
- `superseded` if replaced,
- `blocked` if awaiting approval.

---

## 14. Status and Progress Reporting

### 14.1 Important rule

Status must be computed from runtime facts, not improvised by an LLM.

### 14.2 Progress snapshot inputs

- tasks by state
- baselines
- artifacts produced
- latest events
- open approvals
- blocked tasks
- active change requests

### 14.3 Progress snapshot artifact

`status/progress_snapshot_v{n}.json`

Example:

```json
{
  "goal_id": "goal_001",
  "overall_progress_pct": 46,
  "completed_tasks": 4,
  "running_tasks": 2,
  "blocked_tasks": 1,
  "completed_artifacts": [
    "product/product_brief_v1.md",
    "architecture/system_design_v1.md"
  ],
  "active_risks": [
    "backup policy not yet defined",
    "password reset flow missing"
  ]
}
```

### 14.4 Suggested progress metrics

Report at least:
- tasks completed / total
- tasks blocked
- open approvals
- current change-request status
- latest baseline label
- estimated remaining work

Avoid presenting a single raw percentage without context.

---

## 15. GitHub Repository Bootstrap Capability

### 15.1 Purpose

This capability creates a private GitHub repository for a confirmed project.

Capability name:

```text
scm.github.repo.bootstrap
```

### 15.2 Why it is special

It is a high-risk capability because it causes an external side effect.

It should:
- require approval by default,
- use minimum-scoped credentials,
- be callable only by allowed orchestrators.

### 15.3 Recommended authentication

Preferred:
- GitHub App with minimal required permissions

Avoid:
- broad personal access tokens shared across all capabilities

### 15.4 Inputs

```json
{
  "project_id": "proj_001",
  "github_owner": "my-org",
  "repo_name": "freelance-hub",
  "private": true,
  "default_branch": "main",
  "initialize_readme": true
}
```

### 15.5 Execution steps

1. validate project state is `draft` or `activated`
2. ensure approval exists
3. call GitHub API to create repository
4. update project row with repo URL and owner/name
5. initialize local git repo if not already initialized
6. add remote
7. create initial commit
8. push default branch
9. create initial baseline

### 15.6 Output

- repository metadata in SQLite
- initial baseline
- event log entry
- project manifest updated

---

## 16. Approval System

### 16.1 Approval-required scopes

Initial V1 scopes:
- create GitHub repo
- buy domain
- create VPS
- apply infra changes
- send outbound email to external recipient
- destructive GitHub operations

### 16.2 Approval flow

1. scheduler detects task requiring approval
2. create row in `approvals`
3. mark task `blocked`
4. notify user through ingress channel
5. user approves or rejects
6. scheduler updates task state accordingly

### 16.3 Approval payloads

Approval payload should be explicit and structured.

Example:

```json
{
  "scope": "github.repo.create",
  "summary": "Create private GitHub repository freelance-hub under my-org",
  "parameters": {
    "owner": "my-org",
    "repo_name": "freelance-hub",
    "visibility": "private"
  }
}
```

---

## 17. Input Channels and Message Linking

### 17.1 V1 channels

- Discord
- CLI
- local API

### 17.2 Message linking rules

Every incoming message must be classified as one of:
- new goal,
- goal clarification,
- progress request,
- change request,
- approval response,
- generic note.

### 17.3 Goal linker

The goal linker associates a new message to:
- an existing active goal,
- or a new goal.

Inputs:
- thread reference
- recent goal activity
- explicit references in text
- user metadata

### 17.4 Message splitting

A single message can contain multiple intents.

Example:
- "How far are we on app 1?" → progress request
- "Also add client collaboration" → change request

These should be split into separate internal actions.

---

## 18. Configuration Files

### 18.1 Global app config example

`/workspace/runtime/config/app.yaml`

```yaml
runtime:
  db_path: /workspace/runtime/runtime.db
  workspace_root: /workspace
  poll_interval_seconds: 5
  lease_duration_seconds: 120
  heartbeat_interval_seconds: 30

git:
  default_branch: main
  auto_gc: false

planner:
  max_tasks_per_plan: 15

scheduler:
  max_running_tasks_per_goal: 3
  max_running_tasks_total: 8

approvals:
  default_required_for_high_risk: true
```

### 18.2 Model routing config example

`/workspace/runtime/config/model_routing.yaml`

```yaml
pools:
  strong_reasoning:
    provider: provider_a
    max_parallel_tasks: 2
    daily_token_budget: 1000000
  balanced:
    provider: provider_b
    max_parallel_tasks: 3
    daily_token_budget: 1500000
  cheap_fast:
    provider: provider_c
    max_parallel_tasks: 6
    daily_token_budget: 6000000
  extraction:
    provider: provider_d
    max_parallel_tasks: 8
    daily_token_budget: 8000000
```

---

## 19. Internal Python Module Layout

Recommended package structure:

```text
src/
  agentic_runtime/
    __init__.py
    config.py
    db.py
    models.py
    ids.py
    events.py
    logging.py
    time.py

    planner/
      planner.py
      prompts.py
      schemas.py

    scheduler/
      scheduler.py
      dependencies.py
      approvals.py
      quotas.py
      priorities.py

    workers/
      base.py
      registry.py
      loop.py
      leases.py
      execution.py

    capabilities/
      loader.py
      executor.py
      spec_product_refine.py
      design_system_webapp.py
      github_repo_bootstrap.py
      review_redteam.py

    gitops/
      repo.py
      worktree.py
      commits.py
      validation.py
      branches.py
      baselines.py

    change_management/
      detect.py
      freeze.py
      impact.py
      replan.py
      resume.py

    status/
      snapshots.py
      progress.py

    ingress/
      discord.py
      cli.py
      api.py
      linkers.py
      classifiers.py

    approvals/
      manager.py
      notifier.py
```

---

## 20. Required Python Classes / Interfaces

### 20.1 Task record model

```python
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TaskRecord:
    task_id: str
    goal_id: str
    project_id: str
    capability_name: str
    state: str
    priority: int
    branch_name: Optional[str]
    workspace_path: Optional[str]
    base_commit_sha: Optional[str]
    result_commit_sha: Optional[str]
    allowed_paths: List[str]
    input_artifacts: List[str]
    output_artifacts: List[str]
    model_pool_hint: Optional[str]
    max_tokens: Optional[int]
    max_cost_usd: Optional[float]
```

### 20.2 Capability executor interface

```python
from typing import Protocol

class CapabilityExecutor(Protocol):
    capability_name: str

    def execute(self, task: TaskRecord, context: "ExecutionContext") -> "ExecutionResult":
        ...
```

### 20.3 Execution context

```python
@dataclass
class ExecutionContext:
    db: object
    repo_path: str
    workspace_path: str
    project_id: str
    goal_id: str
    worker_id: str
    model_pool: str
    now_iso: str
```

### 20.4 Execution result

```python
@dataclass
class ExecutionResult:
    changed_files: list[str]
    output_artifacts: list[str]
    summary: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
```

---

## 21. Minimal Algorithms

### 21.1 Scheduler main loop pseudocode

```python
def scheduler_loop():
    while True:
        expire_stale_leases()
        release_ready_tasks()
        create_missing_approvals()
        process_approved_tasks()
        process_rejected_tasks()
        resume_post_change_tasks()
        generate_periodic_status_snapshots()
        sleep(5)
```

### 21.2 Worker main loop pseudocode

```python
def worker_loop(worker_id, supported_capabilities):
    register_or_update_worker(worker_id, supported_capabilities)

    while True:
        heartbeat_worker(worker_id)

        task = claim_next_task(worker_id, supported_capabilities)
        if task is None:
            sleep(5)
            continue

        try:
            run_task(worker_id, task)
        except Exception as exc:
            handle_worker_exception(task, exc)
```

### 21.3 Change manager loop pseudocode

```python
def process_change_request(change_request_id):
    freeze_future_tasks(change_request_id)
    snapshot_running_state(change_request_id)
    analyze_running_tasks(change_request_id)
    write_change_decisions(change_request_id)
    replan_goal(change_request_id)
    apply_resume_decisions(change_request_id)
```

---

## 22. Task Manifests

Each task workspace must contain a manifest file at:

```text
task_manifest.yaml
```

Example:

```yaml
task_id: t2_architecture
goal_id: goal_001
project_id: proj_001
capability_name: design.system.webapp

branch_name: goal_001/task_t2_architecture
workspace_path: /workspace/projects/proj_001/worktrees/task_t2_architecture
repo_path: /workspace/projects/proj_001/project_repo

base_commit_sha: 8cd01f2

input_artifacts:
  - product/product_brief_v1.md
  - product/feature_list_v1.md

expected_output_artifacts:
  - architecture/system_design_v1.md
  - architecture/data_model_v1.md

allowed_paths:
  - architecture/**
  - docs/**

forbidden_paths:
  - app/**
  - infra/**

model_pool: strong_reasoning
max_tokens: 24000
max_cost_usd: 1.50
```

This manifest is both:
- a worker execution contract,
- and a debugging object.

---

## 23. Testing Strategy

### 23.1 Test layers

1. unit tests
2. SQLite integration tests
3. Git integration tests
4. planner contract tests
5. change-management tests
6. end-to-end scenario tests

### 23.2 Critical tests

#### A. Task claiming
- two workers race for same task
- only one should claim it

#### B. Allowed path enforcement
- capability tries to modify forbidden file
- task must fail

#### C. Freeze logic
- change request arrives while tasks queued/running
- queued tasks frozen
- running tasks classified

#### D. Rollback logic
- task worktree reset to base commit
- branch preserved for audit

#### E. Progress snapshot correctness
- status reflects actual task states and artifacts

#### F. Approval flow
- risky task blocked before execution
- approval resumes it

---

## 24. Security and Safety Rules

### 24.1 Never allow unrestricted filesystem writes

Every task must declare `allowed_paths`.

### 24.2 Never allow unrestricted external effects

All external actions must be:
- capability-gated,
- approval-aware,
- event-logged.

### 24.3 Keep credentials out of Git repos

Secrets must never be committed.

### 24.4 Separate GitHub capabilities

At minimum:
- `scm.github.repo.bootstrap`
- `scm.github.repo.write`
- `scm.github.admin`

Do not combine them into one omnipotent capability.

### 24.5 Keep LLM outputs untrusted until validated

Generated artifacts may be useful, but they are not automatically authoritative.

---

## 25. MVP Scope Recommendation

### 25.1 Build first

1. SQLite schema
2. project creation
3. goal creation
4. planner stub
5. task scheduler
6. worker loop
7. Git worktree management
8. one or two low-risk capabilities
9. change freeze + impact classification
10. status snapshot generator

### 25.2 Capabilities for MVP

Recommended first capabilities:
- `spec.product.refine`
- `design.system.webapp`
- `review.redteam`
- `status.progress.snapshot`
- `scm.github.repo.bootstrap` (after approval flow exists)

### 25.3 Do not build first

Avoid building these first:
- domain purchase
- VPS deployment
- destructive infra changes
- email sending
- advanced memory system
- distributed runtime

---

## 26. Example End-to-End Scenario

### 26.1 User starts project

User says:
"I have an app idea for freelancers. I want it deployed on an OVH VPS with a domain."

System:
1. creates project in `draft`
2. creates goal
3. stores messages
4. writes normalized intent
5. planner writes execution plan
6. scheduler creates tasks
7. workers execute spec and architecture tasks
8. artifacts committed to Git
9. progress snapshots generated

### 26.2 User asks for another unrelated task

System:
1. creates a second goal
2. isolates runtime states
3. scheduler shares model pools according to quotas

### 26.3 User changes first app scope during execution

User says:
"Add a client collaboration area, and tell me current progress."

System:
1. links message to goal 1
2. splits message into progress request + change request
3. computes status snapshot from SQLite facts
4. freezes future tasks
5. analyzes running tasks
6. rolls back invalid branches if needed
7. replans
8. resumes valid work
9. reports factual status

---

## 27. Build Order

Recommended implementation order:

### Phase 1 — core runtime
- SQLite schema
- migrations
- Python DB layer
- IDs/events utilities

### Phase 2 — Git layer
- init repo
- create worktree
- validate changed paths
- commit helper
- baseline creation

### Phase 3 — scheduler and workers
- claim protocol
- worker loop
- task state transitions
- dependency release

### Phase 4 — planner and artifacts
- intent normalization
- planner prompt/output schema
- plan ingestion into tasks
- artifact registration

### Phase 5 — change management
- freeze protocol
- impact analyzer
- task classification
- replan and resume

### Phase 6 — approvals and GitHub bootstrap
- approval table and flow
- GitHub repo bootstrap capability
- initial project activation

### Phase 7 — observability and polish
- status snapshots
- CLI tools
- logs
- scenario tests

---

## 28. Final Engineering Principles

1. **Prefer explicit files over hidden memory.**
2. **Prefer deterministic rules over LLM improvisation.**
3. **Prefer branch-per-task over freeform shared editing.**
4. **Prefer partial replan over full restart.**
5. **Prefer auditable side effects over silent automation.**
6. **Prefer simple local architecture first, then distribute later.**
7. **Treat Git as the work history, SQLite as the runtime brain.**

---

## 29. Final Summary

This system should be implemented as:

- a **SQLite-driven runtime orchestrator**,
- a **Git-backed artifact production pipeline**,
- a **capability-based worker system**,
- a **change-aware execution engine**,
- a **carefully permissioned side-effect framework**.

The system is not an AI company simulator.

It is a structured, inspectable, rollback-friendly execution engine for AI-assisted work.

---

# 30. Multi-LLM Provider Configuration (Simple V1)

## 30.1 Design Philosophy

The multi-provider system is intentionally **kept simple for V1**.

Key principles:

- Model selection is **configuration-driven (YAML)**.
- Providers are **declared in config**, not hardcoded.
- No complex plugin system for now.
- No advanced quota routing yet.
- Workers do not decide which model to use.
- Scheduler (or pre-execution step) assigns the model.

> The system chooses *what intelligence to use*, not the worker.

---

## 30.2 Configuration File

All provider and model configuration is stored in a single file:

```
/workspace/runtime/config/models.yaml
```

This file contains two sections:

- `providers`
- `models`

---

## 30.3 Providers Definition

Each provider defines:
- endpoint
- authentication method

Example:

```yaml
providers:
  - id: openai
    endpoint: https://api.openai.com/v1
    auth:
      type: api_key
      env_var: OPENAI_API_KEY

  - id: anthropic
    endpoint: https://api.anthropic.com
    auth:
      type: api_key
      env_var: ANTHROPIC_API_KEY

  - id: local
    endpoint: http://localhost:8000/v1
    auth:
      type: none
```

### Authentication rules

Supported types:

- `api_key` → read from environment variable
- `none` → no authentication
- (future) `oauth`

**IMPORTANT:**
API keys must never be stored in YAML or Git.

---

## 30.4 Models Definition

Each model references a provider and defines:

- qualitative scores
- capabilities
- enable/disable flag

Example:

```yaml
models:
  - id: openai_gpt_code
    provider: openai
    model_name: gpt-code

    scores:
      coding: 9
      reasoning: 7
      extraction: 6
      speed: 6
      cost_efficiency: 4

    capabilities:
      support_vision: true
      support_tools: true
      support_json_schema: true
      support_streaming: true

    enabled: true

  - id: anthropic_reasoning
    provider: anthropic
    model_name: claude-reasoning

    scores:
      coding: 7
      reasoning: 9
      extraction: 8
      speed: 5
      cost_efficiency: 4

    capabilities:
      support_vision: true
      support_tools: false
      support_json_schema: true
      support_streaming: true

    enabled: true

  - id: local_fast
    provider: local
    model_name: qwen-fast

    scores:
      coding: 6
      reasoning: 5
      extraction: 7
      speed: 9
      cost_efficiency: 10

    capabilities:
      support_vision: false
      support_tools: false
      support_json_schema: false
      support_streaming: true

    enabled: true
```

---

## 30.5 Model Selection Process

Model selection is performed **before task execution**.

### Step-by-step

1. Task is created
2. Scheduler reads capability definition
3. Scheduler loads `models.yaml`
4. Models are filtered:
   - disabled models removed
   - models not matching requirements removed
5. Remaining models are scored
6. Best model is selected
7. Selection is written into the task

---

## 30.6 Capability Requirements

Each capability defines its model requirements.

Example:

```yaml
name: code.generate.backend

model_requirements:
  min_scores:
    coding: 8

  required_capabilities:
    support_tools: true

  preferred_capabilities:
    support_json_schema: true

  ranking_formula:
    coding: 5
    reasoning: 2
    speed: 1
    cost_efficiency: 1
```

---

## 30.7 Selection Algorithm (V1)

### Filtering

A model is rejected if:

- `enabled = false`
- missing required capabilities
- below minimum score

### Scoring

```python
score = 0

for skill, weight in ranking_formula.items():
    score += model.scores.get(skill, 0) * weight

for cap, expected in preferred_capabilities.items():
    if model.capabilities.get(cap) == expected:
        score += 2
```

### Selection

- highest score wins
- no fallback chain in V1 (can be added later)

---

## 30.8 Task Enrichment

After selection, the task is updated with:

```json
{
  "selected_model_id": "openai_gpt_code",
  "selected_provider": "openai",
  "selected_model_name": "gpt-code",
  "model_selection_score": 87
}
```

---

## 30.9 Worker Execution

Workers do NOT perform model selection.

They simply:

1. read selected model from task
2. resolve provider config
3. resolve authentication
4. call the endpoint

---

## 30.10 Provider Resolution (Runtime)

Example Python logic:

```python
def resolve_provider(model, providers):
    return providers[model["provider"]]

def resolve_auth(auth_config):
    if auth_config["type"] == "api_key":
        import os
        return os.getenv(auth_config["env_var"])
    return None
```

Execution example:

```python
provider = resolve_provider(model, providers)
api_key = resolve_auth(provider["auth"])

call_llm(
    endpoint=provider["endpoint"],
    api_key=api_key,
    model_name=model["model_name"],
    prompt=...
)
```

---

## 30.11 Future Extensions (Not in V1)

The system is designed to evolve without breaking this model.

Planned extensions:

- quota-aware filtering
- fallback chains
- provider health scoring
- dynamic pricing updates
- multi-endpoint providers
- retry strategies
- cost-aware routing

---

## 30.12 Summary

V1 multi-provider system:

- single YAML file
- simple filtering + scoring
- scheduler-driven selection
- worker execution only
- no quota logic yet

This ensures:
- simplicity
- readability
- fast implementation
- clean evolution path
