"""Memory: its own memories. In the line it adds the ones that match what was said; a model remembers, recalls and
forgets through its functions. Finds by meaning with an embedding model (from the pool), else by words."""

from __future__ import annotations

import math
import re
from typing import Any

import aiohttp

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from ..const import LOGGER
from ..message import Message, new_id
from . import Card, register

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
    help = ("Its own memories. In the line it adds those that match what was said (a note); wired from a model and back, "
            "the model remembers, recalls and forgets. An embedding model finds memories by meaning.")
    page = "memory"
    keeps_data = True
    tags = {"recall": "memories that matched"}
    settings_schema = {
        "embedding": {"type": "model", "title": "Embedding model", "default": "",
                      "help": "From Settings → Models (e.g. bge-m3). Empty: memories are found by their words."},
        "in_line": {"type": "bool", "title": "In the line: add matching memories", "default": True},
        "how_many": {"type": "number", "title": "How many at most", "default": 3},
    }
    functions = {
        "remember": {"help": "Keep something for later (a fact, a preference, a plan).",
                     "args": {"text": {"type": "string", "help": "One short sentence.", "required": True}}},
        "recall": {"help": "Memories that match a question.", "args": {"query": {"type": "string", "help": "What to look for.", "required": True}}},
        "forget": {"help": "Delete memories that contain a text (or by id).",
                   "args": {"text": {"type": "string", "help": "Text or id.", "required": True}}},
        "list": {"help": "The newest memories."},
    }

    def default_data(self) -> dict[str, Any]:
        return {"memories": []}

    async def _embed(self, text: str) -> list[float] | None:
        model = self.core.pool.get(self.settings.get("embedding") or "")
        if not model:
            return None
        headers = {"Authorization": f"Bearer {model['api_key']}"} if model.get("api_key") else {}
        try:
            async with async_get_clientsession(self.hass).post(
                    f"{model['base_url'].rstrip('/')}/embeddings", json={"model": model["model"], "input": text}, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp.raise_for_status()
                return (await resp.json())["data"][0]["embedding"]
        except (aiohttp.ClientError, TimeoutError, KeyError, IndexError) as err:
            LOGGER.info("AAC: embedding failed (%s); finding by words", err)
            return None

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

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        if self.settings.get("in_line", True) and message.text:
            found = await self.find(message.text, int(self.settings.get("how_many") or 3))
            message.put(self.id, "note", "Memories that may matter:\n" + "\n".join(f"- {m['text']}" for m in found) if found else "", "recall")
        return None

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        memories = self.data["memories"]
        if function == "remember":
            text = str(args.get("text") or "").strip()
            if not text:
                return {"error": "empty"}
            memories.append({"id": new_id(), "text": text, "time": dt_util.utcnow().isoformat(), "vector": await self._embed(text)})
        elif function == "recall":
            return [{"id": m["id"], "text": m["text"]} for m in await self.find(str(args.get("query") or ""), 5)]
        elif function == "forget":
            key = str(args.get("text") or "").lower()
            self.data["memories"] = [m for m in memories if m["id"] != key and key not in m["text"].lower()]
        elif function == "list":
            return [{"id": m["id"], "text": m["text"], "time": m["time"]} for m in memories[-30:]]
        else:
            return await super().call(function, args, message)
        await self.save()
        self.core.notify("card", domain=self.domain.id, card=self.id)
        return {"done": function, "memories": len(self.data["memories"])}
