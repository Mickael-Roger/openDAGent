"""
ChatGPT subscription OAuth — device-flow login and token management.

OAuth issuer:  https://auth.openai.com
Client ID:     app_EMoamEEZ73f0CkXaXp7hrann  (public, same as opencode)
Token storage: ~/.config/opendagent/chatgpt_oauth.json
"""
from __future__ import annotations

import hashlib
import base64
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_ISSUER = "https://auth.openai.com"
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_TOKEN_PATH = Path.home() / ".config" / "opendagent" / "chatgpt_oauth.json"
# Safety margin: refresh the token 60 s before it expires
_EXPIRY_MARGIN_S = 60


# ── PKCE helpers ──────────────────────────────────────────────────────────────

def _pkce_verifier() -> str:
    return secrets.token_urlsafe(32)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── Token storage ─────────────────────────────────────────────────────────────

def _load_tokens() -> dict[str, Any] | None:
    if not _TOKEN_PATH.exists():
        return None
    try:
        return json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_tokens(tokens: dict[str, Any]) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    # Restrict permissions so only the owner can read the token file
    try:
        _TOKEN_PATH.chmod(0o600)
    except OSError:
        pass


# ── Token refresh ─────────────────────────────────────────────────────────────

def _refresh(refresh_token: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{_ISSUER}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_access_token() -> tuple[str, str | None]:
    """
    Return ``(access_token, account_id)`` for a valid ChatGPT OAuth session.

    Refreshes the access token automatically when it is about to expire.
    Raises ``RuntimeError`` when no tokens are stored — run
    ``openDAGent --chatgpt-login`` first.
    """
    tokens = _load_tokens()
    if tokens is None:
        raise RuntimeError(
            "No ChatGPT OAuth tokens found. "
            "Run 'openDAGent --chatgpt-login' to authenticate."
        )

    expires_at: float = float(tokens.get("expires_at", 0))
    if time.time() + _EXPIRY_MARGIN_S >= expires_at:
        logger.info("ChatGPT access token expired or expiring soon — refreshing.")
        try:
            data = _refresh(tokens["refresh_token"])
        except Exception as exc:
            raise RuntimeError(
                f"Failed to refresh ChatGPT token: {exc}. "
                "Run 'openDAGent --chatgpt-login' to re-authenticate."
            ) from exc

        tokens["access_token"] = data["access_token"]
        tokens["refresh_token"] = data.get("refresh_token", tokens["refresh_token"])
        tokens["expires_at"] = time.time() + float(data.get("expires_in", 3600))
        _save_tokens(tokens)
        logger.info("ChatGPT access token refreshed.")

    return tokens["access_token"], tokens.get("account_id")


# ── Device-flow login ─────────────────────────────────────────────────────────

def login_device_flow() -> None:
    """
    Run the OAuth device-code flow interactively.

    Prints a URL and a user-code, then polls until the user authenticates.
    Saves the resulting tokens to ``~/.config/opendagent/chatgpt_oauth.json``.
    """
    print("\n── ChatGPT subscription login ───────────────────────────────────")

    # Step 1 — request a device code
    resp = httpx.post(
        f"{_ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": _CLIENT_ID},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Failed to start device authorization (HTTP {resp.status_code}): {resp.text[:500]}"
        )

    device_data = resp.json()
    device_auth_id: str = device_data["device_auth_id"]
    user_code: str = device_data["user_code"]
    poll_interval_s: float = max(float(device_data.get("interval", 5)), 1)

    print(f"\n  1. Open: https://auth.openai.com/codex/device")
    print(f"  2. Enter code: {user_code}")
    print(f"\nWaiting for authorization", end="", flush=True)

    # Step 2 — poll until authorized or timeout (5 minutes)
    deadline = time.time() + 5 * 60
    auth_code: str | None = None
    code_verifier: str | None = None

    while time.time() < deadline:
        time.sleep(poll_interval_s + 3)  # extra 3 s safety margin (mirrors opencode)
        print(".", end="", flush=True)

        poll_resp = httpx.post(
            f"{_ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        if poll_resp.status_code == 200:
            poll_data = poll_resp.json()
            auth_code = poll_data["authorization_code"]
            code_verifier = poll_data["code_verifier"]
            break

        # 403 / 404 = still pending; anything else = hard failure
        if poll_resp.status_code not in (403, 404):
            print()
            raise RuntimeError(
                f"Device authorization failed (HTTP {poll_resp.status_code}): {poll_resp.text[:500]}"
            )

    if auth_code is None or code_verifier is None:
        print()
        raise RuntimeError("Device authorization timed out (5 minutes). Please try again.")

    # Step 3 — exchange auth code for tokens
    token_resp = httpx.post(
        f"{_ISSUER}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": f"{_ISSUER}/deviceauth/callback",
            "client_id": _CLIENT_ID,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()

    account_id = _extract_account_id(token_data)

    tokens = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_at": time.time() + float(token_data.get("expires_in", 3600)),
        "account_id": account_id,
    }
    _save_tokens(tokens)

    print(f"\n\nAuthenticated successfully!")
    if account_id:
        print(f"Account ID: {account_id}")
    print(f"Tokens saved to: {_TOKEN_PATH}\n")


# ── JWT claim helpers ─────────────────────────────────────────────────────────

def _extract_account_id(token_data: dict[str, Any]) -> str | None:
    for key in ("id_token", "access_token"):
        jwt = token_data.get(key, "")
        if not jwt:
            continue
        account_id = _account_id_from_jwt(jwt)
        if account_id:
            return account_id
    return None


def _account_id_from_jwt(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace"))
    except Exception:
        return None
    return (
        claims.get("chatgpt_account_id")
        or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
        or ((claims.get("organizations") or [{}])[0]).get("id")
    )
