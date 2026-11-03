"""The Heartbeat card (docs/v2 page 2): Wakes the chain every N minutes (outside quiet hours) to check in: anything
worth saying? Silence stays silent."""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, register

VERSION = "2.0.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

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
        task = self.task(self.send(context, "out"), background=True)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return fired["id"]

    def _log(self, name: str, text: str, answer: str | None, entry_id: str) -> None:
        self.data["log"] = [*self.data["log"], {"time": dt_util.utcnow().isoformat(), "name": name, "text": text,
                                                 "answer": answer, "id": entry_id}][-LOG_SIZE:]
        self.task(self.save())
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
        self._unsubs.append(self.every_minute(self._tick))

    def _quiet(self, local: datetime) -> bool:
        try:
            a, b = dtime.fromisoformat(self.settings.get("quiet_from") or "22:00"), dtime.fromisoformat(self.settings.get("quiet_to") or "08:00")
        except ValueError:
            return False
        now = local.time()
        return (a <= now or now < b) if a > b else (a <= now < b) if a != b else False

    def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        every = max(1, int(self.settings.get("every") or 30))
        if (local.hour * 60 + local.minute) % every or self._quiet(local):
            return
        self.fire("Heartbeat", self.settings.get("text") or "")

