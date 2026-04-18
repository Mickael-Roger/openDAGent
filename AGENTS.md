# Project-Specific Agent Guidelines

## Overview
- Purpose: build `openDAGent`, a local-first agentic execution system driven by SQLite runtime state and Git-backed project artifacts.
- Primary spec: `PROJECT.md` is the implementation source of truth.
- Initial implementation target: Phase 1 core runtime and the first bootstrap utilities.

## Rules
- Keep runtime state in SQLite and project work state in Git, matching `PROJECT.md`.
- Prefer small, composable Python modules under `src/agentic_runtime/`.
- Use dataclasses and explicit types for runtime records.
- Keep YAML configuration under `runtime/config/` and load it safely.
- Follow the phase ordering from `PROJECT.md`; do not jump to high-risk external capabilities before the approval flow exists.
- Model orchestration dependencies through artifacts, not direct task-to-task dependency edges.

## Current Build Focus
- Phase 1: schema bootstrap, DB helpers, config loading, IDs, time helpers, shared models, and artifact-driven task readiness.
- Phase 2 next: Git repository/worktree helpers and path validation.

## Testing
- Add unit tests for every new core utility module.
- Add SQLite integration tests for schema bootstrap and PRAGMA configuration.

## See Also
- `PROJECT.md`
- `docs/implementation_action_plan.md`
