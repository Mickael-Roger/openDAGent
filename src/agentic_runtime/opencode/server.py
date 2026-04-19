"""
Manages a persistent `opencode serve` subprocess and exposes a thin REST client
around its session/message API.

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

# Default port for opencode serve
DEFAULT_PORT = 9180


def _build_opencode_config(llm_config: dict[str, Any], model_hint: str | None = None) -> dict[str, Any]:
    """
    Map openDAGent's llm_config into an opencode JSON config dict.

    opencode config format:
    {
      "model": "<provider>/<model-id>",
      "providers": {
        "<provider-id>": {
          "apiKey": "...",
          "baseURL": "..."   # for custom endpoints
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
        for provider in llm_config.get("providers", []):
            pid = provider.get("id", provider.get("type", ""))
            for model in provider.get("models", []):
                default_model = f"{pid}/{model.get('id', '')}"
                break
            else:
                continue
            break
        else:
            default_model = ""

    config: dict[str, Any] = {}
    if default_model:
        config["model"] = default_model
    if providers_out:
        config["providers"] = providers_out

    return config


class OpencodeServer:
    """Manages a single `opencode serve` process."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self._process: subprocess.Popen[bytes] | None = None
        self._base_url = f"http://127.0.0.1:{port}"

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def start(self, llm_config: dict[str, Any], model_hint: str | None = None) -> None:
        """
        Start `opencode serve` with the given LLM config injected via
        OPENCODE_CONFIG_CONTENT environment variable.
        """
        if self._process is not None:
            logger.warning("opencode server already running (pid %d)", self._process.pid)
            return

        config_dict = _build_opencode_config(llm_config, model_hint)
        env = os.environ.copy()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config_dict)

        cmd = ["opencode", "serve", "--port", str(self.port)]
        logger.info("Starting opencode serve on port %d ...", self.port)
        try:
            self._process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            logger.error("opencode binary not found; coding capabilities will be unavailable.")
            return

        # Wait for the server to be ready (up to 15 s)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not self.is_alive():
                logger.error("opencode serve exited unexpectedly during startup.")
                return
            try:
                httpx.get(f"{self._base_url}/", timeout=1.0)
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

    # ── REST client ────────────────────────────────────────────────────────────

    def create_session(self) -> str:
        """Create a new opencode session; returns the session ID."""
        resp = httpx.post(f"{self._base_url}/session", timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        return data["id"]

    def send_message(self, session_id: str, text: str, timeout: float = 120.0) -> str:
        """
        Send a message to an existing session and wait for the complete reply.
        Returns the assistant's reply text.
        """
        resp = httpx.post(
            f"{self._base_url}/session/{session_id}/message",
            json={"role": "user", "content": text},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        # opencode returns the updated message list; extract last assistant message
        messages = data if isinstance(data, list) else data.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Content may be a list of blocks
                    return "\n".join(
                        block.get("text", "") for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    )
                return str(content)
        return ""

    def delete_session(self, session_id: str) -> None:
        """Delete an opencode session."""
        try:
            resp = httpx.delete(f"{self._base_url}/session/{session_id}", timeout=10.0)
            resp.raise_for_status()
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
