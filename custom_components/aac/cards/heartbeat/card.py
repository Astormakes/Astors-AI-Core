"""The Heartbeat card (docs/v2 page 2): Wakes the chain every N minutes (outside quiet hours) to check in: anything
worth saying? Silence stays silent."""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from custom_components.aac.api import LOGGER, Card, Context, register

VERSION = "1.1.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

LOG_SIZE = 60

SILENT = ("nothing", "nothing.", "")


class AutoCard(Card):
    """A card that starts messages by itself and keeps a log of what came back."""

    category = "In and out"
    inputs = {"answers": "text"}
    outputs = {"out": "text"}
    ui = True
    keeps_data = True
    tags = {"fired": "what woke the chain"}

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

    def fire(self, name: str, text: str) -> None:
        """Start a context at the output (in the background)."""
        context = Context(origin=self.id)
        context.add(self.id, "said", text, ["fired"], name=name)
        self._log(name, text, None, context.id)
        task = self.hass.async_create_background_task(self.domain.flow.send(context, self.id, "out"), f"aac {self.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _log(self, name: str, text: str, answer: str | None, message_id: str) -> None:
        self.data["log"] = [*self.data["log"], {"time": dt_util.utcnow().isoformat(), "name": name, "text": text,
                                                 "answer": answer, "id": message_id}][-LOG_SIZE:]
        self.hass.async_create_task(self.save())
        self.core.notify("card", domain=self.domain.id, card=self.id)

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        """What the chain concluded."""
        answer = context.answer.strip()
        for item in reversed(self.data["log"]):
            if item.get("id") == context.id:
                item["answer"] = "" if answer.lower() in SILENT else answer
                break
        await self.save()
        self.core.notify("card", domain=self.domain.id, card=self.id)
        return []


@register
class Heartbeat(AutoCard):
    """Every N minutes."""

    type = "heartbeat"
    title = "Heartbeat"
    help = "Wakes the chain every N minutes (outside quiet hours) to check in: anything worth saying? Silence stays silent."
    settings_schema = {
        "every": {"type": "number", "title": "Every (minutes)", "default": 30},
        "quiet_from": {"type": "text", "title": "Quiet from (HH:MM)", "default": "22:00"},
        "quiet_to": {"type": "text", "title": "Quiet until (HH:MM)", "default": "08:00"},
        "text": {"type": "longtext", "title": "Instruction",
                 "default": "A check-in, nobody asked anything. Is there something the owner should know right now? If not, answer exactly NOTHING."},
    }

    async def start(self) -> None:
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))

    def _quiet(self, local: datetime) -> bool:
        try:
            a, b = dtime.fromisoformat(self.settings.get("quiet_from") or "22:00"), dtime.fromisoformat(self.settings.get("quiet_to") or "08:00")
        except ValueError:
            return False
        now = local.time()
        return (a <= now or now < b) if a > b else (a <= now < b) if a != b else False

    @callback
    def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        every = max(1, int(self.settings.get("every") or 30))
        if (local.hour * 60 + local.minute) % every or self._quiet(local):
            return
        self.fire("Heartbeat", self.settings.get("text") or "")
        LOGGER.debug("AAC: heartbeat %s", self.id)

