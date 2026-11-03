"""Memory: its own memories. In the line it adds the ones that match what was said; a model remembers, recalls and
forgets through its functions. Finds by meaning with an embedding model from its list, else by words."""

from __future__ import annotations

import math
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, new_id, register

VERSION = "2.0.2"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

WORD = re.compile(r"\w{3,}")


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@register
class Memory(Card):
    """Memories."""

    type = "memory"
    title = "Memory"
    category = "Data"
    help = ("Its own memories. In the line it adds those that match the newest entry (one entry at the end) and sends the "
            "context on; wired from a model and back, the model remembers, recalls and forgets. An embedding model finds "
            "memories by meaning.")
    ui = True
    keeps_data = True
    tags = {"recall": "memories that matched"}
    ports_help = {"in": "in the line: it adds the memories that match the newest entry (not while the context waits for "
                        "a call); as a model's tool (wired from the model and back) the model remembers, recalls and forgets",
                  "out": "the context with the matching memories, or back to the model"}
    settings_schema = {
        "models": {"type": "modellist", "tag": "embeddings", "title": "Embedding models (in order)", "default": [],
                   "help": "Find memories by meaning (e.g. bge-m3). The list is run through from the top: a free model takes it; a busy one queues it if its Queue is ticked, then the next is tried. Empty: every model with the tag, queued. No embedding model at all: by their words."},
        "in_line": {"type": "bool", "title": "In the line: add matching memories", "default": True},
        "how_many": {"type": "number", "title": "How many at most", "default": 3},
    }
    values = {"count": "how many memories it keeps"}
    functions = {
        "remember": {"help": "Keep something for later (a fact, a preference, a plan).",
                     "args": {"text": {"type": "string", "help": "One short sentence.", "required": True}}},
        "recall": {"help": "Memories that match a question.", "args": {"query": {"type": "string", "help": "What to look for.", "required": True}}},
        "forget": {"help": "Delete one memory, by its id (from recall or list) or its exact text.",
                   "args": {"text": {"type": "string", "help": "The memory's id or its whole text.", "required": True}}},
        "list": {"help": "The newest memories."},
    }

    def current(self) -> dict[str, Any]:
        return {"count": len(self.data.get("memories") or [])}

    def default_data(self) -> dict[str, Any]:
        return {"memories": []}

    async def _embed(self, text: str) -> list[float] | None:
        """The text's vector from its list of embedding models (none answers: None, found by words)."""
        return await self.core.models.embed(self.settings.get("models"), text)

    async def find(self, query: str, limit: int) -> list[dict[str, Any]]:
        """The best matches."""
        memories = self.data["memories"]
        if not memories or not query.strip():
            return []
        vector = await self._embed(query)
        if vector:
            scored = [(_cos(vector, m["vector"]), m) for m in memories if m.get("vector")]
            scored = [x for x in scored if x[0] > 0.45]
        else:
            words = set(WORD.findall(query.lower()))
            scored = [(len(words & set(WORD.findall(m["text"].lower()))), m) for m in memories]
            scored = [x for x in scored if x[0] > 0]
        return [m for _, m in sorted(scored, key=lambda x: -x[0])[:limit]]

    async def handle(self, context: Context, port: str) -> None:
        newest = context.newest
        if not self.settings.get("in_line", True) or context.streaming or context.waiting or newest is None or not newest["text"].strip():
            return
        for old in context.find(self.tag("recall")):
            context.remove(old)
        if found := await self.find(newest["text"], int(self.settings.get("how_many") or 3)):
            context.add(self.tag("recall"), "Memories that may matter:\n" + "\n".join(f"- {m['text']}" for m in found))
        await self.send(context, "out")

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        memories = self.data["memories"]
        if function == "remember":
            text = str(args.get("text") or "").strip()
            if not text:
                return {"error": "empty"}
            memories.append({"id": new_id(), "text": text, "time": dt_util.utcnow().isoformat(), "vector": await self._embed(text)})
        elif function == "recall":
            return [{"id": m["id"], "text": m["text"]} for m in await self.find(str(args.get("query") or ""), 5)]
        elif function == "forget":  # one memory, by its id or its whole text: never everything that contains a word
            key = str(args.get("text") or "").strip()
            gone = [m for m in memories if m["id"] == key or m["text"].strip().lower() == key.lower()]
            if not gone:
                return {"error": "no memory with that id or text; recall or list give the ids",
                        "close": [{"id": m["id"], "text": m["text"]} for m in await self.find(key, 5)]}
            self.data["memories"] = [m for m in memories if m not in gone]
        elif function == "list":
            return [{"id": m["id"], "text": m["text"], "time": m["time"]} for m in memories[-30:]]
        else:
            return await super().call(function, args, context)
        await self.save()
        self.tell()
        return {"done": function, "memories": len(self.data["memories"])}
