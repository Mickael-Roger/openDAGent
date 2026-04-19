from __future__ import annotations

import email as _email_stdlib
import imaplib
import smtplib
import sqlite3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.header import decode_header as _decode_header
from typing import Any

from . import Tool

# ── Module-level config (set at startup via configure()) ─────────────────────

_EMAIL_CONFIG: dict[str, Any] = {}


def configure(cfg: dict[str, Any]) -> None:
    """Called at startup with the email section of the app config."""
    global _EMAIL_CONFIG
    _EMAIL_CONFIG = cfg


# ── Helpers ───────────────────────────────────────────────────────────────────

def _imap_connect() -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
    imap = _EMAIL_CONFIG.get("imap", {})
    host = imap.get("host", "")
    port = int(imap.get("port", 993))
    if not host:
        raise RuntimeError("Email IMAP host is not configured.")
    # Use IMAP4_SSL for port 993 (standard) or any port when no explicit
    # plaintext preference is indicated; fall back to plain IMAP4 otherwise.
    if port == 143:
        conn = imaplib.IMAP4(host, port)
        conn.starttls()
    else:
        conn = imaplib.IMAP4_SSL(host, port)
    username = imap.get("username", "")
    password = imap.get("password", "")
    conn.login(username, password)
    return conn


def _smtp_connect() -> smtplib.SMTP | smtplib.SMTP_SSL:
    smtp = _EMAIL_CONFIG.get("smtp", {})
    host = smtp.get("host", "")
    port = int(smtp.get("port", 587))
    conn_type = smtp.get("type", "starttls").lower()
    if not host:
        raise RuntimeError("Email SMTP host is not configured.")
    if conn_type == "tls":
        conn: smtplib.SMTP | smtplib.SMTP_SSL = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        conn = smtplib.SMTP(host, port, timeout=30)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()
    conn.login(smtp.get("username", ""), smtp.get("password", ""))
    return conn


def _decode_str(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    parts = _decode_header(value)
    decoded_parts: list[str] = []
    for raw, charset in parts:
        if isinstance(raw, bytes):
            decoded_parts.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(raw)
    return "".join(decoded_parts)


def _parse_envelope(msg_id: str, raw: bytes) -> dict[str, str]:
    msg = _email_stdlib.message_from_bytes(raw)
    return {
        "id": msg_id,
        "subject": _decode_str(msg.get("Subject")),
        "from": _decode_str(msg.get("From")),
        "to": _decode_str(msg.get("To")),
        "date": _decode_str(msg.get("Date")),
    }


def _get_text_body(raw: bytes) -> str:
    msg = _email_stdlib.message_from_bytes(raw)
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace") if payload else ""
            if ct == "text/html":
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace") if payload else ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _not_configured() -> str:
    return (
        "Email is not configured or not enabled. "
        "Set email.enabled: true and fill in the IMAP/SMTP settings in your config.yaml."
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

class ListEmails(Tool):
    name = "list_emails"
    description = (
        "List emails in an IMAP mailbox folder. Returns a summary of each message "
        "(ID, subject, from, date). Use read_email to fetch the full body."
    )
    parameters = {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "Mailbox folder to list (e.g. INBOX, Sent, Drafts). Default: INBOX.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of messages to return, most recent first. Default 20.",
            },
        },
        "required": [],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        folder: str = "INBOX",
        limit: int = 20,
        **_: Any,
    ) -> str:
        if not _EMAIL_CONFIG.get("enabled", False):
            return _not_configured()
        try:
            imap = _imap_connect()
            try:
                imap.select(folder, readonly=True)
                _, data = imap.search(None, "ALL")
                ids = data[0].split() if data[0] else []
                ids = ids[-limit:]  # most recent last; reverse for recency
                ids = list(reversed(ids))
                if not ids:
                    return f"No messages in {folder}."
                results: list[str] = []
                for msg_id in ids:
                    _, msg_data = imap.fetch(msg_id, "(RFC822.HEADER)")
                    if msg_data and msg_data[0]:
                        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                        env = _parse_envelope(msg_id.decode(), raw)
                        results.append(
                            f"ID {env['id']} | {env['date']} | From: {env['from']} | {env['subject']}"
                        )
                return f"Messages in {folder} ({len(results)} shown):\n" + "\n".join(results)
            finally:
                imap.logout()
        except Exception as exc:
            return f"Error listing emails: {exc}"


