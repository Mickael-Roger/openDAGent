from __future__ import annotations

import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Callable

from .client import HttpMCPClient, StdioMCPClient, StreamableHttpMCPClient, _parse_tool_list

logger = logging.getLogger(__name__)

# Map MIME types to file extensions for saved images
_MIME_TO_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}


def _process_content_blocks(
    blocks: list[dict[str, Any]],
    server_id: str,
    workspace_path: str | None,
) -> str:
    """Convert raw MCP content blocks into a text string.

    - ``type=text`` blocks are collected as-is.
    - ``type=image`` blocks are decoded from base64 and saved as files
      in ``<workspace>/mcp_images/<server>_<uuid>.<ext>``.  A text
      reference is returned so the LLM (and humans) can find the file.
    - Other block types are serialised as JSON.
    """
    parts: list[str] = []

    for block in blocks:
        btype = block.get("type", "")

        if btype == "text":
            parts.append(block.get("text", ""))

        elif btype == "image":
            mime = block.get("mimeType", "image/png")
            data_b64 = block.get("data", "")
            if not data_b64:
                parts.append("[Image block with no data]")
                continue

            if workspace_path is None:
                logger.warning("MCP server '%s' returned an image but no workspace is available — skipping save.", server_id)
                parts.append(f"[Image from {server_id} — could not save: no workspace]")
                continue

            ext = _MIME_TO_EXT.get(mime, ".bin")
            img_dir = Path(workspace_path) / "mcp_images"
            img_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{server_id}_{uuid.uuid4().hex[:12]}{ext}"
            filepath = img_dir / filename

            try:
                filepath.write_bytes(base64.b64decode(data_b64))
                rel_path = f"mcp_images/{filename}"
                parts.append(f"[Image saved: {rel_path}]")
                logger.info("Saved MCP image from '%s': %s (%s)", server_id, filepath, mime)
            except Exception as exc:
                logger.error("Failed to save MCP image from '%s': %s", server_id, exc)
                parts.append(f"[Image from {server_id} — save failed: {exc}]")

        else:
            # Unknown block type — serialise so nothing is silently lost
            parts.append(json.dumps(block))

    return "\n".join(parts) if parts else ""


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

    def tools(
        self,
        workspace_path: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Callable[[dict[str, Any]], str]]]:
        """Return (schemas, dispatch_map) for all connected servers.

        *workspace_path* is the task workspace directory.  When an MCP tool
        returns image content blocks they are saved as files under
        ``<workspace>/mcp_images/``.
        """
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

                def _make_caller(
                    c: Any, orig: str, server_id: str, ws: str | None,
                ) -> Callable[[dict[str, Any]], str]:
                    def _call(arguments: dict[str, Any]) -> str:
                        blocks = c.call_tool(orig, arguments)
                        return _process_content_blocks(blocks, server_id, ws)
                    return _call

                dispatch[prefixed] = _make_caller(client, original, sid, workspace_path)

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
