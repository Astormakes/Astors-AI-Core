"""The File card (docs/v2 page 2): A Markdown file (<config>/aac/files/<name>.md) with a page in the sidebar. In the
line it appends the newest answer with the time; a model reads, appends and rewrites it."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, register

VERSION = "1.4.0"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class File(Card):
    """A Markdown file."""

    type = "file"
    title = "File"
    category = "Data"
    help = ("A Markdown file (<config>/aac/files/<name>.md) with a page in the sidebar. In the line it appends the newest "
            "answer with the time; a model reads, appends and rewrites it. A dotted wire from 'provides' lets another "
            "card read the file.")
    outputs = {"out": "text", "provides": "read"}
    ports_help = {"provides": "the file's text, for cards that read it (dotted): one entry"}
    tags = {"text": "the file's text (one entry)"}
    ui = True
    text = ""  # the file as last read or written (what 'provides' holds: held() may not touch the disk)
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

    def _write(self, text: str, append: bool) -> str:
        """Write, and the whole file afterwards."""
        self.path().parent.mkdir(parents=True, exist_ok=True)
        with self.path().open("a" if append else "w", encoding="utf-8") as f:
            f.write(text)
        return self._read()

    async def start(self) -> None:
        self.text = (await self.hass.async_add_executor_job(self._read))[-8000:]

    def held(self, port: str) -> Context | None:
        """What a dotted wire from 'provides' reads: the file's text as one entry (its id changes with the text)."""
        if port != "provides":
            return None
        context = Context(origin=self.id)
        if self.text.strip():
            entry = context.add(self.id, "note", self.text, ["text"])
            entry["id"] = hashlib.sha1(f"{self.id}|{self.text}".encode()).hexdigest()[:10]
        return context

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        what = self.settings.get("in_line") or "answer"
        text = context.answer if what == "answer" else context.text if what == "said" else ""
        if text.strip():
            stamp = dt_util.now().strftime("%Y-%m-%d %H:%M")
            self.text = (await self.hass.async_add_executor_job(self._write, f"\n## {stamp}\n\n{text.strip()}\n", True))[-8000:]
        return None

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "read":
            self.text = (await self.hass.async_add_executor_job(self._read))[-8000:]
            return self.text
        if function in ("append", "write"):
            text = str(args.get("text") or "")
            self.text = (await self.hass.async_add_executor_job(self._write, text if text.endswith("\n") else text + "\n", function == "append"))[-8000:]
            self.core.notify("card", domain=self.domain.id, card=self.id)
            return {"done": function}
        return await super().call(function, args, context)

