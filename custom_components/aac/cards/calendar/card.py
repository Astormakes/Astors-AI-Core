"""Calendar: its own entries; with the entries other cards offer (a Lists card's dated entries, a Schedule's times)
it shows AAC's calendar, asked through the core (`core.calendar`), never another card. Home Assistant shows the same
(`calendar.aac_calendar`); a model or a Relay's script uses its functions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, new_id, parse_when, register

VERSION = "1.3.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


@register
class Calendar(Card):
    """A calendar."""

    type = "calendar"
    title = "Calendar"
    category = "Data"
    help = ("AAC's calendar: its own entries and those other cards offer (dated list entries, a Schedule's times); Home "
            "Assistant shows the same. A model reads and changes it through its functions.")
    keeps_data = True
    ui = True
    settings_schema = {"ha_calendars": {"type": "list", "title": "Home Assistant calendars on its page", "default": [],
                                        "help": "Entity ids, comma separated (e.g. calendar.family). Its page shows them too; "
                                                "they stay in Home Assistant."},
                       "view": {"type": "select", "title": "Its page shows", "default": "week",
                                "options": [["week", "Week grid"], ["table", "Table"], ["month", "Month"], ["agenda", "Agenda"]]},
                       "days": {"type": "number", "title": "Days on its page", "default": 7}}
    values = {"today": "today's entries (\"HH:MM what\", all-day ones without a time)", "next": "the next entry (\"what, when\")"}
    functions = {
        "calendar": {"help": "AAC's calendar between two dates (default: the next 7 days): every card's entries, each with "
                             "its uid and the card it lives in.",
                     "args": {"start": {"type": "string", "help": "YYYY-MM-DD"}, "end": {"type": "string", "help": "YYYY-MM-DD"}}},
        "add": {"help": "A new entry.", "args": {"summary": {"type": "string", "help": "What.", "required": True},
                                                  "start": {"type": "string", "help": "YYYY-MM-DD or YYYY-MM-DDTHH:MM.", "required": True},
                                                  "end": {"type": "string", "help": "Same form (default: 1 hour / 1 day)."},
                                                  "description": {"type": "string", "help": "Details."}}},
        "change": {"help": "Change an entry where it lives (a card may not allow it).", "args": {"uid": {"type": "string", "help": "Its uid (from calendar).", "required": True},
                                                        "summary": {"type": "string"}, "start": {"type": "string"},
                                                        "end": {"type": "string"}, "description": {"type": "string"}}},
        "remove": {"help": "Delete an entry where it lives (a card may not allow it).", "args": {"uid": {"type": "string", "help": "Its uid.", "required": True}}},
    }

    @classmethod
    def upgrade(cls, settings: dict[str, Any], made_with: str) -> dict[str, Any]:
        settings.pop("lists", None)  # 1.3.0: every card's entries come through the core
        return settings

    async def start(self) -> None:
        self.core.calendar.offer(self, self._query, self._create, self._change, self._remove)

    def _query(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Its own entries (the core picks those of the period)."""
        return [{**e, "key": e["uid"]} for e in self.data["events"]]

    async def _create(self, entry: dict[str, Any]) -> str:
        if parse_when(entry.get("start")) is None:
            raise ValueError("start must be YYYY-MM-DD or YYYY-MM-DDTHH:MM")
        uid = new_id()
        self.data["events"].append({"uid": uid, "summary": str(entry.get("summary") or ""), "start": str(entry["start"]),
                                    "end": entry.get("end") or None, "description": entry.get("description") or ""})
        await self._saved()
        return uid

    async def _change(self, key: str, changes: dict[str, Any]) -> None:
        found = next((e for e in self.data["events"] if e["uid"] == key), None)
        if found is None:
            raise ValueError("no such entry")
        found.update(changes)
        await self._saved()

    async def _remove(self, key: str) -> None:
        self.data["events"] = [e for e in self.data["events"] if e["uid"] != key]
        await self._saved()

    async def _saved(self) -> None:
        await self.save()
        self.core.notify("card", domain=self.domain.id, card=self.id)

    def current(self) -> dict[str, Any]:
        now = dt_util.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = [f"{e['start_v'].strftime('%H:%M') + ' ' if not e['all_day'] else ''}{e['summary']}"
                 for e in self.core.calendar.entries(start, start + timedelta(days=1))]
        upcoming = self.core.calendar.entries(now, now + timedelta(days=365))
        return {"today": today, "next": f"{upcoming[0]['summary']}, {upcoming[0]['start_v']}" if upcoming else ""}

    def default_data(self) -> dict[str, Any]:
        return {"events": []}

    def _name(self, card_id: str) -> str:
        card = self.domain.cards.get(card_id)
        return card.name if card else card_id

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """Its page: the entries of a period (AAC's calendar and the Home Assistant calendars picked)."""
        if action != "agenda":
            return await super().ui_call(action, args)
        tz = dt_util.get_default_time_zone()
        first = date.fromisoformat(args["start"]) if args.get("start") else dt_util.now().date()
        start = datetime.combine(first, datetime.min.time(), tz)
        end = start + timedelta(days=max(1, min(42, int(args.get("days") or 7))))
        found = [{"name": e["summary"], "start": e["start_v"].isoformat(), "end": e["end_v"].isoformat(), "all_day": e["all_day"],
                  "source": "own" if e["source"] == self.id else "card", "calendar": self._name(e["source"]),
                  "notes": e.get("description", "")} for e in self.core.calendar.entries(start, end)]
        for entity in [x for x in self.settings.get("ha_calendars") or [] if x and x != "calendar.aac_calendar"]:
            try:
                got = await self.hass.services.async_call("calendar", "get_events", {"entity_id": entity, "start_date_time": start,
                                                          "end_date_time": end}, blocking=True, return_response=True)
            except Exception as err:  # noqa: BLE001 - a missing calendar must not hide the others
                found.append({"name": f"{entity}: {err}", "start": start.isoformat(), "all_day": True, "source": "error"})
                continue
            name = (self.hass.states.get(entity).attributes.get("friendly_name") if self.hass.states.get(entity) else None) or entity
            for e in (got or {}).get(entity, {}).get("events", []):
                found.append({"name": e.get("summary", ""), "start": e["start"], "end": e.get("end"), "all_day": len(e["start"]) == 10,
                              "source": "ha", "calendar": name, "notes": e.get("location") or e.get("description") or ""})
        return {"entries": sorted(found, key=lambda e: e["start"]), "view": self.settings.get("view") or "week",
                "days": int(self.settings.get("days") or 7)}

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        try:
            if function == "calendar":
                today = dt_util.now()
                start = parse_when(args.get("start")) or today
                end = parse_when(args.get("end")) or (today + timedelta(days=7))
                to_dt = lambda v: v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time(), dt_util.get_default_time_zone())  # noqa: E731
                return [{"uid": e["uid"], "summary": e["summary"], "start": str(e["start_v"]), "end": str(e["end_v"]),
                         "description": e["description"], "in": self._name(e["source"])} for e in self.core.calendar.entries(to_dt(start), to_dt(end))]
            if function == "add":
                return {"done": "add", "uid": f"{self.id}:{await self._create(args)}"}
            if function == "change":
                await self.core.calendar.change(str(args.get("uid") or ""), args)
                return {"done": "change"}
            if function == "remove":
                await self.core.calendar.remove(str(args.get("uid") or ""))
                return {"done": "remove"}
        except ValueError as err:
            return {"error": str(err)}
        return await super().call(function, args, context)
