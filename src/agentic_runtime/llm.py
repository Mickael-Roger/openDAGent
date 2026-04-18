from __future__ import annotations

import os
from typing import Any

import httpx


def resolve_api_key(auth_config: dict[str, Any]) -> str | None:
    auth_type = auth_config.get("type", "none")
    if auth_type != "api_key":
        return None
    value = auth_config.get("value", "")
    if value:
        return str(value)
    env_var = auth_config.get("env_var", "")
    return os.environ.get(str(env_var)) if env_var else None


def complete(
    messages: list[dict[str, str]],
    provider_config: dict[str, Any],
    model_name: str,
    *,
    system: str | None = None,
    max_tokens: int = 2048,
) -> str:
    provider_type = str(provider_config.get("type", "openai"))
    endpoint = str(provider_config["endpoint"]).rstrip("/")
    api_key = resolve_api_key(provider_config.get("auth", {}))

    if provider_type == "anthropic":
        return _anthropic_complete(messages, endpoint, api_key, model_name, system, max_tokens)
    return _openai_complete(messages, endpoint, api_key, model_name, system, max_tokens)


def _openai_complete(
    messages: list[dict[str, str]],
    endpoint: str,
    api_key: str | None,
    model_name: str,
    system: str | None,
    max_tokens: int,
) -> str:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    all_messages: list[dict[str, str]] = []
    if system:
        all_messages.append({"role": "system", "content": system})
    all_messages.extend(messages)

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{endpoint}/chat/completions",
            headers=headers,
            json={"model": model_name, "messages": all_messages, "max_tokens": max_tokens},
        )
        resp.raise_for_status()
        return str(resp.json()["choices"][0]["message"]["content"])


def _anthropic_complete(
    messages: list[dict[str, str]],
    endpoint: str,
    api_key: str | None,
    model_name: str,
    system: str | None,
    max_tokens: int,
) -> str:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    if api_key:
        headers["x-api-key"] = api_key

    payload: dict[str, Any] = {
        "model": model_name,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            f"{endpoint}/v1/messages",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        return str(resp.json()["content"][0]["text"])
