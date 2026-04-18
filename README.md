<p align="center">
  <img src="assets/logo.png" alt="openDAGent logo" width="180">
</p>

# openDAGent

openDAGent is a local-first agentic execution system for long-running AI-assisted work.

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

### 1. Install the package

```bash
pip install openDAGent
```

### 2. Create a configuration file

The intended operator flow is based on one YAML file that contains runtime settings, web server settings, input channels, and LLM provider/model configuration.

Example:

```bash
mkdir -p /etc/opendagent
openDAGent --init-config /etc/opendagent/config.yaml
```

The configuration file now includes sections such as:
- `runtime`
- `server`
- `inputs`
- `llm`
- `git`
- `planner`
- `scheduler`
- `approvals`

In practice, this file is where you will configure:
- Discord or email ingress
- local API bindings
- LLM providers and models
- runtime storage directories
- bind address and port for the web UI

### 3. Edit the configuration

Important values to edit first:
- `runtime.workdir`
- `runtime.db_path`
- `server.enabled`
- `server.host`
- `server.port`
- `inputs.*`
- `llm.*`

### 4. Start openDAGent

```bash
openDAGent --config /etc/opendagent/config.yaml
```

By default, the command will:
- load the YAML configuration file
- create the runtime working directory if needed
- initialize the SQLite database if it does not exist
- start the web interface if `server.enabled` is true

### 5. Useful startup options

`openDAGent` supports operator-friendly overrides at startup:

```bash
openDAGent --config /etc/opendagent/config.yaml --host 0.0.0.0 --port 8080
openDAGent --config /etc/opendagent/config.yaml --no-web
openDAGent --config /etc/opendagent/config.yaml --workdir /var/lib/opendagent
openDAGent --config /etc/opendagent/config.yaml --db /var/lib/opendagent/runtime/runtime.db
openDAGent --config /etc/opendagent/config.yaml --init-db-only
```

Available runtime flags:
- `--init-config`: write the bundled default config template to a path and exit
- `--config`: path to the YAML configuration file
- `--host`: override the web bind host
- `--port`: override the web bind port
- `--web`: force-enable the web interface
- `--no-web`: disable the web interface
- `--workdir`: override the runtime working directory
- `--db`: override the SQLite database path
- `--init-db-only`: initialize the database and exit

### 6. Open the web interface

```bash
http://127.0.0.1:8080/
```

The web interface is intended to show:
- all projects
- task DAGs
- runtime states
- task details
- artifact relationships

## Packaging And Publishing

This repository now includes a GitHub Actions workflow for PyPI publishing:
- `.github/workflows/publish-pypi.yml`

It is designed to publish on version tags such as:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The release workflow also performs a smoke test by installing the package, generating a default config, and running `openDAGent --init-db-only` before publishing.

### Current Status

The project is moving toward the user experience above.

Already aligned with that direction:
- package metadata for `openDAGent`
- top-level `openDAGent` CLI entrypoint
- one primary YAML configuration file
- startup flags for config, bind host, port, workdir, database path, and web on/off
- bundled web UI for projects, task DAGs, statuses, and task details
- GitHub Actions workflow for PyPI publishing

Not finished yet:
- full production ingress services for Discord and email
- full scheduler/worker runtime loops
- end-to-end goal creation from external inputs
- complete install docs for systemd/reverse proxy deployment

## Current Limitation

Today, `openDAGent` already has the shape of installable software, but the orchestration engine is still only partially implemented under the hood. The install/start UX is being aligned first so the product can grow into a real deployable server application instead of remaining a developer-only runtime library.

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
