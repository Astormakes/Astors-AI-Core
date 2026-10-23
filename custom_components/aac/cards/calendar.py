"""Calendar: its own entries, plus dated entries of Lists cards (a field "when", "date" or "due"). Home Assistant shows
them as AAC's calendar (`calendar.aac_calendar`); a model or a Conditional card uses its functions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..message import Message, new_id
from . import Card, register

DATE_FIELDS = ("when", "date", "due")


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


def _walk(items: list[dict[str, Any]]):
    for it in items:
        yield it
        yield from _walk(it.get("children") or [])


@register
class Calendar(Card):
    """A calendar."""

    type = "calendar"
    title = "Calendar"
    category = "Data"
    help = ("Its own entries and the dated entries of Lists cards (a field when/date/due); Home Assistant shows them as "
            "AAC's calendar. A model reads and changes it through its functions.")
    keeps_data = True
    settings_schema = {"lists": {"type": "text", "title": "Show dated entries of the Lists cards", "default": "lists",
                                 "help": "Their ids, comma separated (empty: none)."}}
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

    def default_data(self) -> dict[str, Any]:
        return {"events": []}

    def events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Its own entries and the dated list entries between two times: {uid, summary, start, end, description, own}."""
        found = []
        for e in self.data["events"]:
            found.append({**e, "own": True})
        for card_id in [x.strip() for x in str(self.settings.get("lists") or "").split(",") if x.strip()]:
            card = self.domain.cards.get(card_id)
            for li in (card.data.get("lists") or []) if card is not None and card.type == "lists" else []:
                for it in _walk(li.get("items") or []):
                    value = next((it.get("fields", {}).get(f) for f in DATE_FIELDS if it.get("fields", {}).get(f)), None)
                    if value and not it.get("done"):
                        found.append({"uid": f"list:{it['id']}", "summary": f"{it['text']} ({li['name']})", "start": value,
                                      "end": None, "description": "", "own": False})
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

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
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
            return await super().call(function, args, message)
        await self.save()
        self.core.notify("calendar")
        return {"done": function}
