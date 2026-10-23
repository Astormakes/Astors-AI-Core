"""Small single-purpose cards: File (a Markdown file), Notify (a phone notification through HA), Timer (hold a
message for a while), Web (search and read pages)."""

from __future__ import annotations

import asyncio
import html
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote_plus, unquote

import aiohttp

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from ..message import Message
from . import Card, register


@register
class File(Card):
    """A Markdown file."""

    type = "file"
    title = "File"
    category = "Data"
    help = ("A Markdown file (<config>/aac/files/<name>.md) with a page in the sidebar. In the line it appends the newest "
            "answer with the time; a model reads, appends and rewrites it.")
    page = "file"
    settings_schema = {
        "name": {"type": "text", "title": "File name", "default": "notes"},
        "in_line": {"type": "select", "title": "In the line it appends", "default": "answer",
                    "options": [["answer", "the newest answer"], ["said", "the newest said line"], ["nothing", "nothing"]]},
    }
    functions = {
        "read": {"help": "The whole file."},
        "append": {"help": "Add text at the end (Markdown).", "args": {"text": {"type": "string", "help": "The text.", "required": True}}},
        "write": {"help": "Replace the whole file (Markdown).", "args": {"text": {"type": "string", "help": "The text.", "required": True}}},
    }

    def path(self) -> Path:
        """Where the file lives (a plain name, no folders)."""
        name = re.sub(r"[^\w\- ]", "", str(self.settings.get("name") or "notes")).strip() or "notes"
        return Path(self.hass.config.path("aac", "files", f"{name}.md"))

    def _read(self) -> str:
        return self.path().read_text(encoding="utf-8") if self.path().exists() else ""

    def _write(self, text: str, append: bool) -> None:
        self.path().parent.mkdir(parents=True, exist_ok=True)
        with self.path().open("a" if append else "w", encoding="utf-8") as f:
            f.write(text)

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        what = self.settings.get("in_line") or "answer"
        text = message.answer if what == "answer" else message.text if what == "said" else ""
        if text.strip():
            stamp = dt_util.now().strftime("%Y-%m-%d %H:%M")
            await self.hass.async_add_executor_job(self._write, f"\n## {stamp}\n\n{text.strip()}\n", True)
        return None

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        if function == "read":
            return (await self.hass.async_add_executor_job(self._read))[-8000:]
        if function in ("append", "write"):
            text = str(args.get("text") or "")
            await self.hass.async_add_executor_job(self._write, text if text.endswith("\n") else text + "\n", function == "append")
            self.core.notify("card", domain=self.domain.id, card=self.id)
            return {"done": function}
        return await super().call(function, args, message)


@register
class Notify(Card):
    """A phone notification."""

    type = "notify"
    title = "Notify"
    category = "Home Assistant"
    help = "Sends a notification through Home Assistant (your phone's notify service). In the line: the newest answer."
    settings_schema = {
        "service": {"type": "text", "title": "Notify service", "default": "notify.notify", "help": "e.g. notify.mobile_app_my_phone"},
        "in_line": {"type": "bool", "title": "In the line: send the newest answer", "default": True},
        "title": {"type": "text", "title": "Title", "default": "AAC"},
    }
    functions = {"send": {"help": "Send a notification.", "args": {"message": {"type": "string", "help": "The text.", "required": True},
                                                                  "title": {"type": "string", "help": "A title."}}}}

    async def _send(self, text: str, title: str | None = None) -> dict[str, Any]:
        domain, _, service = str(self.settings.get("service") or "notify.notify").partition(".")
        if not self.hass.services.has_service(domain, service):
            return {"error": f"There is no service {domain}.{service}"}
        await self.hass.services.async_call(domain, service, {"message": text, "title": title or self.settings.get("title") or "AAC"}, blocking=True)
        return {"sent": True}

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        if self.settings.get("in_line", True) and message.answer.strip() and message.answer.strip().lower() not in ("nothing", "nothing."):
            await self._send(message.answer.strip())
        return None

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        if function == "send":
            return await self._send(str(args.get("message") or ""), args.get("title"))
        return await super().call(function, args, message)


@register
class Timer(Card):
    """Holds a message for a while."""

    type = "timer"
    title = "Timer"
    category = "Logic"
    help = "Holds the message for some seconds, then sends it on (e.g. to wait for a reply); a stop ends the wait."
    settings_schema = {"seconds": {"type": "number", "title": "Seconds", "default": 5}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._waits: set[asyncio.Task] = set()

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        task = asyncio.current_task()
        self._waits.add(task)
        try:
            await asyncio.sleep(max(0.0, float(self.settings.get("seconds") or 0)))
        except asyncio.CancelledError:
            return []
        finally:
            self._waits.discard(task)
        return None

    async def stop(self) -> None:
        for task in list(self._waits):
            task.cancel()


TAG = re.compile(r"<[^>]+>")


@register
class Web(Card):
    """Search and read pages."""

    type = "web"
    title = "Web"
    category = "Data"
    help = "Search the web and read pages, for a model (as tools) or a Conditional card."
    settings_schema = {
        "searx": {"type": "text", "title": "SearXNG address (optional)", "default": "",
                  "help": "e.g. http://192.168.1.20:8888; empty: DuckDuckGo's plain page."},
    }
    functions = {
        "search": {"help": "Search: titles, links and snippets.", "args": {"query": {"type": "string", "help": "What to look for.", "required": True}}},
        "read": {"help": "A page as plain text.", "args": {"url": {"type": "string", "help": "The address.", "required": True}}},
    }

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        session = async_get_clientsession(self.hass)
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {"User-Agent": "Mozilla/5.0 (AAC)"}
        try:
            if function == "search":
                query = str(args.get("query") or "")
                if searx := (self.settings.get("searx") or "").rstrip("/"):
                    async with session.get(f"{searx}/search", params={"q": query, "format": "json"}, timeout=timeout) as resp:
                        data = await resp.json()
                    return [{"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")} for r in data.get("results", [])[:8]]
                async with session.get(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}", headers=headers, timeout=timeout) as resp:
                    page = await resp.text()
                found = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
                out = []
                for href, title, snippet in found[:8]:
                    url = unquote(href.split("uddg=")[1].split("&")[0]) if "uddg=" in href else href
                    out.append({"title": html.unescape(TAG.sub("", title)), "url": url, "snippet": html.unescape(TAG.sub("", snippet))})
                return out
            if function == "read":
                async with session.get(str(args.get("url")), headers=headers, timeout=timeout) as resp:
                    page = await resp.text()
                page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
                return re.sub(r"\s+", " ", html.unescape(TAG.sub(" ", page))).strip()[:8000]
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            return {"error": str(err)}
        return await super().call(function, args, message)
