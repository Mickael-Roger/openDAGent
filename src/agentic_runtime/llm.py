from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Retry schedule ────────────────────────────────────────────────────────────
#
# HTTP status codes that are transient and worth retrying indefinitely:
#   429  Too Many Requests / quota exhausted
#   500  Internal Server Error (transient backend fault)
#   502  Bad Gateway
#   503  Service Unavailable
#   504  Gateway Timeout
#
# All other 4xx errors are permanent (bad request, auth failure, …) and are
# re-raised immediately without retrying.
#
# The retry schedule in seconds (applied sequentially, then the last value
# repeats forever):
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRY_DELAYS = [10, 30, 120, 300, 600]  # 10s, 30s, 2m, 5m, 10m, 10m, …


def _log_http_error(exc: Exception, provider_label: str) -> None:
    """Log the response body for non-retryable HTTP errors to aid debugging."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return
    status = resp.status_code
    try:
        body = resp.text[:2000]
    except Exception:
        body = "<unreadable>"
    logger.error(
        "%s API error %s — response body: %s",
        provider_label, status, body,
    )


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is transient and the request should be retried."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    # Network-level errors (connection refused, timeout, DNS failure, …)
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    return False


def _retry_delay(attempt: int, exc: Exception | None = None) -> float:
    """
    Return the number of seconds to wait before attempt number `attempt` (0-based).
    If the response carries a Retry-After header, that value takes precedence.
    """
    if exc is not None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            retry_after = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset-requests")
            if retry_after:
                try:
                    return max(float(retry_after), 1.0)
                except ValueError:
                    pass
    idx = min(attempt, len(_RETRY_DELAYS) - 1)
    return float(_RETRY_DELAYS[idx])


# ── Response types ────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] | None = None  # prompt_tokens, completion_tokens

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


# ── Auth ──────────────────────────────────────────────────────────────────────

def resolve_api_key(auth_config: dict[str, Any]) -> str | None:
    if auth_config.get("type") != "api_key":
        return None
    value = auth_config.get("value", "")
    if value:
        return str(value)
    env_var = auth_config.get("env_var", "")
    return os.environ.get(str(env_var)) if env_var else None


def _resolve_oauth_token() -> tuple[str, str | None]:
    """Return ``(access_token, account_id)`` for a ChatGPT OAuth session."""
    from .chatgpt_auth import get_valid_access_token
    return get_valid_access_token()


# ── Message conversion ────────────────────────────────────────────────────────
#
# Internal message format (used throughout the codebase):
#   {"role": "user"|"assistant", "content": str}
#   {"role": "assistant", "content": str|None, "tool_calls": [{"id","name","arguments"}]}
#   {"role": "tool_result", "tool_call_id": str, "content": str}

def _to_openai_messages(
    messages: list[dict[str, Any]],
    system: str | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for msg in messages:
        role = msg["role"]
        if role == "tool_result":
            result.append({
                "role": "tool",
                "tool_call_id": msg["tool_call_id"],
                "content": str(msg.get("content", "")),
            })
        elif role == "assistant" and msg.get("tool_calls"):
            result.append({
                "role": "assistant",
                "content": msg.get("content"),
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"]),
                        },
                    }
                    for tc in msg["tool_calls"]
                ],
            })
        else:
            result.append({"role": role, "content": msg.get("content", "")})
    return result


def _to_anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Anthropic requires alternating user/assistant turns.
    # tool_result messages must be grouped into user messages.
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg["role"]

        if role == "tool_result":
            # Collect consecutive tool_result messages into one user message
            blocks: list[dict[str, Any]] = []
            while i < len(messages) and messages[i]["role"] == "tool_result":
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": messages[i]["tool_call_id"],
                    "content": str(messages[i].get("content", "")),
                })
                i += 1
            result.append({"role": "user", "content": blocks})

        elif role == "assistant" and msg.get("tool_calls"):
            blocks = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["arguments"],
                })
            result.append({"role": "assistant", "content": blocks})
            i += 1

        else:
            result.append({"role": role, "content": msg.get("content", "")})
            i += 1

    return result


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": t} for t in tools]


def _anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


# ── OpenAI Responses API message converter ────────────────────────────────────
#
# The ChatGPT subscription endpoint (`https://chatgpt.com/backend-api/codex/responses`)
# follows the OpenAI Responses API spec (not chat/completions).
# Key differences:
#   - Body uses `input` (array) instead of `messages`
#   - System prompt goes in a `{"role": "system", "content": "..."}` item
#   - Tool calls are top-level items: `{"type": "function_call", "call_id", "name", "arguments"}`
#   - Tool results: `{"type": "function_call_output", "call_id", "output"}`
#   - Response `output` array replaces `choices`
#   - Usage keys: `input_tokens` / `output_tokens`

def _to_responses_input(
    messages: list[dict[str, Any]],
    system: str | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if system:
        result.append({"role": "system", "content": system})
    for msg in messages:
        role = msg["role"]
        if role == "tool_result":
            result.append({
                "type": "function_call_output",
                "call_id": msg["tool_call_id"],
                "output": str(msg.get("content", "")),
            })
        elif role == "assistant" and msg.get("tool_calls"):
            # Emit each tool call as a top-level function_call item, then the
            # text content (if any) as a separate message item.
            if msg.get("content"):
                result.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": msg["content"]}],
                })
            for tc in msg["tool_calls"]:
                result.append({
                    "type": "function_call",
                    "call_id": tc["id"],
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"]),
                })
        elif role == "assistant":
            result.append({
                "role": "assistant",
                "content": [{"type": "output_text", "text": msg.get("content", "")}],
            })
        else:  # user
            result.append({"role": role, "content": str(msg.get("content", ""))})
    return result


def _responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


# ── Main entry point ──────────────────────────────────────────────────────────

def chat(
    messages: list[dict[str, Any]],
    provider_config: dict[str, Any],
    model_name: str,
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 8192,
) -> LLMResponse:
    provider_type = str(provider_config.get("type", "openai"))
    endpoint = str(provider_config.get("endpoint", "")).rstrip("/")
    api_key = resolve_api_key(provider_config.get("auth", {}))

    if provider_type == "chatgpt":
        return _chatgpt_chat(messages, model_name, system, tools, max_tokens)
    if provider_type == "anthropic":
        return _anthropic_chat(messages, endpoint, api_key, model_name, system, tools, max_tokens)
    return _openai_chat(messages, endpoint, api_key, model_name, system, tools, max_tokens)


def _openai_chat(
    messages: list[dict[str, Any]],
    endpoint: str,
    api_key: str | None,
    model_name: str,
    system: str | None,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> LLMResponse:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": _to_openai_messages(messages, system),
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = _openai_tools(tools)
        payload["tool_choice"] = "auto"

    for attempt in range(10_000):  # effectively infinite
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{endpoint}/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
            break
        except Exception as exc:
            if not _is_retryable(exc):
                _log_http_error(exc, "OpenAI-compatible")
                raise
            delay = _retry_delay(attempt, exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "OpenAI-compatible request failed (attempt %d, status=%s): %s — retrying in %.0fs",
                attempt + 1, status, exc, delay,
            )
            time.sleep(delay)

    data = resp.json()
    message = data["choices"][0]["message"]
    content: str | None = message.get("content")
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        raw_args = tc["function"]["arguments"]
        try:
            arguments = json.loads(raw_args)
        except json.JSONDecodeError:
            logger.warning(
                "Tool call '%s' returned malformed JSON arguments (truncated response?): %r — "
                "using empty arguments so the LLM can recover.",
                tc["function"]["name"], raw_args,
            )
            arguments = {}
        tool_calls.append(ToolCall(
            id=tc["id"],
            name=tc["function"]["name"],
            arguments=arguments,
        ))
    raw_usage = data.get("usage", {}) or {}
    usage: dict[str, int] | None = None
    if raw_usage:
        usage = {
            "prompt_tokens": int(raw_usage.get("prompt_tokens", 0)),
            "completion_tokens": int(raw_usage.get("completion_tokens", 0)),
        }
    return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)


def _anthropic_chat(
    messages: list[dict[str, Any]],
    endpoint: str,
    api_key: str | None,
    model_name: str,
    system: str | None,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> LLMResponse:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["x-api-key"] = api_key

    payload: dict[str, Any] = {
        "model": model_name,
        "max_tokens": max_tokens,
        "messages": _to_anthropic_messages(messages),
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = _anthropic_tools(tools)

    for attempt in range(10_000):  # effectively infinite
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{endpoint}/v1/messages", headers=headers, json=payload)
                resp.raise_for_status()
            break
        except Exception as exc:
            if not _is_retryable(exc):
                _log_http_error(exc, "Anthropic")
                raise
            delay = _retry_delay(attempt, exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "Anthropic request failed (attempt %d, status=%s): %s — retrying in %.0fs",
                attempt + 1, status, exc, delay,
            )
            time.sleep(delay)

    data = resp.json()
    content: str | None = None
    tool_calls: list[ToolCall] = []
    for block in data.get("content", []):
        if block["type"] == "text":
            content = (content or "") + block["text"]
        elif block["type"] == "tool_use":
            tool_calls.append(ToolCall(
                id=block["id"],
                name=block["name"],
                arguments=block["input"],
            ))
    raw_usage = data.get("usage", {}) or {}
    usage: dict[str, int] | None = None
    if raw_usage:
        usage = {
            "prompt_tokens": int(raw_usage.get("input_tokens", 0)),
            "completion_tokens": int(raw_usage.get("output_tokens", 0)),
        }
    return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)


# ── ChatGPT subscription (OAuth + Responses API) ─────────────────────────────

_CHATGPT_ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"


def _chatgpt_chat(
    messages: list[dict[str, Any]],
    model_name: str,
    system: str | None,
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> LLMResponse:
    access_token, account_id = _resolve_oauth_token()

    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id

    payload: dict[str, Any] = {
        "model": model_name,
        "input": _to_responses_input(messages, system),
        "max_output_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = _responses_tools(tools)

    for attempt in range(10_000):  # effectively infinite
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(_CHATGPT_ENDPOINT, headers=headers, json=payload)
                resp.raise_for_status()
            break
        except Exception as exc:
            if not _is_retryable(exc):
                _log_http_error(exc, "ChatGPT")
                raise
            delay = _retry_delay(attempt, exc)
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "ChatGPT request failed (attempt %d, status=%s): %s — retrying in %.0fs",
                attempt + 1, status, exc, delay,
            )
            time.sleep(delay)

    data = resp.json()

    content: str | None = None
    tool_calls: list[ToolCall] = []

    for item in data.get("output", []):
        item_type = item.get("type")
        if item_type == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    content = (content or "") + block["text"]
        elif item_type == "function_call":
            raw_args = item.get("arguments", "{}")
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError:
                logger.warning(
                    "ChatGPT tool call '%s' returned malformed JSON arguments: %r — using empty dict.",
                    item.get("name"), raw_args,
                )
                arguments = {}
            tool_calls.append(ToolCall(
                id=item.get("call_id") or item.get("id", ""),
                name=item.get("name", ""),
                arguments=arguments,
            ))

    raw_usage = data.get("usage", {}) or {}
    usage: dict[str, int] | None = None
    if raw_usage:
        usage = {
            "prompt_tokens": int(raw_usage.get("input_tokens", 0)),
            "completion_tokens": int(raw_usage.get("output_tokens", 0)),
        }
    return LLMResponse(content=content, tool_calls=tool_calls, usage=usage)


# ── Backward-compatible helper ────────────────────────────────────────────────

def complete(
    messages: list[dict[str, str]],
    provider_config: dict[str, Any],
    model_name: str,
    *,
    system: str | None = None,
    max_tokens: int = 8192,
) -> str:
    resp = chat(messages, provider_config, model_name, system=system, max_tokens=max_tokens)
    return resp.content or ""
