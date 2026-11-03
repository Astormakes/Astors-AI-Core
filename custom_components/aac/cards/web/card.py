"""The Web card (docs/v2 page 2): Search the web and read pages, for a model (as tools) or a Relay's script."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import quote_plus, unquote

from custom_components.aac.api import Card, Context, register

VERSION = "1.4.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

TAG = re.compile(r"<[^>]+>")


@register
class Web(Card):
    """Search and read pages."""

    type = "web"
    title = "Web"
    category = "Data"
    help = "Search the web and read pages, for a model (as tools) or a Relay's script."
    settings_schema = {
        "searx": {"type": "text", "title": "SearXNG address (optional)", "default": "",
                  "help": "e.g. http://192.168.1.20:8888; empty: DuckDuckGo's plain page."},
    }
    needs = {"web"}
    functions = {
        "search": {"help": "Search: titles, links and snippets.", "args": {"query": {"type": "string", "help": "What to look for.", "required": True}}},
        "read": {"help": "A page as plain text.", "args": {"url": {"type": "string", "help": "The address.", "required": True}}},
    }

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function not in self.functions:
            return await super().call(function, args, context)
        headers = {"User-Agent": "Mozilla/5.0 (AAC)"}
        try:
            if function == "search":
                query = str(args.get("query") or "")
                if searx := (self.settings.get("searx") or "").rstrip("/"):
                    data = json.loads((await self.fetch(f"{searx}/search", params={"q": query, "format": "json"}))["text"])
                    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")} for r in data.get("results", [])[:8]]
                page = (await self.fetch(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}", headers=headers))["text"]
                found = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
                out = []
                for href, title, snippet in found[:8]:
                    url = unquote(href.split("uddg=")[1].split("&")[0]) if "uddg=" in href else href
                    out.append({"title": html.unescape(TAG.sub("", title)), "url": url, "snippet": html.unescape(TAG.sub("", snippet))})
                return out
            if function == "read":
                page = (await self.fetch(str(args.get("url")), headers=headers))["text"]
                page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
                return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", page))).strip()[:8000]
        except (PermissionError, ConnectionError, ValueError) as err:
            return {"error": str(err)}
        return await super().call(function, args, context)

