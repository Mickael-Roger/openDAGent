from __future__ import annotations

import logging
import os
from typing import Any, Callable

from .client import HttpMCPClient, StdioMCPClient, StreamableHttpMCPClient, _parse_tool_list

logger = logging.getLogger(__name__)


class MCPManager:
    """
    Context manager that opens MCP server connections for the duration of a task,
    exposes their tools as a unified schema + dispatcher, then closes them.

    Usage::

        with MCPManager(["filesystem", "github"], mcp_config) as mgr:
            schemas, dispatch = mgr.tools()
            # schemas: list[dict] — tool schemas for the LLM
            # dispatch: dict[str, Callable] — prefixed_name -> call(arguments) -> str
    """

    def __init__(self, server_ids: list[str], mcp_config: dict[str, Any]) -> None:
        self._server_ids = server_ids
        self._server_defs: dict[str, dict[str, Any]] = {
            s["id"]: s for s in mcp_config.get("servers", [])
        }
        self._clients: dict[str, StdioMCPClient | HttpMCPClient | StreamableHttpMCPClient] = {}

    def __enter__(self) -> MCPManager:
        for sid in self._server_ids:
            if sid not in self._server_defs:
                logger.warning("MCP server '%s' not defined in config — skipping.", sid)
                continue
            try:
                client = self._connect(self._server_defs[sid])
                client.initialize()
                self._clients[sid] = client
                logger.info("MCP server '%s' connected.", sid)
            except Exception:
                logger.exception("Failed to connect MCP server '%s'.", sid)
        return self

    def __exit__(self, *_: Any) -> None:
        for sid, client in self._clients.items():
            try:
                client.close()
                logger.debug("MCP server '%s' closed.", sid)
            except Exception:
                logger.warning("Error closing MCP server '%s'.", sid)
        self._clients.clear()

    def tools(self) -> tuple[list[dict[str, Any]], dict[str, Callable[[dict[str, Any]], str]]]:
        """Return (schemas, dispatch_map) for all connected servers."""
        schemas: list[dict[str, Any]] = []
        dispatch: dict[str, Callable[[dict[str, Any]], str]] = {}

        for sid, client in self._clients.items():
            try:
                raw_tools = client.list_tools()
            except Exception:
                logger.exception("Failed to list tools from MCP server '%s'.", sid)
                continue

            for tool_def in _parse_tool_list(raw_tools, sid):
                prefixed = tool_def["name"]
                original = tool_def["_mcp_original_name"]
                schemas.append({k: v for k, v in tool_def.items() if not k.startswith("_")})

                # Capture client + original name in closure
                def _make_caller(c: Any, orig: str) -> Callable[[dict[str, Any]], str]:
                    def _call(arguments: dict[str, Any]) -> str:
                        return c.call_tool(orig, arguments)
                    return _call

                dispatch[prefixed] = _make_caller(client, original)

        return schemas, dispatch

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect(self, cfg: dict[str, Any]) -> StdioMCPClient | HttpMCPClient | StreamableHttpMCPClient:
        transport = cfg.get("transport", "stdio")

        if transport == "stdio":
            raw_env: dict[str, Any] = cfg.get("env", {}) or {}
            resolved_env: dict[str, str] = {}
            for key, val in raw_env.items():
                if isinstance(val, dict) and "env_var" in val:
                    resolved_env[key] = os.environ.get(val["env_var"], "")
                else:
                    resolved_env[key] = str(val)
            return StdioMCPClient(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=resolved_env if resolved_env else None,
            )

        if transport == "http":
            return HttpMCPClient(url=cfg["url"], auth_config=cfg.get("auth"))

        if transport == "streamable":
            return StreamableHttpMCPClient(url=cfg["url"], auth_config=cfg.get("auth"))

        raise ValueError(f"Unknown MCP transport: {transport!r}")
