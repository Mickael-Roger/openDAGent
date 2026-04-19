from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import httpx


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _parse_tool_list(raw: list[dict[str, Any]], server_id: str) -> list[dict[str, Any]]:
    """Normalize MCP tool definitions to the internal tool schema format."""
    tools = []
    for t in raw:
        tools.append({
            "name": f"{server_id}__{t['name']}",
            "description": t.get("description", ""),
            # MCP uses inputSchema; map to our parameters field
            "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
            "_mcp_original_name": t["name"],
            "_mcp_server_id": server_id,
        })
    return tools


# ── stdio transport ────────────────────────────────────────────────────────────

class StdioMCPClient:
    """Minimal MCP client over stdio (JSON-RPC 2.0, line-delimited)."""

    def __init__(
        self,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
    ) -> None:
        resolved_env = {**os.environ}
        if env:
            resolved_env.update(env)

        self._proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=resolved_env,
        )
        self._req_id = 0

    def _send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._req_id += 1
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            msg["params"] = params
        line = json.dumps(msg) + "\n"
        assert self._proc.stdin is not None
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()
        assert self._proc.stdout is not None
        raw = self._proc.stdout.readline()
        if not raw:
            raise RuntimeError(f"MCP server closed stdout unexpectedly (method={method})")
        return json.loads(raw).get("result")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        line = json.dumps(msg) + "\n"
        assert self._proc.stdin is not None
        self._proc.stdin.write(line.encode())
        self._proc.stdin.flush()

    def initialize(self) -> None:
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "openDAGent", "version": "1.0"},
        })
        self._notify("notifications/initialized")

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._send("tools/list")
        return result.get("tools", []) if result else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        if not result:
            return ""
        parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return "\n".join(parts) if parts else json.dumps(result)

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()


# ── HTTP transport ─────────────────────────────────────────────────────────────

class HttpMCPClient:
    """Minimal MCP client over HTTP (JSON-RPC 2.0 POST)."""

    def __init__(self, url: str, auth_config: dict[str, Any] | None = None) -> None:
        from ..llm import resolve_api_key
        self._url = url.rstrip("/")
        self._req_id = 0
        api_key = resolve_api_key(auth_config or {})
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def _send(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._req_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._req_id, "method": method}
        if params is not None:
            payload["params"] = params
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(self._url, headers=self._headers, json=payload)
            resp.raise_for_status()
        return resp.json().get("result")

    def initialize(self) -> None:
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "openDAGent", "version": "1.0"},
        })

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._send("tools/list")
        return result.get("tools", []) if result else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        if not result:
            return ""
        parts = [
            block.get("text", "")
            for block in result.get("content", [])
            if block.get("type") == "text"
        ]
        return "\n".join(parts) if parts else json.dumps(result)

    def close(self) -> None:
        pass  # stateless HTTP; nothing to tear down
