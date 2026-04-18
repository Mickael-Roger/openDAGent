<p align="center">
  <img src="assets/logo.png" alt="openDAGent logo" width="180">
</p>

# openDAGent

openDAGent is a local-first agentic execution framework for long-running AI-assisted work.

It is designed to turn user requests into explicit goals, break those goals into constrained tasks, execute them inside isolated Git workspaces, and track runtime orchestration in SQLite.

The project treats AI as part of an execution engine, not as a roleplay system. Runtime state is explicit, inspectable, and recoverable.

## Project Description

This project aims to build a capability-driven orchestration runtime with these core properties:
- SQLite is the runtime control plane.
- Git is the source of truth for project work and artifact history.
- Tasks run through bounded capabilities instead of unrestricted filesystem or API access.
- Artifacts are first-class runtime objects.
- Task readiness is driven by artifact availability and status, not direct task-to-task dependency edges.
- Change requests can freeze work, trigger impact analysis, replan, and resume execution.

At a high level, the system manages:
- projects
- goals
- tasks
- capabilities
- artifacts
- baselines
- change requests
- workers

## Why

Most agent systems become hard to trust once work lasts longer than a single prompt or a single interaction. They often hide state in prompts, blur planning and execution, and make rollback or inspection difficult.

This project exists to solve that by making orchestration explicit.

Key motivations:
- Long-running work needs durable runtime state.
- Multi-step execution needs deterministic scheduling rules.
- Generated outputs need versioned history and rollback.
- User changes during execution need controlled freeze, analysis, and replan flows.
- External side effects need capability gates and approvals.
- Operators need factual status from runtime data, not improvised summaries.

In short: SQLite is the runtime brain, Git is the work history, and artifacts are the dependency boundary.

## How

The runtime is built around a small set of rules.

### Core Architecture

- `runtime/runtime.db` stores runtime truth.
- project repositories store generated artifacts and change history.
- workers execute tasks in isolated worktrees.
- capabilities define what a task is allowed to read, write, and do.
- artifacts connect the output of one task to the readiness of another.

### Artifact-Driven Orchestration

The current orchestration model is artifact-first.

Each task declares:
- `required_artifacts`
- `produced_artifacts`

A task is executable only when all of its required artifacts exist with the required status.

Artifacts can be:
- file-based outputs through `file_path`
- structured runtime values through `value_json`

Examples of structured artifacts:
- a selected domain name
- an external resource ID
- an approval decision
- planner output metadata

### Runtime Flow

The intended runtime flow is:
1. an input arrives through CLI, API, or another ingress channel
2. the input is normalized into a goal
3. a planner generates task definitions
4. tasks are stored in SQLite with required and produced artifact declarations
5. the scheduler queues tasks whose required artifacts are available
6. a worker claims a queued task
7. the worker executes inside a Git worktree
8. task outputs are registered as artifacts
9. newly available artifacts unlock downstream tasks
10. status snapshots are generated from runtime facts

## Current Status

The repository currently contains the Phase 1 foundation plus the first artifact-driven scheduling slice.

Implemented today:
- project packaging and repository bootstrap
- runtime configuration files under `runtime/config/`
- SQLite schema bootstrap with required PRAGMAs
- shared runtime models
- artifact resolver and artifact registration helpers
- artifact-based task readiness checks
- scheduler helper for queueing ready tasks
- unit and integration-style tests for the current runtime slice

Planned next:
- project and goal creation services
- Git repository and worktree helpers
- worker claiming and execution loop
- planner ingestion into task and artifact declaration rows
- change management and approval flows

## Repository Layout

```text
.
├── PROJECT.md
├── README.md
├── AGENTS.md
├── docs/
│   └── implementation_action_plan.md
├── runtime/
│   └── config/
├── src/
│   └── agentic_runtime/
└── tests/
```

Important files:
- `PROJECT.md`: full implementation specification
- `docs/implementation_action_plan.md`: phased delivery plan
- `src/agentic_runtime/db.py`: SQLite schema/bootstrap
- `src/agentic_runtime/artifacts.py`: artifact resolution and registration logic
- `src/agentic_runtime/scheduler.py`: readiness-based queueing

## Quickstart

### Requirements

- Python 3.11+
- Git

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd opendagent
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install the package

```bash
python3 -m pip install -e .
```

### 4. Initialize the runtime database

This creates `runtime/runtime.db` and applies the SQLite schema and PRAGMA configuration.

```bash
python3 -c "from agentic_runtime import initialize_database; initialize_database('runtime/runtime.db').close()"
```

### 5. Verify the installation

```bash
python3 -c "from agentic_runtime import initialize_database, resolve_latest_artifact; print('openDAGent installed')"
python3 -m unittest discover -s tests -v
```

### 6. Configure the runtime

Configuration lives under `runtime/config/`:
- `app.yaml`
- `model_routing.yaml`
- `models.yaml`
- `capabilities/*.yaml`

Adjust these files to match your local paths, model providers, and capability settings before running a fuller deployment.

### What runs today

The current repository provides a runnable bootstrap runtime layer:
- package installation
- SQLite runtime initialization
- artifact registration and resolution
- artifact-based task readiness checks
- scheduler-ready queueing primitives

You can initialize and exercise the current runtime API from Python immediately.

Example:

```bash
python3 - <<'PY'
from agentic_runtime import initialize_database, queue_ready_tasks

connection = initialize_database('runtime/runtime.db')
print('database initialized')
print('ready tasks:', queue_ready_tasks(connection))
connection.close()
PY
```

### Current limitation

The project is still in the bootstrap phase. A full end-user service runner is not implemented yet.

That means the following are not available yet:
- a production CLI for creating projects and goals
- a long-running scheduler process
- a worker daemon that executes real capabilities end to end
- planner-driven task ingestion from user requests

Today, the installable and runnable part of the project is the core runtime foundation that those services will build on.

## Design Principles

- Prefer explicit files over hidden memory.
- Prefer deterministic rules over improvised agent behavior.
- Prefer auditable side effects over silent automation.
- Prefer artifact-driven orchestration over task-edge coupling.
- Prefer simple local architecture first, then distribute later.

## See Also

- `PROJECT.md`
- `docs/implementation_action_plan.md`
- `AGENTS.md`
