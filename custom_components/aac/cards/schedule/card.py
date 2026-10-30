"""The Schedule card (docs/v2 page 2): Wakes the chain at set times with an instruction (an alarm, a reminder, a
routine). A model can add and remove entries itself through its functions."""

from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, register

VERSION = "1.2.0"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

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

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        """A turn still being worked on: only the finished answer counts (nothing goes on from here)."""
        return []

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

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
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
            return await super().call(function, args, context)
        await self.domain.update_card(self.id, settings={"entries": entries})
        return {"done": function}