class ReadEmail(Tool):
    name = "read_email"
    description = (
        "Read the full content of an email by its IMAP message ID. "
        "Returns headers and body text."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "IMAP message sequence number or UID as returned by list_emails.",
            },
            "folder": {
                "type": "string",
                "description": "Mailbox folder containing the message. Default: INBOX.",
            },
        },
        "required": ["message_id"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        message_id: str,
        folder: str = "INBOX",
        **_: Any,
    ) -> str:
        if not _EMAIL_CONFIG.get("enabled", False):
            return _not_configured()
        try:
            imap = _imap_connect()
            try:
                imap.select(folder, readonly=True)
                _, msg_data = imap.fetch(message_id.encode(), "(RFC822)")
                if not msg_data or not msg_data[0]:
                    return f"Message {message_id} not found in {folder}."
                raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                env = _parse_envelope(message_id, raw)
                body = _get_text_body(raw)
                return (
                    f"From: {env['from']}\n"
                    f"To: {env['to']}\n"
                    f"Date: {env['date']}\n"
                    f"Subject: {env['subject']}\n"
                    f"\n{body}"
                )
            finally:
                imap.logout()
        except Exception as exc:
            return f"Error reading email {message_id}: {exc}"


class SendEmail(Tool):
    name = "send_email"
    description = (
        "Compose and send an email via SMTP. "
        "Returns a confirmation or error message."
    )
    parameters = {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": "Recipient email address (or comma-separated list).",
            },
            "subject": {
                "type": "string",
                "description": "Email subject line.",
            },
            "body": {
                "type": "string",
                "description": "Plain-text body of the email.",
            },
            "cc": {
                "type": "string",
                "description": "Optional CC addresses (comma-separated).",
            },
        },
        "required": ["to", "subject", "body"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        **_: Any,
    ) -> str:
        if not _EMAIL_CONFIG.get("enabled", False):
            return _not_configured()
        try:
            sender = _EMAIL_CONFIG.get("address", _EMAIL_CONFIG.get("smtp", {}).get("username", ""))
            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = to
            msg["Subject"] = subject
            if cc:
                msg["Cc"] = cc
            msg.attach(MIMEText(body, "plain", "utf-8"))

            recipients = [a.strip() for a in to.split(",")]
            if cc:
                recipients += [a.strip() for a in cc.split(",")]

            smtp = _smtp_connect()
            try:
                smtp.sendmail(sender, recipients, msg.as_string())
            finally:
                smtp.quit()
            return f"Email sent to {to} with subject '{subject}'."
        except Exception as exc:
            return f"Error sending email: {exc}"


