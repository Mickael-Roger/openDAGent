from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from .. import llm as llm_mod
from .. import tools as tools_mod
from ..exceptions import TaskBlocked
from ..ids import new_id
from ..time import utc_now_iso
from ..tracing import Tracer

logger = logging.getLogger(__name__)

_MAX_ITERATIONS_DEFAULT = 20

# ── Artifact contract injected into the system prompt ─────────────────────────

_ARTIFACT_CONTRACT_HEADER = """

## REQUIRED — Artifact Production Contract

This task has declared the artifacts it MUST produce. Downstream tasks in the
project DAG are BLOCKED until every listed artifact exists with the required
status. **The task is NOT complete until all artifacts below have been written.**
"""

_ARTIFACT_REQUIRED_HEADER = """

## Available Input Artifacts

This task depends on artifacts produced by upstream tasks. Use `read_artifact`
with the exact key to retrieve them:
"""


class BaseCapability:
    """
    Generic agentic execution loop.

    Instances are constructed from a capability definition (loaded from YAML or DB).
    The loop:
      1. Resolves native Python tools + MCP tools.
      2. Calls the LLM with the accumulated messages and tool schemas.
      3. If the LLM returns tool calls, dispatches each and appends results.
      4. Repeats until the LLM returns a final text response or max_iterations is hit.
    """

    name: str = ""
    description: str = ""
    risk_level: str = "low"
    system_prompt: str = ""
    llm_features: list[str] = []
    availability_conditions: list[str] = []
    preferred_score: str = ""    # score dimension used to pick the best model
    tools: list[str] = []        # native tool names
    mcp_servers: list[str] = []  # MCP server IDs (from config)
    max_iterations: int = _MAX_ITERATIONS_DEFAULT

    # ── Public entry point ────────────────────────────────────────────────────

    def execute(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        llm_config: dict[str, Any],
        mcp_config: dict[str, Any] | None = None,
        app_config: dict[str, Any] | None = None,
    ) -> str | None:
        """Run the capability loop.  Returns the LLM's final text content
        (if any) so callers can decide whether to auto-post it."""
        # Ensure the task has a workspace directory
        self._ensure_workspace(conn, task)

        native_tools = self._resolve_native_tools()
        mcp_schemas: list[dict[str, Any]] = []
        mcp_dispatch: dict[str, Any] = {}

        tracer = Tracer.current()
        with tracer.trace(
            task["task_id"],
            goal_id=task.get("goal_id"),
            project_id=task.get("project_id"),
            capability=self.name,
            attributes={
                "capability.max_iterations": self.max_iterations,
                "capability.risk_level": self.risk_level,
                "task.title": task.get("title", ""),
            },
        ) as trace:
            if self.mcp_servers:
                from ..mcp.manager import MCPManager
                with MCPManager(self.mcp_servers, mcp_config or {}) as mgr:
                    mcp_schemas, mcp_dispatch = mgr.tools(
                        workspace_path=task.get("workspace_path"),
                    )
                    return self._run_loop(conn, task, llm_config, native_tools, mcp_schemas, mcp_dispatch, trace, app_config)

            return self._run_loop(conn, task, llm_config, native_tools, mcp_schemas, mcp_dispatch, trace, app_config)

    # ── Loop ──────────────────────────────────────────────────────────────────

    def _run_loop(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        llm_config: dict[str, Any],
        native_tools: list[Any],
        mcp_schemas: list[dict[str, Any]],
        mcp_dispatch: dict[str, Any],
        trace: Any = None,
        app_config: dict[str, Any] | None = None,
    ) -> str | None:
        provider, model, max_tokens = self._resolve_provider(llm_config)
        provider_id = str(provider.get("id", ""))
        provider_type = str(provider.get("type", "openai"))
        all_schemas = [t.schema() for t in native_tools] + mcp_schemas

        messages = self._restore_or_build_messages(conn, task)
        system_prompt = self._build_system_prompt(conn, task, app_config)

        final_content: str | None = None
        total_prompt_tokens = 0
        total_completion_tokens = 0

        if trace is not None:
            trace.attributes["llm.provider"] = provider_id
            trace.attributes["llm.model"] = model
            trace.attributes["llm.provider_type"] = provider_type

        for iteration in range(self.max_iterations):
            # ── LLM call span ────────────────────────────────────────────
            llm_attrs: dict[str, Any] = {
                "llm.provider": provider_id,
                "llm.model": model,
                "llm.max_tokens": max_tokens,
                "llm.iteration": iteration,
                "llm.message_count": len(messages),
                "llm.tool_count": len(all_schemas) if all_schemas else 0,
            }

            with trace.span("llm_call", kind="llm_call", attributes=llm_attrs) as llm_span:
                response = llm_mod.chat(
                    messages,
                    provider,
                    model,
                    system=system_prompt,
                    tools=all_schemas if all_schemas else None,
                    max_tokens=max_tokens,
                )

                if response.usage:
                    prompt_toks = response.usage.get("prompt_tokens", 0)
                    completion_toks = response.usage.get("completion_tokens", 0)
                    total_prompt_tokens += prompt_toks
                    total_completion_tokens += completion_toks
                    llm_span.set_attribute("llm.tokens.prompt", prompt_toks)
                    llm_span.set_attribute("llm.tokens.completion", completion_toks)
                    llm_span.set_attribute("llm.tokens.total", prompt_toks + completion_toks)

                llm_span.set_attribute("llm.response.is_final", response.is_final)
                llm_span.set_attribute("llm.response.tool_call_count", len(response.tool_calls))
                if response.content:
                    llm_span.set_attribute("llm.response.content_length", len(response.content))

            if response.is_final:
                final_content = response.content
                if response.content:
                    logger.debug("Capability %s finished after %d iteration(s).", self.name, iteration + 1)
                break

            # Append the assistant turn (with tool calls)
            messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
            })

            # Execute each tool call and append results
            for tc in response.tool_calls:
                tool_attrs = {
                    "tool.name": tc.name,
                    "tool.call_id": tc.id,
                    "tool.iteration": iteration,
                }
                with trace.span(f"tool:{tc.name}", kind="tool_call", attributes=tool_attrs) as tool_span:
                    try:
                        result = self._dispatch(tc, native_tools, mcp_dispatch, conn, task)
                    except TaskBlocked as exc:
                        # Save conversation state so the task can resume later.
                        # Include a synthetic tool result so the LLM knows what happened.
                        messages.append({
                            "role": "tool_result",
                            "tool_call_id": tc.id,
                            "content": (
                                f"Subtask spawned (id={exc.child_task_id}). "
                                "This task is now BLOCKED until the subtask completes. "
                                "You will resume with the subtask result."
                            ),
                        })
                        self._save_suspended_state(conn, task, messages)
                        tool_span.set_attribute("tool.blocked_by", exc.child_task_id)
                        raise
                    tool_span.set_attribute("tool.result_length", len(result))
                    if len(result) <= 500:
                        tool_span.set_attribute("tool.result_preview", result)
                    else:
                        tool_span.set_attribute("tool.result_preview", result[:500] + "…")

                logger.debug("Tool %s → %s", tc.name, result[:120])
                messages.append({
                    "role": "tool_result",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            logger.warning("Capability %s hit max_iterations=%d.", self.name, self.max_iterations)

        # Record totals on the trace
        if trace is not None:
            trace.attributes["llm.tokens.total_prompt"] = total_prompt_tokens
            trace.attributes["llm.tokens.total_completion"] = total_completion_tokens
            trace.attributes["llm.iterations"] = min(iteration + 1, self.max_iterations) if self.max_iterations > 0 else 0

        # Persist token usage
        if total_prompt_tokens + total_completion_tokens > 0:
            self._record_usage(conn, task, provider_id, model, total_prompt_tokens, total_completion_tokens)

        return final_content

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _record_usage(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        provider_id: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Write accumulated token usage for this task execution to task_costs."""
        total = prompt_tokens + completion_tokens
        conn.execute(
            """
            INSERT INTO task_costs
                (cost_id, task_id, provider_id, model_id,
                 prompt_tokens, completion_tokens, total_tokens,
                 estimated_cost_usd, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                new_id("cost"),
                task["task_id"],
                provider_id,
                model_id,
                prompt_tokens,
                completion_tokens,
                total,
                utc_now_iso(),
            ),
        )
        conn.commit()
        logger.debug(
            "Task %s: %s/%s — %d prompt + %d completion = %d tokens.",
            task["task_id"], provider_id, model_id,
            prompt_tokens, completion_tokens, total,
        )

    def _resolve_native_tools(self) -> list[Any]:
        resolved = []
        for name in self.tools:
            tool = tools_mod.get(name)
            if tool is None:
                logger.warning("Native tool '%s' not found in registry — skipping.", name)
            else:
                resolved.append(tool)
        return resolved

    def _resolve_provider(
        self, llm_config: dict[str, Any]
    ) -> tuple[dict[str, Any], str, int]:
        """
        Select the best (provider, model_id, max_tokens) triple for this capability.

        If `preferred_score` is set, iterate over all configured models, filter
        to those that satisfy `llm_features` requirements, and pick the one with
        the highest score for that dimension.  Ties are broken by declaration
        order (first wins).  Falls back to default_provider / default_model when:
          - preferred_score is empty
          - no model declares a score for that dimension
          - no model satisfies the llm_features requirements

        max_tokens is read from the model's `max_tokens` config field; defaults
        to 65535 when not specified.
        """
        _DEFAULT_MAX_TOKENS = 65535
        providers_list: list[dict[str, Any]] = llm_config.get("providers", [])

        if self.preferred_score:
            required_features = set(self.llm_features)
            best_score: int | float = -1
            best_provider: dict[str, Any] | None = None
            best_model_id: str | None = None
            best_model_dict: dict[str, Any] = {}

            for provider in providers_list:
                for model in provider.get("models", []):
                    model_features = set(model.get("features", []))
                    if not required_features.issubset(model_features):
                        continue
                    score = model.get("scores", {}).get(self.preferred_score, -1)
                    if score > best_score:
                        best_score = score
                        best_provider = provider
                        best_model_id = model["id"]
                        best_model_dict = model

            if best_provider is not None and best_model_id is not None:
                max_tokens = int(best_model_dict.get("max_tokens", _DEFAULT_MAX_TOKENS))
                logger.debug(
                    "Capability '%s': selected model '%s/%s' (score %s=%s, max_tokens=%d).",
                    self.name, best_provider.get("id"), best_model_id,
                    self.preferred_score, best_score, max_tokens,
                )
                return best_provider, best_model_id, max_tokens

        # Default fallback
        provider_id = str(llm_config.get("default_provider", "openai"))
        model_id = str(llm_config.get("default_model", "gpt-4.1"))
        providers_by_id = {p["id"]: p for p in providers_list}
        provider = providers_by_id.get(provider_id) or next(iter(providers_by_id.values()), {})
        model_dict = next(
            (m for m in provider.get("models", []) if m.get("id") == model_id),
            {},
        )
        max_tokens = int(model_dict.get("max_tokens", _DEFAULT_MAX_TOKENS))
        return provider, model_id, max_tokens

    def _build_initial_messages(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build the starting message list from task description + goal chat history.

        For work tasks (those with a description), the task description is always
        the primary instruction. Goal conversation history is appended as context
        so the LLM understands the broader project goal.
        """
        messages: list[dict[str, Any]] = []

        # Include recent goal messages as conversation context
        rows = conn.execute(
            """
            SELECT author_type, content
            FROM goal_messages
            WHERE goal_id = ?
            ORDER BY message_ts ASC, created_at ASC
            """,
            (task["goal_id"],),
        ).fetchall()

        goal_messages: list[dict[str, Any]] = []
        for row in rows:
            role = "user" if row["author_type"] == "user" else "assistant"
            goal_messages.append({"role": role, "content": row["content"]})

        task_desc = (task.get("description") or "").strip()
        task_title = (task.get("title") or "").strip()

        if task_desc:
            # Work task: task description is the primary instruction.
            # Prepend goal conversation as background context in a single
            # user message, then add the specific task instructions.
            if goal_messages:
                context_lines = []
                for gm in goal_messages:
                    prefix = "User" if gm["role"] == "user" else "Assistant"
                    context_lines.append(f"{prefix}: {gm['content']}")
                messages.append({
                    "role": "user",
                    "content": (
                        "[Project conversation for context]\n"
                        + "\n---\n".join(context_lines)
                    ),
                })
            header = f"[Task: {task_title}]\n" if task_title else ""
            messages.append({"role": "user", "content": f"{header}{task_desc}"})
        elif goal_messages:
            # Conversational task (chat_response, etc.): use goal messages directly
            messages.extend(goal_messages)

        return messages

    def _restore_or_build_messages(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Load saved conversation if this task is resuming from a blocked state,
        otherwise build fresh initial messages."""
        row = conn.execute(
            "SELECT suspended_state_json, blocked_by_task_id FROM tasks WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()

        suspended_json = row["suspended_state_json"] if row else None
        if not suspended_json:
            return self._build_initial_messages(conn, task)

        # Restore saved messages
        messages: list[dict[str, Any]] = json.loads(suspended_json)

        # Clear the suspended state so we don't reload it on retry
        conn.execute(
            "UPDATE tasks SET suspended_state_json = NULL, blocked_by_task_id = NULL, updated_at = ? WHERE task_id = ?",
            (utc_now_iso(), task["task_id"]),
        )
        conn.commit()

        # Inject subtask result as a continuation message
        child_task_id = row["blocked_by_task_id"]
        if child_task_id:
            child_summary = self._get_subtask_result(conn, task, child_task_id)
            messages.append({
                "role": "user",
                "content": child_summary,
            })

        logger.info("Task %s resuming from blocked state with %d saved messages.", task["task_id"], len(messages))
        return messages

    def _get_subtask_result(
        self,
        conn: sqlite3.Connection,
        parent_task: dict[str, Any],
        child_task_id: str,
    ) -> str:
        """Build a summary of the completed subtask for injection into the parent conversation."""
        child = conn.execute(
            "SELECT task_id, title, state, capability_name FROM tasks WHERE task_id = ?",
            (child_task_id,),
        ).fetchone()
        if child is None:
            return f"[Subtask {child_task_id} not found — it may have been deleted.]"

        state = child["state"]
        title = child["title"]

        if state == "failed":
            # Fetch error from the latest attempt
            attempt = conn.execute(
                "SELECT error_message FROM task_attempts WHERE task_id = ? ORDER BY started_at DESC LIMIT 1",
                (child_task_id,),
            ).fetchone()
            error = attempt["error_message"] if attempt else "unknown error"
            return (
                f"[Subtask FAILED]\n"
                f"Title: {title}\n"
                f"Error: {error}\n"
                f"You must handle this failure — retry, work around it, or report the issue."
            )

        # Collect any artifacts produced by the child
        artifacts = conn.execute(
            """
            SELECT artifact_key, value_json, file_path
            FROM artifacts
            WHERE produced_by_task_id = ? AND status IN ('active', 'approved')
            ORDER BY version DESC
            """,
            (child_task_id,),
        ).fetchall()

        parts = [
            f"[Subtask completed successfully]",
            f"Title: {title}",
            f"Capability: {child['capability_name']}",
        ]
        if artifacts:
            parts.append("Produced artifacts:")
            for art in artifacts:
                value = art["value_json"]
                fpath = art["file_path"]
                if value and len(value) <= 2000:
                    parts.append(f"  - {art['artifact_key']}: {value}")
                elif value:
                    parts.append(f"  - {art['artifact_key']}: {value[:2000]}… (truncated)")
                elif fpath:
                    fp = Path(fpath)
                    binary_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
                                   ".pdf", ".zip", ".tar", ".gz", ".bin", ".mp4"}
                    if fp.suffix.lower() in binary_exts:
                        parts.append(f"  - {art['artifact_key']}: [binary file: {fp.name}] (path: {fpath})")
                    elif fp.exists():
                        try:
                            content = fp.read_text(encoding="utf-8", errors="replace")
                            if len(content) <= 2000:
                                parts.append(f"  - {art['artifact_key']}: {content}")
                            else:
                                parts.append(f"  - {art['artifact_key']}: {content[:2000]}… (truncated, file: {fpath})")
                        except Exception:
                            parts.append(f"  - {art['artifact_key']}: [file: {fpath}]")
                    else:
                        parts.append(f"  - {art['artifact_key']}: [file missing: {fpath}]")
                else:
                    parts.append(f"  - {art['artifact_key']}: (empty artifact)")
        else:
            parts.append("No artifacts were produced — the subtask may have performed a side-effect (e.g. sent an email).")

        return "\n".join(parts)

    def _save_suspended_state(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> None:
        """Persist the current conversation messages so the task can resume later."""
        conn.execute(
            "UPDATE tasks SET suspended_state_json = ?, updated_at = ? WHERE task_id = ?",
            (json.dumps(messages, default=str), utc_now_iso(), task["task_id"]),
        )
        conn.commit()
        logger.info("Task %s: saved %d messages for resumption.", task["task_id"], len(messages))

    def _dispatch(
        self,
        tc: Any,  # llm_mod.ToolCall
        native_tools: list[Any],
        mcp_dispatch: dict[str, Any],
        conn: sqlite3.Connection,
        task: dict[str, Any],
    ) -> str:
        for tool in native_tools:
            if tool.name == tc.name:
                try:
                    return tool.run(conn, task, **tc.arguments)
                except TaskBlocked:
                    raise
                except Exception as exc:
                    return f"Error running tool '{tc.name}': {exc}"

        if tc.name in mcp_dispatch:
            try:
                return mcp_dispatch[tc.name](tc.arguments)
            except Exception as exc:
                return f"Error calling MCP tool '{tc.name}': {exc}"

        return f"Error: unknown tool '{tc.name}'."

    # ── Artifact contract injection ──────────────────────────────────────────

    def _build_system_prompt(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        app_config: dict[str, Any] | None = None,
    ) -> str:
        """Augment the capability system prompt with task-specific context.

        Injects the user's email identity (when email is enabled) and the
        artifact production contract so the LLM knows which artifact keys
        it must read and write.
        """
        extra_sections: list[str] = []

        # User identity (email)
        email_cfg = (app_config or {}).get("email", {})
        if email_cfg.get("enabled") and email_cfg.get("address"):
            extra_sections.append(
                f"\n## User Identity\n\n"
                f"Your email address is **{email_cfg['address']}**. "
                f"Use this address whenever a service asks for an email "
                f"(sign-up forms, account verification, contact fields, etc.)."
            )

        # Required (input) artifacts
        required_rows = conn.execute(
            "SELECT artifact_key, required_status FROM task_required_artifacts WHERE task_id = ?",
            (task["task_id"],),
        ).fetchall()
        if required_rows:
            lines = [_ARTIFACT_REQUIRED_HEADER]
            for row in required_rows:
                lines.append(f'- `read_artifact(artifact_key="{row["artifact_key"]}")` (expected status: {row["required_status"]})')
            extra_sections.append("\n".join(lines))

        # Produced (output) artifacts — the MANDATORY contract
        produced_rows = conn.execute(
            """
            SELECT artifact_key, artifact_type, delivery_mode
            FROM task_produced_artifacts
            WHERE task_id = ?
            """,
            (task["task_id"],),
        ).fetchall()
        if produced_rows:
            lines = [_ARTIFACT_CONTRACT_HEADER]
            for row in produced_rows:
                lines.append(
                    f'- `write_artifact(artifact_key="{row["artifact_key"]}", ...)` '
                    f'— type: {row["artifact_type"]}, delivery: {row["delivery_mode"]}'
                )
            lines.append("")
            lines.append(
                "Call `write_artifact` with the **exact artifact_key** listed above. "
                "Do NOT invent different keys. If you skip an artifact, the project DAG stalls."
            )
            extra_sections.append("\n".join(lines))

        if not extra_sections:
            return self.system_prompt

        return self.system_prompt.rstrip() + "\n" + "\n".join(extra_sections) + "\n"

    # ── Workspace directory management ───────────────────────────────────────

    def _ensure_workspace(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
    ) -> None:
        """Create the task workspace directory if it doesn't exist yet.

        Directory structure:  <db_dir>/../data/<project_id>/<task_id>/
        Sets task["workspace_path"] so tools (read_file, write_file) can use it.
        """
        if task.get("workspace_path"):
            # Already set (e.g. from a previous attempt)
            Path(task["workspace_path"]).mkdir(parents=True, exist_ok=True)
            return

        # Derive the data root from the DB path stored on the connection
        # The DB lives at <workdir>/runtime/runtime.db; data goes in <workdir>/data/
        db_path = conn.execute("PRAGMA database_list").fetchone()[2]
        if db_path:
            data_root = Path(db_path).resolve().parent.parent / "data"
        else:
            data_root = Path.cwd() / "data"

        workspace = data_root / task["project_id"] / task["task_id"]
        workspace.mkdir(parents=True, exist_ok=True)
        workspace_str = str(workspace)

        # Persist so the workspace survives restarts
        conn.execute(
            "UPDATE tasks SET workspace_path = ?, updated_at = ? WHERE task_id = ?",
            (workspace_str, utc_now_iso(), task["task_id"]),
        )
        conn.commit()
        task["workspace_path"] = workspace_str
        logger.debug("Task %s workspace: %s", task["task_id"], workspace_str)
