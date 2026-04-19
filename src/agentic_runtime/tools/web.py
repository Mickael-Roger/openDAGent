from __future__ import annotations

import os
import sqlite3
from typing import Any

import httpx

from . import Tool


class WebFetch(Tool):
    name = "web_fetch"
    description = (
        "Fetch the text content of a URL. Returns the raw text body (HTML stripped "
        "to readable text where possible). Useful for reading documentation, articles, "
        "or any web resource."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters to return. Default 8000.",
            },
        },
        "required": ["url"],
    }

    def run(
        self,
        conn: sqlite3.Connection,
        task: dict[str, Any],
        *,
        url: str,
        max_chars: int = 8000,
        **_: Any,
    ) -> str:
        try:
            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=20,
                headers={"User-Agent": "openDAGent/1.0 (web_fetch tool)"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return f"Error: HTTP {e.response.status_code} for {url}"
        except Exception as e:
            return f"Error fetching {url}: {e}"

        content_type = resp.headers.get("content-type", "")
        text = resp.text

        if "html" in content_type:
            text = _strip_html(text)

        text = text[:max_chars]
        return f"[Content from {url}]\n\n{text}"


def _strip_html(html: str) -> str:
    """Very lightweight HTML-to-text: remove tags and collapse whitespace."""
    import re
    # Remove script/style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                          ("&nbsp;", " "), ("&#39;", "'"), ("&quot;", '"')]:
        html = html.replace(entity, char)
    # Collapse whitespace
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


class WebSearch(Tool):
    name = "web_search"
    description = (
        "Search the web and return a list of relevant results (title, URL, snippet). "
        "Uses the Brave Search API. Requires BRAVE_API_KEY environment variable."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
            },
            "count": {
                "type": "integer",
                "description": "Number of results to return (1–20). Default 8.",
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
        count: int = 8,
        **_: Any,
    ) -> str:
        api_key = os.environ.get("BRAVE_API_KEY", "")
        if not api_key:
            return "Error: BRAVE_API_KEY environment variable is not set."

        count = max(1, min(20, int(count)))
        try:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return f"Error: Brave Search HTTP {e.response.status_code}"
        except Exception as e:
            return f"Error calling Brave Search: {e}"

        data = resp.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return "No results found."

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   URL: {r.get('url', '')}")
            desc = r.get("description", "")
            if desc:
                lines.append(f"   {desc}")
            lines.append("")
        return "\n".join(lines)