class SearchEmails(Tool):
    name = "search_emails"
    description = (
        "Search for emails in an IMAP folder using a keyword or IMAP search criteria. "
        "Returns a list of matching messages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search text. Searches subject and body. "
                    "For advanced IMAP criteria (e.g. 'FROM user@example.com', "
                    "'SINCE 01-Jan-2024', 'UNSEEN'), prefix with 'imap:' "
                    "(e.g. 'imap:FROM user@example.com UNSEEN')."
                ),
            },
            "folder": {
                "type": "string",
                "description": "Folder to search. Default: INBOX.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum results to return. Default 20.",
            },
        },
        "required": ["query"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        query: str,
        folder: str = "INBOX",
        limit: int = 20,
        **_: Any,
    ) -> str:
        if not _EMAIL_CONFIG.get("enabled", False):
            return _not_configured()
        try:
            imap = _imap_connect()
            try:
                imap.select(folder, readonly=True)
                if query.startswith("imap:"):
                    criteria = query[5:].strip()
                else:
                    safe = query.replace('"', '')
                    criteria = f'TEXT "{safe}"'
                _, data = imap.search(None, criteria)
                ids = data[0].split() if data[0] else []
                ids = list(reversed(ids))[:limit]
                if not ids:
                    return f"No messages matching '{query}' in {folder}."
                results: list[str] = []
                for msg_id in ids:
                    _, msg_data = imap.fetch(msg_id, "(RFC822.HEADER)")
                    if msg_data and msg_data[0]:
                        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                        env = _parse_envelope(msg_id.decode(), raw)
                        results.append(
                            f"ID {env['id']} | {env['date']} | From: {env['from']} | {env['subject']}"
                        )
                return (
                    f"Search results for '{query}' in {folder} ({len(results)} found):\n"
                    + "\n".join(results)
                )
            finally:
                imap.logout()
        except Exception as exc:
            return f"Error searching emails: {exc}"


class MoveEmail(Tool):
    name = "move_email"
    description = (
        "Move an email from one IMAP folder to another. "
        "Copies the message to the destination folder then deletes it from the source."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "IMAP message sequence number as returned by list_emails or search_emails.",
            },
            "source_folder": {
                "type": "string",
                "description": "Source folder. Default: INBOX.",
            },
            "destination_folder": {
                "type": "string",
                "description": "Destination folder (e.g. Archive, Spam, Work/Projects).",
            },
        },
        "required": ["message_id", "destination_folder"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        message_id: str,
        destination_folder: str,
        source_folder: str = "INBOX",
        **_: Any,
    ) -> str:
        if not _EMAIL_CONFIG.get("enabled", False):
            return _not_configured()
        try:
            imap = _imap_connect()
            try:
                imap.select(source_folder)
                result = imap.copy(message_id.encode(), destination_folder)
                if result[0] != "OK":
                    return f"Failed to copy message to {destination_folder}: {result}"
                imap.store(message_id.encode(), "+FLAGS", "\\Deleted")
                imap.expunge()
                return f"Message {message_id} moved from {source_folder} to {destination_folder}."
            finally:
                imap.logout()
        except Exception as exc:
            return f"Error moving email: {exc}"


class DeleteEmail(Tool):
    name = "delete_email"
    description = (
        "Delete an email by moving it to the Trash folder, "
        "or permanently delete it if permanent=true."
    )
    parameters = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "IMAP message sequence number.",
            },
            "folder": {
                "type": "string",
                "description": "Folder containing the message. Default: INBOX.",
            },
            "permanent": {
                "type": "boolean",
                "description": "If true, permanently delete instead of moving to Trash. Default false.",
            },
        },
        "required": ["message_id"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        message_id: str,
        folder: str = "INBOX",
        permanent: bool = False,
        **_: Any,
    ) -> str:
        if not _EMAIL_CONFIG.get("enabled", False):
            return _not_configured()
        try:
            imap = _imap_connect()
            try:
                imap.select(folder)
                if permanent:
                    imap.store(message_id.encode(), "+FLAGS", "\\Deleted")
                    imap.expunge()
                    return f"Message {message_id} permanently deleted from {folder}."
                else:
                    # Try common trash folder names
                    for trash in ("Trash", "Deleted Items", "Deleted Messages", "[Gmail]/Trash"):
                        res = imap.copy(message_id.encode(), trash)
                        if res[0] == "OK":
                            imap.store(message_id.encode(), "+FLAGS", "\\Deleted")
                            imap.expunge()
                            return f"Message {message_id} moved to {trash}."
                    return (
                        f"Could not find Trash folder. "
                        f"Use move_email to move it manually, or set permanent=true to delete it."
                    )
            finally:
                imap.logout()
        except Exception as exc:
            return f"Error deleting email: {exc}"
