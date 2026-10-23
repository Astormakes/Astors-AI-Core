"""Cards that wake a chain on their own (docs/v2 page 2): Schedule (times), HA event (entity changes), Heartbeat
(every N minutes). Each starts a message at its output; what the model concluded comes back into `answers` and goes
into the card's log (its page in the sidebar). "NOTHING" or an empty answer is logged as quiet."""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timedelta
from typing import Any

from homeassistant.core import Event, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from ..const import LOGGER
from ..message import Message
from . import Card, register

LOG_SIZE = 60
SILENT = ("nothing", "nothing.", "")
DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


class AutoCard(Card):
    """A card that starts messages by itself and keeps a log of what came back."""

    category = "In and out"
    inputs = {"answers": "text"}
    outputs = {"out": "text"}
    page = "log"
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
        """Start a message at the output (in the background)."""
        message = Message(origin=self.id)
        message.add(self.id, "said", text, ["fired"], name=name)
        self._log(name, text, None, message.id)
        task = self.hass.async_create_background_task(self.domain.flow.send(message, self.id, "out"), f"aac {self.id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _log(self, name: str, text: str, answer: str | None, message_id: str) -> None:
        self.data["log"] = [*self.data["log"], {"time": dt_util.utcnow().isoformat(), "name": name, "text": text,
                                                 "answer": answer, "id": message_id}][-LOG_SIZE:]
        self.hass.async_create_task(self.save())
        self.core.notify("card", domain=self.domain.id, card=self.id)

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        """What the chain concluded."""
        answer = message.answer.strip()
        for item in reversed(self.data["log"]):
            if item.get("id") == message.id:
                item["answer"] = "" if answer.lower() in SILENT else answer
                break
        await self.save()
        self.core.notify("card", domain=self.domain.id, card=self.id)
        return []


@register
class Schedule(AutoCard):
    """Times."""

    type = "schedule"
    title = "Schedule"
    help = ("Wakes the chain at set times with an instruction (an alarm, a reminder, a routine). A model can add and "
            "remove entries itself through its functions.")
    settings_schema = {
        "entries": {"type": "schedule", "title": "Entries", "default": [],
                    "help": "Each: a name, a time (HH:MM), the weekdays (none = every day; or a date for once) and what to do."},
    }
    functions = {
        "list": {"help": "The entries."},
        "add": {"help": "A new entry (e.g. a reminder).",
                "args": {"name": {"type": "string", "help": "Its name.", "required": True},
                         "at": {"type": "string", "help": "HH:MM.", "required": True},
                         "text": {"type": "string", "help": "What to do then.", "required": True},
                         "weekdays": {"type": "array", "items": "integer", "help": "0 = Monday … 6 = Sunday; empty: every day."},
                         "date": {"type": "string", "help": "YYYY-MM-DD for once (then it goes away)."}}},
        "remove": {"help": "Take an entry out.", "args": {"name": {"type": "string", "help": "Its name.", "required": True}}},
        "run": {"help": "Run an entry now.", "args": {"name": {"type": "string", "help": "Its name.", "required": True}}},
    }

    async def start(self) -> None:
        self._unsubs.append(async_track_time_change(self.hass, self._tick, second=0))

    @callback
    def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        hm, day, date = local.strftime("%H:%M"), local.weekday(), local.date().isoformat()
        gone = []
        for entry in self.settings.get("entries") or []:
            if not entry.get("enabled", True) or entry.get("at") != hm:
                continue
            if entry.get("date"):
                if entry["date"] != date:
                    continue
                gone.append(entry["name"])
            elif entry.get("weekdays") and day not in entry["weekdays"]:
                continue
            self.fire(entry["name"], f"Scheduled ({entry['name']}, {hm}): {entry.get('text', '')}")
        if gone:  # a one-time entry is done
            self.hass.async_create_task(self.domain.update_card(self.id, settings={"entries": [
                e for e in self.settings.get("entries") or [] if e["name"] not in gone]}))

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        entries = list(self.settings.get("entries") or [])
        if function == "list":
            return entries
        if function == "add":
            at = str(args.get("at", ""))
            try:
                dtime.fromisoformat(at)
            except ValueError:
                return {"error": "at must be HH:MM"}
            entries = [e for e in entries if e["name"] != args["name"]] + [{
                "name": args["name"], "at": at[:5], "text": args.get("text", ""), "weekdays": args.get("weekdays") or [],
                **({"date": args["date"]} if args.get("date") else {}), "enabled": True}]
        elif function == "remove":
            entries = [e for e in entries if e["name"].lower() != str(args.get("name", "")).lower()]
        elif function == "run":
            found = next((e for e in entries if e["name"].lower() == str(args.get("name", "")).lower()), None)
            if not found:
                return {"error": "no such entry"}
            self.fire(found["name"], f"Run now ({found['name']}): {found.get('text', '')}")
            return {"started": found["name"]}
        else:
            return await super().call(function, args, message)
        await self.domain.update_card(self.id, settings={"entries": entries})
        return {"done": function}


@register
class HaEvent(AutoCard):
    """Entity changes."""

    type = "ha_event"
    title = "HA event"
    help = "Wakes the chain when an entity changes (e.g. the door opens), with an instruction; a cooldown keeps it calm."
    settings_schema = {
        "watches": {"type": "watches", "title": "Watches", "default": [],
                    "help": "Each: a name, an entity, the state that counts (empty: any change), what to do, a cooldown (s)."},
    }

    async def start(self) -> None:
        entities = sorted({w["entity_id"] for w in self.settings.get("watches") or [] if w.get("entity_id")})
        self._last: dict[str, datetime] = {}
        if entities:
            self._unsubs.append(async_track_state_change_event(self.hass, entities, self._changed))

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
