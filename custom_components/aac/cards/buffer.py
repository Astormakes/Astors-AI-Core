"""Buffer: keeps the last N entries of the tags you choose (e.g. the room's last heard lines) and writes them into
each context as one entry; a model or a Relay's script can read and change it through its functions."""

from __future__ import annotations

import hashlib
from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "1.1.1"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

KIND = {"start": "system", "note": "note", "after": "trailer"}


@register
class Buffer(Card):
    """The last lines."""

    type = "buffer"
    title = "Buffer"
    category = "Logic"
    help = ("Keeps the last lines of the tags you pick and adds them to each context as one entry; a model can read and "
            "edit it. A dotted wire from 'provides' lets another card read its lines.")
    outputs = {"out": "text", "provides": "read"}
    ports_help = {"in": "a context: its newest matching line is kept", "out": "the context, with the lines added",
                  "provides": "its lines, for cards that read them (dotted): one entry per line, oldest first"}
    tags = {"lines": "the kept lines (one entry)"}
    keeps_data = True
    settings_schema = {
        "tags": {"type": "tags", "title": "Keeps entries tagged", "default": [],
                 "help": "Empty: what people said (kind 'said')."},
        "size": {"type": "number", "title": "Slots", "default": 10},
        "position": {"type": "select", "title": "Writes them", "default": "note",
                     "options": [["note", "as a note before the turn"], ["start", "at the start"],
                                 ["after", "after the turn"], ["nothing", "nowhere (only for its functions)"]]},
    }
    values = {"text": "its lines, one per line", "lines": "its lines (a list)", "count": "how many lines"}
    functions = {
        "read": {"help": "What the buffer holds (numbered, newest last)."},
        "add": {"help": "Put a line in.", "args": {"text": {"type": "string", "help": "The line.", "required": True}}},
        "remove": {"help": "Take one line out by its number.", "args": {"number": {"type": "integer", "help": "From read.", "required": True}}},
        "remove_matching": {"help": "Take out every line that contains a text.",
                            "args": {"text": {"type": "string", "help": "The text.", "required": True}}},
        "clear": {"help": "Empty the buffer."},
    }

    def current(self) -> dict[str, Any]:
        lines = list(self.data.get("lines") or [])
        return {"text": "\n".join(map(str, lines)), "lines": lines, "count": len(lines)}

    def default_data(self) -> dict[str, Any]:
        return {"lines": []}

    def held(self, port: str) -> Context | None:
        """What a dotted wire from 'provides' reads: one entry per line (an entry keeps its id while the line is there)."""
        if port != "provides":
            return None
        context, seen = Context(), {}
        for text in self.data.get("lines") or []:
            seen[text] = seen.get(text, 0) + 1
            line = context.add(self.id, "note", text, ["lines"])
            line["id"] = hashlib.sha1(f"{self.id}|{text}|{seen[text]}".encode()).hexdigest()[:10]
        return context

    def _keep(self, text: str) -> None:
        lines = [*self.data["lines"], text][-max(1, int(self.settings.get("size") or 10)):]
        self.data["lines"] = lines

    def block(self) -> str:
        """The kept lines as one text."""
        lines = self.data["lines"]
        return (f"{self.name} (newest last):\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(lines, 1))) if lines else ""

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        tags = self.settings.get("tags") or []
        newest = next((e for e in reversed(context.entries)
                       if (any(t in e["tags"] for t in tags) if tags else e["kind"] == "said") and e["text"].strip()), None)
        if newest and (not self.data["lines"] or self.data["lines"][-1] != newest["text"].strip()):
            self._keep(newest["text"].strip())
            await self.save()
        if (position := self.settings.get("position") or "note") in KIND:
            context.put(self.id, KIND[position], self.block(), "lines")
        return None

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        lines = self.data["lines"]
        if function == "add" and str(args.get("text") or "").strip():
            self._keep(str(args["text"]).strip())
        elif function == "remove":
            n = int(args.get("number") or 0)
            if 1 <= n <= len(lines):
                del lines[n - 1]
        elif function == "remove_matching":
            word = str(args.get("text") or "").lower()
            self.data["lines"] = [t for t in lines if word not in t.lower()]
        elif function == "clear":
            self.data["lines"] = []
        elif function != "read":
            return await super().call(function, args, context)
        await self.save()
        return {"lines": self.data["lines"]}
