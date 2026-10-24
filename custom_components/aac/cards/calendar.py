"""Calendar: its own entries, plus the dated entries of cards that offer them (`dated()`: a Lists card's entries with a
field when/date/due). Home Assistant shows it as AAC's calendar (`calendar.aac_calendar`, through the core's
"ha_calendar" channel); a model or a Conditional card uses its functions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, new_id, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

def parse(value: Any) -> date | datetime | None:
    """A date or a time from text (local)."""
    if not value:
        return None
    text = str(value).strip()
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    when = dt_util.parse_datetime(text)
    return dt_util.as_local(when) if when else None


@register
class Calendar(Card):
    """A calendar."""

    type = "calendar"
    title = "Calendar"
    category = "Data"
    help = ("Its own entries and the dated entries of Lists cards (a field when/date/due); Home Assistant shows them as "
            "AAC's calendar. A model reads and changes it through its functions.")
    keeps_data = True
    settings_schema = {"lists": {"type": "text", "title": "Also show the dated entries of", "default": "lists",
                                 "help": "Card ids, comma separated: any card that offers dated entries (a Lists card)."}}
    values = {"today": "today's entries (\"HH:MM what\", all-day ones without a time)", "next": "the next entry (\"what, when\")"}
    functions = {
        "calendar": {"help": "Entries between two dates (default: the next 7 days).",
                     "args": {"start": {"type": "string", "help": "YYYY-MM-DD"}, "end": {"type": "string", "help": "YYYY-MM-DD"}}},
        "add": {"help": "A new entry.", "args": {"summary": {"type": "string", "help": "What.", "required": True},
                                                  "start": {"type": "string", "help": "YYYY-MM-DD or YYYY-MM-DDTHH:MM.", "required": True},
                                                  "end": {"type": "string", "help": "Same form (default: 1 hour / 1 day)."},
                                                  "description": {"type": "string", "help": "Details."}}},
        "change": {"help": "Change an entry.", "args": {"uid": {"type": "string", "help": "Its id (from calendar).", "required": True},
                                                        "summary": {"type": "string"}, "start": {"type": "string"},
                                                        "end": {"type": "string"}, "description": {"type": "string"}}},
        "remove": {"help": "Delete an entry.", "args": {"uid": {"type": "string", "help": "Its id.", "required": True}}},
    }

    def current(self) -> dict[str, Any]:
        now = dt_util.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today = [f"{e['start_v'].strftime('%H:%M') + ' ' if not e['all_day'] else ''}{e['summary']}"
                 for e in self.events(start, start + timedelta(days=1))]
        upcoming = self.events(now, now + timedelta(days=365))
        return {"today": today, "next": f"{upcoming[0]['summary']}, {upcoming[0]['start_v']}" if upcoming else ""}

    def default_data(self) -> dict[str, Any]:
        return {"events": []}

    async def start(self) -> None:
        self.core.subscribe("ha_calendar", self, self.events)  # Home Assistant's calendar entity shows it

    def events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Its own entries and the dated list entries between two times: {uid, summary, start, end, description, own}."""
        found = []
        for e in self.data["events"]:
            found.append({**e, "own": True})
        for card_id in [x.strip() for x in str(self.settings.get("lists") or "").split(",") if x.strip()]:
            card = self.domain.cards.get(card_id)
            if card is not None and hasattr(card, "dated"):  # any card that offers dated entries
                found += [{**e, "description": e.get("description", ""), "end": e.get("end"), "own": False} for e in card.dated()]
        out = []
        for e in found:
            s = parse(e["start"])
            if s is None:
                continue
            all_day = not isinstance(s, datetime)
            en = parse(e.get("end")) or (s + (timedelta(days=1) if all_day else timedelta(hours=1)))
            s_dt = datetime.combine(s, datetime.min.time(), dt_util.get_default_time_zone()) if all_day else s
            en_dt = datetime.combine(en, datetime.min.time(), dt_util.get_default_time_zone()) if all_day else en
            if en_dt > start and s_dt < end:
                out.append({**e, "start_v": s, "end_v": en, "all_day": all_day})
        return sorted(out, key=lambda e: str(e["start_v"]))

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "calendar":
            today = dt_util.now()
            start = parse(args.get("start")) or today
            end = parse(args.get("end")) or (today + timedelta(days=7))
            to_dt = lambda v: v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time(), dt_util.get_default_time_zone())  # noqa: E731
            return [{"uid": e["uid"], "summary": e["summary"], "start": str(e["start_v"]), "end": str(e["end_v"]),
                     "description": e.get("description", "")} for e in self.events(to_dt(start), to_dt(end))]
        events = self.data["events"]
        if function == "add":
            if parse(args.get("start")) is None:
                return {"error": "start must be YYYY-MM-DD or YYYY-MM-DDTHH:MM"}
            events.append({"uid": new_id(), "summary": str(args["summary"]), "start": str(args["start"]),
                           "end": args.get("end"), "description": args.get("description") or ""})
        elif function == "change":
            found = next((e for e in events if e["uid"] == args.get("uid")), None)
            if not found:
                return {"error": "no such entry (only its own entries can change)"}
            found.update({k: v for k, v in args.items() if k in ("summary", "start", "end", "description") and v is not None})
        elif function == "remove":
            self.data["events"] = [e for e in events if e["uid"] != args.get("uid")]
        else:
            return await super().call(function, args, context)
        await self.save()
        self.core.notify("calendar")
        return {"done": function}
