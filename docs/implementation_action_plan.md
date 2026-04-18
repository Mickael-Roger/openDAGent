# Implementation Action Plan

## Objective

Build the framework described in `PROJECT.md` as a local-first execution engine where:
- SQLite is the runtime control plane.
- Git is the artifact and branch history layer.
- Workers execute capability-bounded tasks inside isolated worktrees.
- change requests can freeze, analyze, replan, and resume active work.

## Delivery Principles

- Ship in thin vertical slices, but preserve the architecture in the spec.
- Prefer deterministic orchestration over hidden prompt state.
- Keep external side effects behind explicit capabilities and approvals.
- Make every important state transition inspectable through SQLite rows, events, and artifacts.
- Trigger tasks from artifact readiness, not direct task-to-task edges.

## Work Breakdown Structure

### Phase 1: Core Runtime Foundation

Goal: establish the shared primitives needed by every later subsystem.

Tasks:
- Create the Python package skeleton under `src/agentic_runtime/`.
- Add packaging metadata and development test configuration.
- Add safe YAML config loading for `runtime/config/*.yaml`.
- Implement shared time and ID helpers.
- Implement runtime record dataclasses from the spec.
- Implement SQLite bootstrap with required PRAGMAs.
- Create the full V1 schema from section 5 of `PROJECT.md`.
- Model task readiness through required and produced artifacts instead of task dependency edges.
- Add tests that prove config loading and schema initialization work.

Deliverables:
- `pyproject.toml`
- `src/agentic_runtime/config.py`
- `src/agentic_runtime/db.py`
- `src/agentic_runtime/ids.py`
- `src/agentic_runtime/time.py`
- `src/agentic_runtime/models.py`
- `src/agentic_runtime/artifacts.py`
- `src/agentic_runtime/scheduler.py`
- `tests/test_config.py`
- `tests/test_db.py`
- `tests/test_artifacts.py`
- `tests/test_core_utils.py`

### Phase 2: Git Operations Layer

Goal: support project repositories, task branches, isolated worktrees, and safe commits.

Tasks:
- Implement repository initialization helpers.
- Implement worktree create/attach/detach helpers.
- Implement branch naming and commit message helpers.
- Implement allowed-path validation against task contracts.
- Implement baseline creation and baseline persistence helpers.
- Add integration tests covering worktree setup and changed-path validation.

Deliverables:
- `src/agentic_runtime/gitops/repo.py`
- `src/agentic_runtime/gitops/worktree.py`
- `src/agentic_runtime/gitops/validation.py`
- `src/agentic_runtime/gitops/commits.py`
- `src/agentic_runtime/gitops/baselines.py`

### Phase 3: Scheduler and Worker Runtime

Goal: make queued tasks executable with deterministic claiming and state transitions.

Tasks:
- Implement worker registration and heartbeats.
- Implement atomic claim protocol using SQLite transactions.
- Implement task state transition helpers.
- Implement dependency release logic.
- Implement worker loop skeleton and execution orchestration.
- Add retry and stale-lease recovery behavior.
- Add concurrency tests for competing task claims.
- Ensure queueing decisions are based on required artifact availability and status.

Deliverables:
- `src/agentic_runtime/workers/base.py`
- `src/agentic_runtime/workers/registry.py`
- `src/agentic_runtime/workers/leases.py`
- `src/agentic_runtime/workers/loop.py`
- `src/agentic_runtime/workers/execution.py`
- `src/agentic_runtime/scheduler/scheduler.py`
- `src/agentic_runtime/scheduler/dependencies.py`

### Phase 4: Planning and Artifact Management

Goal: turn normalized goal inputs into bounded task graphs and registered artifacts.

Tasks:
- Implement goal and message normalization models.
- Implement planner input assembly.
- Implement planner output schema and plan artifact writing.
- Ingest plans into task rows plus required/produced artifact declaration rows.
- Ingest plans into task and artifact declaration rows.
- Implement artifact registration with versioning and structured value support.
- Add contract tests for planner output shape.

Deliverables:
- `src/agentic_runtime/planner/planner.py`
- `src/agentic_runtime/planner/schemas.py`
- `src/agentic_runtime/capabilities/loader.py`
- `src/agentic_runtime/events.py`

### Phase 5: Change Management

Goal: support in-flight scope changes without full resets.

Tasks:
- Detect change requests from linked goal messages.
- Freeze `created` and `queued` tasks.
- Snapshot running task state.
- Generate impact analysis artifacts and task decisions.
- Replan using still-valid artifacts.
- Resume, cancel, or supersede paused tasks.
- Add freeze and rollback integration tests.

Deliverables:
- `src/agentic_runtime/change_management/detect.py`
- `src/agentic_runtime/change_management/freeze.py`
- `src/agentic_runtime/change_management/impact.py`
- `src/agentic_runtime/change_management/replan.py`
- `src/agentic_runtime/change_management/resume.py`

### Phase 6: Approvals and External Capabilities

Goal: safely introduce high-risk external effects.

Tasks:
- Implement approval creation and response handling.
- Block approval-gated tasks before queueing.
- Implement GitHub repository bootstrap capability.
- Persist external side effects in events and project metadata.
- Add approval flow tests.

Deliverables:
- `src/agentic_runtime/approvals/manager.py`
- `src/agentic_runtime/scheduler/approvals.py`
- `src/agentic_runtime/capabilities/github_repo_bootstrap.py`

### Phase 7: Status, CLI, and End-to-End Scenarios

Goal: make the system inspectable and operable.

Tasks:
- Implement factual progress snapshot generation from runtime data.
- Add CLI entry points for project, goal, task, and approval administration.
- Add structured logging and event reporting.
- Add end-to-end scenario tests matching section 26 of the spec.

Deliverables:
- `src/agentic_runtime/status/snapshots.py`
- `src/agentic_runtime/status/progress.py`
- `src/agentic_runtime/ingress/cli.py`
- scenario tests under `tests/`

## Cross-Cutting Task List

These tasks apply across all phases:
- Keep schema, state names, and filesystem conventions aligned with `PROJECT.md`.
- Preserve artifact-first orchestration as the runtime dependency model.
- Add or update tests with every feature slice.
- Add event emission for important transitions.
- Keep external credentials out of repo content and example configs.
- Record project-specific implementation decisions in `AGENTS.md`.

## MVP Execution Order

1. Finish Phase 1 foundation.
2. Implement minimal Git operations from Phase 2.
3. Implement task claiming and worker execution skeleton from Phase 3.
4. Add a planner stub and artifact registration from Phase 4.
5. Add freeze and impact classification from Phase 5.
6. Add status snapshots.
7. Add approvals, then GitHub bootstrap.

## Initial Implementation Slice Started

This repository now begins with:
- the Phase 1 package and config skeleton,
- SQLite schema bootstrap,
- artifact resolver and artifact-driven task readiness helpers,
- shared runtime dataclasses and helpers,
- tests for config, database initialization, artifact resolution, and scheduler readiness.

## Immediate Next Tasks

1. Add missing runtime repositories/services around the schema.
2. Implement project and goal creation APIs against SQLite.
3. Add Git repository bootstrap helpers.
4. Extend scheduler/worker flow from ready queueing into atomic claim and execution transitions.
