"""
Manages a persistent `opencode serve` subprocess and exposes a thin REST client
around its session/message API.

opencode API reference: https://opencode.ai/docs/server

Lifecycle:
  start()   — launch `opencode serve --port <port>` with OPENCODE_CONFIG_CONTENT injected
  stop()    — terminate the subprocess
  is_alive() — True while the process is running

REST methods (thin wrappers around httpx):
  create_session()             → session_id: str
  send_message(session_id, text) → reply text (blocks until done)
  delete_session(session_id)   → None
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Default port when explicitly configured; 0 means auto-assign a free port.
DEFAULT_PORT = 0


def _build_opencode_config(llm_config: dict[str, Any], model_hint: str | None = None) -> dict[str, Any]:
    """
    Map openDAGent's llm_config into an opencode JSON config dict.

    opencode config format (https://opencode.ai/docs/config):
    {
      "$schema": "https://opencode.ai/config.json",
      "model": "<provider>/<model-id>",
      "provider": {
        "<provider-id>": {
          "apiKey": "...",
          "baseURL": "..."
        }
      }
    }
    """
    providers_out: dict[str, Any] = {}
    first_model: str | None = None

    for provider in llm_config.get("providers", []):
        ptype = provider.get("type", provider.get("id", ""))
        pid = provider.get("id", ptype)
        auth = provider.get("auth", {})
        endpoint = provider.get("endpoint", "")

        entry: dict[str, Any] = {}

        # Resolve API key from env var
        if auth.get("type") == "api_key":
            env_var = auth.get("env_var", "")
            api_key = os.environ.get(env_var, "")
            if api_key:
                entry["apiKey"] = api_key

        # Custom endpoint (only set if it differs from the canonical one)
        canonical_endpoints = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
        }
        if endpoint and endpoint != canonical_endpoints.get(ptype, ""):
            entry["baseURL"] = endpoint

        if entry:
            providers_out[pid] = entry

        # Pick the first code-capable model as default if no hint given
        for model in provider.get("models", []):
            mid = model.get("id", "")
            features = model.get("features", [])
            if "code" in features and first_model is None:
                first_model = f"{pid}/{mid}"

    # Determine which model to use
    if model_hint:
        default_model = model_hint
    elif first_model:
        default_model = first_model
    else:
        # Fall back to first model of any kind
        default_model = ""
        for provider in llm_config.get("providers", []):
            pid = provider.get("id", provider.get("type", ""))
            for model in provider.get("models", []):
                default_model = f"{pid}/{model.get('id', '')}"
                break
            else:
                continue
            break

    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
    }
    if default_model:
        config["model"] = default_model
    if providers_out:
        config["provider"] = providers_out

    return config


class OpencodeServer:
    """Manages a single `opencode serve` process."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._base_url = f"http://127.0.0.1:{port}" if port else ""

    @staticmethod
    def _find_free_port() -> int:
        """Bind to port 0 and let the OS assign a free port."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self, llm_config: dict[str, Any], model_hint: str | None = None) -> None:
        """
        Start `opencode serve` with the given LLM config injected via
        OPENCODE_CONFIG_CONTENT environment variable.

        If port is 0, a free port is auto-assigned so multiple instances
        can run concurrently without conflicts.
        """
        if self._process is not None:
            logger.warning("opencode server already running (pid %d)", self._process.pid)
            return

        # Auto-assign a free port if none was explicitly configured
        if self.port == 0:
            self.port = self._find_free_port()
            self._base_url = f"http://127.0.0.1:{self.port}"
            logger.info("Auto-assigned port %d for opencode serve.", self.port)

        config_dict = _build_opencode_config(llm_config, model_hint)
        config_json = json.dumps(config_dict)
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = config_json

        cmd = ["opencode", "serve", "--port", str(self.port)]
        logger.info("Starting opencode serve on port %d ...", self.port)
        logger.debug("opencode config: %s", config_json)
        try:
            self._process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("opencode binary not found; coding capabilities will be unavailable.")
            return

        # Wait for the server to be ready (up to 15 s)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not self.is_alive():
                stderr_output = self._read_stderr()
                logger.error(
                    "opencode serve exited unexpectedly during startup.%s",
                    f" stderr: {stderr_output}" if stderr_output else "",
                )
                return
            try:
                resp = httpx.get(f"{self._base_url}/global/health", timeout=1.0)
                if resp.status_code == 200:
                    logger.info("opencode serve is ready on port %d.", self.port)
                    return
            except httpx.RequestError:
                time.sleep(0.5)

        logger.warning("opencode serve did not respond within 15 s; proceeding anyway.")

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            self._process.kill()
        self._process = None
        logger.info("opencode serve stopped.")

    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.poll() is None

    def _read_stderr(self) -> str:
        """Read available stderr from the subprocess (non-blocking)."""
        if self._process is None or self._process.stderr is None:
            return ""
        try:
            # Read what's available without blocking
            import select
            if select.select([self._process.stderr], [], [], 0.1)[0]:
                return self._process.stderr.read(4096).decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        return ""

    # ── REST client ────────────────────────────────────────────────────────────

    def create_session(self) -> str:
        """Create a new opencode session; returns the session ID."""
        resp = httpx.post(
            f"{self._base_url}/session",
            json={},
            timeout=10.0,
        )
        if resp.status_code != 200:
            body = resp.text[:500]
            logger.error("opencode create_session failed (%d): %s", resp.status_code, body)
            resp.raise_for_status()
        data = resp.json()
        session_id = data.get("id", "")
        if not session_id:
            logger.error("opencode create_session returned no id: %s", json.dumps(data)[:500])
            raise RuntimeError("opencode create_session returned no session id")
        logger.debug("opencode session created: %s", session_id)
        return session_id

    def send_message(self, session_id: str, text: str, timeout: float = 120.0) -> str:
        """
        Send a message to an existing session and wait for the complete reply.
        Returns the assistant's reply text.

        opencode message API (https://opencode.ai/docs/server):
          POST /session/:id/message
          Body: { parts: [{ type: "text", text: "..." }], model?, agent?, ... }
          Response: { info: Message, parts: Part[] }
        """
        payload: dict[str, Any] = {
            "parts": [{"type": "text", "text": text}],
        }
        logger.debug(
            "opencode send_message session=%s text_len=%d",
            session_id, len(text),
        )
        resp = httpx.post(
            f"{self._base_url}/session/{session_id}/message",
            json=payload,
            timeout=timeout,
        )
        if resp.status_code != 200:
            body = resp.text[:1000]
            logger.error(
                "opencode send_message failed (%d) session=%s: %s",
                resp.status_code, session_id, body,
            )
            resp.raise_for_status()

        data = resp.json()
        return self._extract_reply_text(data)

    @staticmethod
    def _extract_reply_text(data: dict[str, Any]) -> str:
        """
        Extract the assistant reply text from an opencode message response.

        Response format: { info: {...}, parts: [{ type: "text", text: "..." }, ...] }
        """
        parts = data.get("parts", [])
        if isinstance(parts, list):
            text_parts = []
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
            if text_parts:
                return "\n".join(text_parts)

        # Fallback: try to extract from the info field or raw data
        info = data.get("info", {})
        if isinstance(info, dict):
            content = info.get("content", "")
            if content:
                return str(content)

        logger.warning(
            "opencode: could not extract reply text from response keys=%s",
            list(data.keys()),
        )
        return ""

    def delete_session(self, session_id: str) -> None:
        """Delete an opencode session."""
        try:
            resp = httpx.delete(
                f"{self._base_url}/session/{session_id}",
                timeout=10.0,
            )
            if resp.status_code >= 400:
                logger.debug(
                    "opencode delete_session %s returned %d",
                    session_id, resp.status_code,
                )
        except Exception as exc:
            logger.debug("Failed to delete opencode session %s: %s", session_id, exc)


# ── Module-level singleton ─────────────────────────────────────────────────────

_SERVER: OpencodeServer | None = None


def get_server() -> OpencodeServer | None:
    return _SERVER


def init_server(
    llm_config: dict[str, Any],
    port: int = DEFAULT_PORT,
    model_hint: str | None = None,
) -> OpencodeServer:
    """Start the global opencode server singleton (idempotent)."""
    global _SERVER
    if _SERVER is None:
        _SERVER = OpencodeServer(port=port)
        _SERVER.start(llm_config, model_hint=model_hint)
    return _SERVER


def shutdown_server() -> None:
    global _SERVER
    if _SERVER is not None:
        _SERVER.stop()
        _SERVER = None
