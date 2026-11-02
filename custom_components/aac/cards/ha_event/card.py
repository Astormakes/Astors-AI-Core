"""The HA event card (docs/v2 page 2): Wakes the chain when an entity changes (e.g. the door opens), with an
instruction; a cooldown keeps it calm."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import Event, callback
from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, register

VERSION = "2.1.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

LOG_SIZE = 60

class AutoCard(Card):
    """A card that starts contexts by itself and keeps a log of what came back."""

    category = "In and out"
    inputs = {"answers": "text"}
    outputs = {"out": "text"}
    ui = True
    keeps_data = True
    tags = {"said": "what woke the chain: its instruction"}

    def default_data(self) -> dict[str, Any]:
        return {"log": []}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._unsubs: list = []
        self._tasks: set[asyncio.Task] = set()

    async def unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    def fire(self, name: str, text: str) -> str:
        """Start a context at the output (in the background); the id of its entry."""
        context = Context()
        fired = context.add(self.tag("said"), text, name=name)
        self._log(name, text, None, fired["id"])
        task = self.hass.async_create_background_task(self.send(context, "out"), f"aac {self.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return fired["id"]

    def _log(self, name: str, text: str, answer: str | None, entry_id: str) -> None:
        self.data["log"] = [*self.data["log"], {"time": dt_util.utcnow().isoformat(), "name": name, "text": text,
                                                 "answer": answer, "id": entry_id}][-LOG_SIZE:]
        self.hass.async_create_task(self.save())
        self.tell()

    def answered(self, context: Context) -> dict[str, Any] | None:
        """The log item a context answers (it holds the entry the card fired), with the answer written in: the newest
        said line or error after it (an error: failed, with why)."""
        for item in reversed(self.data["log"]):
            at = next((i for i, e in enumerate(context.entries) if e["id"] == item.get("id")), None)
            if at is None:
                continue
            found = [e for e in context.entries[at + 1:] if e["tag"].endswith((".said", ".error")) and e["text"].strip()]
            if found:
                item["answer"] = found[-1]["text"].strip()
                if found[-1]["tag"].endswith(".error"):
                    item["failed"] = item["answer"][:200]
            return item
        return None

    async def handle(self, context: Context, port: str) -> None:
        """What the chain concluded."""
        if port != "answers" or context.streaming or self.answered(context) is None:
            return
        await self.save()
        self.tell()


@register
class HaEvent(AutoCard):
    """Entity changes."""

    type = "ha_event"
    title = "HA event"
    help = "Wakes the chain when an entity changes (e.g. the door opens), with an instruction; a cooldown keeps it calm."
    needs = {"entities"}  # the entities it watches, at read
    settings_schema = {
        "watches": {"type": "watches", "title": "Watches", "default": [],
                    "help": "Each: a name, an entity, the state that counts (empty: any change), what to do, a cooldown (s)."},
    }

    async def start(self) -> None:
        entities = sorted({w["entity_id"] for w in self.settings.get("watches") or [] if w.get("entity_id")})
        self._last: dict[str, datetime] = {}
        if entities:
            self._unsubs.append(self.ha.watch(entities, self._changed))

    @callback
    def _changed(self, event: Event) -> None:
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or (old is not None and old.state == new.state):
            return
        now = dt_util.utcnow()
        for w in self.settings.get("watches") or []:
            if w.get("entity_id") != new.entity_id or (w.get("to") and w["to"] != new.state):
                continue
            key = f"{w['name']}:{new.entity_id}"
            if now - self._last.get(key, datetime.min.replace(tzinfo=now.tzinfo)) < timedelta(seconds=int(w.get("cooldown") or 300)):
                continue
            self._last[key] = now
            name = new.attributes.get("friendly_name", new.entity_id)
            self.fire(w["name"], f"{name} changed from {old.state if old else '?'} to {new.state}: {w.get('text', '')}")

