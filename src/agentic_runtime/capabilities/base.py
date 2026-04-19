from __future__ import annotations

import logging
import sqlite3
from typing import Any

from .. import llm as llm_mod
from .. import tools as tools_mod

logger = logging.getLogger(__name__)

_MAX_ITERATIONS_DEFAULT = 20


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
    ) -> None:
        native_tools = self._resolve_native_tools()
        mcp_schemas: list[dict[str, Any]] = []
        mcp_dispatch: dict[str, Any] = {}

        if self.mcp_servers:
            from ..mcp.manager import MCPManager
            with MCPManager(self.mcp_servers, mcp_config or {}) as mgr:
                mcp_schemas, mcp_dispatch = mgr.tools()
                self._run_loop(conn, task, llm_config, native_tools, mcp_schemas, mcp_dispatch)
            return

        self._run_loop(conn, task, llm_config, native_tools, mcp_schemas, mcp_dispatch)

    # ── Loop ──────────────────────────────────────────────────────────────────

    def _run_loop(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        llm_config: dict[str, Any],
        native_tools: list[Any],
        mcp_schemas: list[dict[str, Any]],
        mcp_dispatch: dict[str, Any],
    ) -> None:
        provider, model, max_tokens = self._resolve_provider(llm_config)
        all_schemas = [t.schema() for t in native_tools] + mcp_schemas

        messages = self._build_initial_messages(conn, task)

        for iteration in range(self.max_iterations):
            response = llm_mod.chat(
                messages,
                provider,
                model,
                system=self.system_prompt,
                tools=all_schemas if all_schemas else None,
                max_tokens=max_tokens,
            )

            if response.is_final:
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
                result = self._dispatch(tc, native_tools, mcp_dispatch, conn, task)
                logger.debug("Tool %s → %s", tc.name, result[:120])
                messages.append({
                    "role": "tool_result",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            logger.warning("Capability %s hit max_iterations=%d.", self.name, self.max_iterations)

    # ── Helpers ───────────────────────────────────────────────────────────────

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
        """Build the starting message list from task description + goal chat history."""
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

        for row in rows:
            role = "user" if row["author_type"] == "user" else "assistant"
            messages.append({"role": role, "content": row["content"]})

        # If there are no messages yet, seed with task description
        if not messages and task.get("description"):
            messages.append({"role": "user", "content": task["description"]})

        return messages

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
                except Exception as exc:
                    return f"Error running tool '{tc.name}': {exc}"

        if tc.name in mcp_dispatch:
            try:
                return mcp_dispatch[tc.name](tc.arguments)
            except Exception as exc:
                return f"Error calling MCP tool '{tc.name}': {exc}"

        return f"Error: unknown tool '{tc.name}'."
