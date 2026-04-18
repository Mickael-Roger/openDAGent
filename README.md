<p align="center">
  <img src="assets/logo.png" alt="openDAGent logo" width="180">
</p>

<h1 align="center">openDAGent</h1>

<p align="center">
  <strong>Artifact-driven orchestration for long-running AI work.</strong><br>
  SQLite is the brain. Git is the history. Artifacts are the dependency boundary.
</p>

<p align="center">
  <a href="#why-not-an-org-chart">The Idea</a> ·
  <a href="#how-it-works">How It Works</a> ·
  <a href="#quickstart">Quickstart</a> ·
  <a href="#current-status">Status</a>
</p>

---

## The Problem With "AI Companies"

Most multi-agent frameworks today build **AI org charts**: a manager agent delegates to worker agents, who report back up the chain. There are departments, roles, and reporting lines — modeled straight from how human teams operate.

That design made sense for humans. It exists because of **human constraints**:
- People have limited working memory → need handoff documents and status meetings
- People need sleep and context-switching costs → async communication protocols
- People have specialization gaps → role definitions and escalation paths
- People can't be trusted blindly → approval hierarchies and sign-offs

**LLMs don't share these constraints.** They also have failure modes humans don't:
- They hallucinate under ambiguity, not under fatigue
- They lose coherence over long horizons, not over org boundaries
- They have no communication overhead between "agents" if state is explicit
- Their cost is per-token, not per-hour

Copying human org structure into an agentic system imports all the bureaucratic overhead **designed to compensate for human limits** — without getting the benefits, and while ignoring the actual LLM failure modes.

---

## A Different Model

openDAGent doesn't model an organization. It models a **dependency graph**.

Work is broken into tasks. Each task declares:
- what artifacts it **needs** to run
- what artifacts it **produces** when done

A task becomes executable the moment its required artifacts exist with the right status. Nothing else gates it. No manager agent decides. No delegation chain. Just data readiness.

```
         ┌─────────────────┐
         │   product.brief │  ← artifact (file or structured value)
         └────────┬────────┘
                  │ required by
         ┌────────▼────────┐
         │  Design system  │  ← task (queued automatically when artifact is ready)
         └────────┬────────┘
                  │ produces
         ┌────────▼────────┐
         │  design.spec    │  ← artifact
         └─────────────────┘
```

This maps to how LLMs actually work: give them bounded, well-defined inputs → get reliable, auditable outputs. The DAG is the contract. Artifacts are the interface.

No prompt-chain spaghetti. No agent that "decides" whether to delegate. No hidden state living inside a context window.

---

## How It Works

### Core Architecture

| Layer | Role |
|---|---|
| **SQLite** | Runtime control plane — all state is here, explicit and queryable |
| **Git** | Source of truth for project work, artifact history, and rollback |
| **Capabilities** | What a task is allowed to read, write, and call — no unrestricted access |
| **Artifacts** | Versioned runtime objects connecting task outputs to task readiness |
| **Workers** | Isolated execution inside Git worktrees |

### Runtime Flow

```
Input (CLI / API / channel)
        │
        ▼
   Goal created
        │
        ▼
   Planner → Task definitions + artifact declarations → SQLite
        │
        ▼
   Scheduler checks artifact availability
        │
        ▼
   Ready tasks queued → Worker claims task
        │
        ▼
   Execution inside isolated Git worktree
        │
        ▼
   Outputs registered as artifacts → downstream tasks unlocked
```

### Artifact Types

Artifacts can be file-based or structured runtime values:

```python
# File artifact
{ "artifact_key": "product.brief", "file_path": "docs/brief_v1.md" }

# Structured artifact
{ "artifact_key": "domain.selected", "value_json": {"domain": "openDAGent.io"} }
{ "artifact_key": "approval.deploy",  "value_json": {"approved": true, "by": "user"} }
```

Structured artifacts mean approvals, decisions, and planner outputs are all first-class runtime objects — not side effects buried in conversation history.

### Change Requests

When requirements change mid-execution, openDAGent handles it with a controlled flow:
1. Active work freezes
2. Impact analysis runs against the artifact graph
3. Affected tasks are replanned
4. Execution resumes from a clean checkpoint

No re-running everything from scratch. No silent divergence.

---

## Quickstart

### Requirements

- Python 3.11+
- Git

### Install

```bash
pip install openDAGent
```

### Configure

```bash
mkdir -p /etc/opendagent
openDAGent --init-config /etc/opendagent/config.yaml
```

Edit the key values:

```yaml
runtime:
  workdir: /var/lib/opendagent
  db_path: /var/lib/opendagent/runtime/runtime.db

server:
  enabled: true
  host: 127.0.0.1
  port: 8080

llm:
  # your model provider configuration
```

### Start

```bash
openDAGent --config /etc/opendagent/config.yaml
```

### Open the Web UI

```
http://127.0.0.1:8080/
```

The dashboard shows all projects, task DAGs, runtime states, task details, and artifact relationships.

### Runtime Flags

```bash
openDAGent --config path/to/config.yaml           # standard start
openDAGent --config ... --host 0.0.0.0 --port 9090  # override bind
openDAGent --config ... --no-web                  # headless mode
openDAGent --config ... --init-db-only            # bootstrap db and exit
openDAGent --init-config path/to/output.yaml      # write default config template
```

---

## Current Status

The Phase 1 foundation and artifact-driven scheduling slice are complete.

**Done**
- Package, CLI, and repository bootstrap
- SQLite schema with required PRAGMAs
- Shared runtime models
- Artifact resolver and registration
- Artifact-based task readiness and scheduler
- Web UI: dashboard, project DAG view, task detail
- GitHub Actions workflow for PyPI publishing
- Unit and integration tests

**In Progress**
- Project and goal creation services
- Git repository and worktree helpers
- Worker claiming and execution loop
- Planner output ingestion into task and artifact rows
- Change management and approval flows

---

## Design Principles

- **Explicit over hidden** — runtime state lives in SQLite, not in a prompt.
- **Data-driven over authority-driven** — artifact availability gates tasks, not agent delegation.
- **Bounded execution** — capabilities define what a task can touch; nothing else is reachable.
- **Auditable by default** — artifact versions and task history are facts, not summaries.
- **Local-first** — runs on a single machine; distribute later if needed.

---

## Publishing

Releases publish to PyPI via GitHub Actions on version tags:

```bash
git tag v0.1.1
git push origin v0.1.1
```

The workflow smoke-tests the install before publishing.

---

## See Also

- [`PROJECT.md`](PROJECT.md) — full implementation specification
- [`docs/implementation_action_plan.md`](docs/implementation_action_plan.md) — phased delivery plan
- [`AGENTS.md`](AGENTS.md) — agent and worker contracts
