"""The Schedule card (docs/v2 page 2; v1's time triggers): wakes the chain at set times with an instruction (an alarm, a
reminder, a routine). Its entries are its data, not settings (a reminder is no Undo step); each says who made it: the
owner on its page, or AAC (a model, a Relay's script) through its functions, which may only touch its own entries, up to
MAX_PLANS, and only as the owner allows (Permissions: allowed, ask first, off). AAC's entries wait out quiet hours. An
entry nobody answered (no model, or it failed) goes out at 'unanswered' (wire it to a Notify card). Its times are on
AAC's calendar."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time as dtime, timedelta
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, new_id, register

VERSION = "2.0.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

LOG_SIZE = 60
MAX_PLANS = 20  # entries AAC may have at once
DAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]

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


def days(value: Any) -> list[int]:
    """Weekdays from what a person or a small model writes: numbers (0 = Monday), names ("Mon", "monday"), groups
    ("weekdays", "weekend"), ranges ("Mon-Fri", "Mo-Fr"), as a list or a text; [] = every day."""
    items = value if isinstance(value, list) else re.split(r"[,;/\s]+(?:and\s+)?", str(value or "").strip())
    found: set[int] = set()

    def day(word: str) -> int:
        if word.isdigit() and 0 <= int(word) <= 6:
            return int(word)
        if word[:2] in DAYS:
            return DAYS.index(word[:2])
        raise ValueError(f"not a day: {word}")
    for item in items:
        word = str(item).strip().lower()
        if not word or word in ("daily", "every day", "everyday", "all", "every"):
            continue
        if word in ("weekdays", "weekday", "workdays", "workday"):
            found |= {0, 1, 2, 3, 4}
        elif word in ("weekend", "weekends"):
            found |= {5, 6}
        elif "-" in word:
            a, b = (day(x.strip()) for x in word.split("-", 1))
            found |= {(a + i) % 7 for i in range((b - a) % 7 + 1)}
        else:
            found.add(day(word))
    return sorted(found)


def _quiet(settings: dict[str, Any], now: dtime) -> bool:
    try:
        a, b = dtime.fromisoformat(settings.get("quiet_from") or ""), dtime.fromisoformat(settings.get("quiet_to") or "")
    except ValueError:
        return False
    return (a <= now or now < b) if a > b else (a <= now < b) if a != b else False


@register
class Schedule(AutoCard):
    """Times."""

    type = "schedule"
    title = "Schedule"
    help = ("Wakes the chain at set times with an instruction (an alarm, a reminder, a routine). Its entries are on its "
            "page and on AAC's calendar; a model can add its own through its functions, as Permissions allow. What "
            "nobody answers goes out at 'unanswered'.")
    outputs = {"out": "text", "unanswered": "text"}
    ports_help = {"out": "an entry that is due: its instruction (<this card>.said)", "answers": "what the chain answered",
                  "unanswered": "an entry nobody answered in time (no answer, or the model failed): its instruction, for a Notify card"}
    tags = {"said": "what woke the chain: its instruction", "unanswered": "an entry nobody answered"}
    settings_schema = {
        "plans": {"type": "select", "title": "Entries a model makes", "default": "allowed", "permission": True,
                  "options": [["allowed", "allowed at once"], ["ask", "wait for my OK (on its page)"], ["off", "not allowed"]],
                  "help": "A model (or a Relay's script) may add, change and remove only its own entries, at most 20; yours it "
                          "can only read and run."},
        "quiet_from": {"type": "text", "title": "Quiet from (HH:MM)", "default": "", "fold": "Quiet hours",
                       "help": "AAC's entries that fall inside wait until the quiet hours end (yours ring as set). Empty: none."},
        "quiet_to": {"type": "text", "title": "Quiet until (HH:MM)", "default": "", "fold": "Quiet hours"},
        "answer_within": {"type": "number", "title": "Unanswered after (minutes)", "default": 30, "min": 0, "off": 0,
                          "help": "When no answer comes back in this time (or the model failed), the entry goes out at "
                                  "'unanswered'. 0: never."},
    }
    functions = {
        "list": {"help": "The entries: name, time, days or date, what to do, who made it (owner or aac), waiting for the owner's OK."},
        "add": {"help": "A new entry of your own (e.g. a reminder).",
                "args": {"name": {"type": "string", "help": "Its name.", "required": True},
                         "at": {"type": "string", "help": "HH:MM.", "required": True},
                         "text": {"type": "string", "help": "What to do then (a reminder: what to say).", "required": True},
                         "days": {"type": "string", "help": "e.g. 'Mon-Fri', 'weekend', 'Mon, Thu'; empty: every day."},
                         "date": {"type": "string", "help": "YYYY-MM-DD for once (then it goes away)."}}},
        "change": {"help": "Change one of your own entries (only what is given).",
                   "args": {"name": {"type": "string", "help": "Its name.", "required": True},
                            "new_name": {"type": "string"}, "at": {"type": "string", "help": "HH:MM."}, "text": {"type": "string"},
                            "days": {"type": "string", "help": "As in add."}, "date": {"type": "string", "help": "YYYY-MM-DD, or 'none'."},
                            "enabled": {"type": "boolean"}}},
        "remove": {"help": "Take one of your own entries out.", "args": {"name": {"type": "string", "help": "Its name.", "required": True}}},
        "run": {"help": "Run an entry now.", "args": {"name": {"type": "string", "help": "Its name.", "required": True}}},
    }

    def default_data(self) -> dict[str, Any]:
        return {"log": [], "entries": [], "waiting": []}

    @classmethod
    def upgrade(cls, settings: dict[str, Any], made_with: str) -> dict[str, Any]:
        settings.pop("entries", None)  # 1.3.0: the entries are its data (upgrade_data moves them)
        return settings

    async def upgrade_data(self, settings: dict[str, Any], made_with: str) -> None:
        if "entries" not in self.data:  # saved before 1.3.0: the entries were a setting, made by the owner
            self.data["entries"] = [{"id": new_id(), **e, "weekdays": e.get("weekdays") or [], "by": "owner"}
                                    for e in settings.get("entries") or []]
            self.data.setdefault("waiting", [])
            await self.save()

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.data.setdefault("entries", [])

    async def start(self) -> None:
        self._unsubs.append(self.every_minute(self._tick))
        self.core.calendar.offer(self, self._calendar)
        limit = float(self.settings.get("answer_within") or 0) * 60
        for item in self.data["log"]:  # fired before a restart and not answered yet: still watched
            if item.get("answer") is None and not item.get("unanswered") and limit:
                age = (dt_util.utcnow() - datetime.fromisoformat(item["time"])).total_seconds()
                if age < 86400:
                    self._watch(item["id"], max(1.0, limit - age))

    def offered(self) -> dict[str, dict[str, Any]]:
        """Without the owner's leave (Permissions: off) a model sees no add, change, remove."""
        found = super().offered()
        return {k: v for k, v in found.items() if k not in ("add", "change", "remove")} if self.settings.get("plans") == "off" else found

    # ------------------------------------------------------------------ firing
    def _tick(self, now: datetime) -> None:
        local = dt_util.as_local(now)
        hm, day, today = local.strftime("%H:%M"), local.weekday(), local.date().isoformat()
        quiet = _quiet(self.settings, local.time())
        waiting, changed = self.data.setdefault("waiting", []), False
        if waiting and not quiet:  # quiet hours are over: AAC's entries that waited ring now
            self.data["waiting"], changed = [], True
            for entry in [e for e in self.entries if e["id"] in waiting]:
                self._ring(entry, hm)
        for entry in self.entries:
            if not entry.get("enabled", True) or entry.get("proposed") or entry.get("at") != hm:
                continue
            if (entry.get("date") and entry["date"] != today) or (not entry.get("date") and entry.get("weekdays") and day not in entry["weekdays"]):
                continue
            changed = True
            if quiet and entry.get("by") == "aac":
                self.data["waiting"].append(entry["id"])
            else:
                self._ring(entry, hm)
        if changed:
            self.task(self._saved())

    def _ring(self, entry: dict[str, Any], hm: str | None = None) -> None:
        """An entry rings (hm: at its time; None: run now)."""
        when = f"Scheduled ({entry['name']}, {hm})" if hm else f"Run now ({entry['name']})"
        text = (f"{when}: {entry.get('text', '')}\n(It is due now. A reminder: say it now. A task: "
                "do it. Something to check: only speak if it matters.)")
        message_id = self.fire(entry["name"], text)
        if not self.wired("out"):  # nothing can answer
            next(i for i in self.data["log"] if i["id"] == message_id)["failed"] = "nothing is wired to its output"
            self._unanswered(message_id)
        elif limit := float(self.settings.get("answer_within") or 0) * 60:
            self._watch(message_id, limit)
        if hm and entry.get("date"):  # a one-time entry is done once it rang
            self.data["entries"] = [e for e in self.entries if e["id"] != entry["id"]]

    def _watch(self, message_id: str, seconds: float) -> None:
        self._unsubs.append(self.later(seconds, lambda: self._unanswered(message_id)))

    def _unanswered(self, message_id: str) -> None:
        """No answer came back in time, or the model failed: the entry goes out at 'unanswered'."""
        item = next((i for i in self.data["log"] if i.get("id") == message_id), None)
        if item is None or item.get("unanswered") or (item.get("answer") is not None and not item.get("failed")):
            return
        item["unanswered"] = True
        text, failed = item["text"].split("\n(It is due now.")[0], f" ({item['failed']})" if item.get("failed") else ""
        context = Context()
        context.add(self.tag("unanswered"), f"Not answered{failed}: {text}")
        self.log(f"{item['name']}: nobody answered{failed}", "warning", what="unanswered")
        self.task(self._saved())
        task = self.task(self.send(context, "unanswered"), background=True)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def handle(self, context: Context, port: str) -> None:
        """What the chain concluded; a model that failed counts as no answer."""
        if port != "answers" or context.streaming or (item := self.answered(context)) is None:
            return
        await self.save()
        self.tell()
        if item.get("failed"):
            self._unanswered(item["id"])

    # ------------------------------------------------------------------ AAC's calendar
    def _calendar(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Each time an entry rings in the period (a repeating one once per day)."""
        found = []
        first, last = dt_util.as_local(start).date(), min(dt_util.as_local(end).date(), dt_util.as_local(start).date() + timedelta(days=62))
        for entry in self.entries:
            if not entry.get("enabled", True) or entry.get("proposed"):
                continue
            if entry.get("date"):
                dates = [date.fromisoformat(entry["date"])] if first <= date.fromisoformat(entry["date"]) <= last else []
            else:
                dates = [first + timedelta(days=i) for i in range((last - first).days + 1)
                         if not entry.get("weekdays") or (first + timedelta(days=i)).weekday() in entry["weekdays"]]
            for d in dates:
                at = datetime.combine(d, dtime.fromisoformat(entry["at"]), dt_util.get_default_time_zone())
                found.append({"key": f"{entry['id']}@{d.isoformat()}", "summary": entry["name"], "start": at.isoformat(),
                              "end": (at + timedelta(minutes=15)).isoformat(), "description": entry.get("text", "")})
        return found

    # ------------------------------------------------------------------ changing entries
    async def _saved(self) -> None:
        await self.save()
        self.tell()

    @staticmethod
    def _checked(entry: dict[str, Any]) -> dict[str, Any]:
        """An entry as it is kept: a time HH:MM, days as numbers, a date or none."""
        at = str(entry.get("at") or "").strip()
        dtime.fromisoformat(at)  # ValueError: not a time
        if entry.get("date"):
            date.fromisoformat(str(entry["date"]))
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ValueError("an entry needs a name")
        return {**entry, "name": name, "at": at[:5], "weekdays": days(entry.get("weekdays", [])), "date": entry.get("date") or None}

    def _named(self, name: Any) -> dict[str, Any] | None:
        return next((e for e in self.entries if e["name"].lower() == str(name or "").strip().lower()), None)

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """A model's or a script's call: AAC's own entries only, as the owner allows."""
        if function == "list":
            return [{k: e.get(k) for k in ("name", "at", "weekdays", "date", "text", "enabled", "by", "proposed") if e.get(k) is not None}
                    for e in self.entries]
        if function == "run":
            if not (found := self._named(args.get("name"))):
                return {"error": "no such entry"}
            self._ring(found)
            return {"started": found["name"]}
        if function not in ("add", "change", "remove"):
            return await super().call(function, args, context)
        plans = self.settings.get("plans") or "allowed"
        if plans == "off":
            return {"error": "the owner does not allow you to make entries here"}
        found = self._named(args.get("name"))
        if found is not None and found.get("by") != "aac":
            return {"error": f"'{found['name']}' is the owner's entry: you may only read and run it"}
        try:
            if function == "add":
                if found is None and sum(e.get("by") == "aac" for e in self.entries) >= MAX_PLANS:
                    return {"error": f"you have {MAX_PLANS} entries already: remove one first"}
                entry = self._checked({"id": found["id"] if found else new_id(), "name": args["name"], "at": args.get("at"),
                                       "text": args.get("text", ""), "weekdays": args.get("days") or args.get("weekdays") or [],
                                       "date": args.get("date"), "enabled": plans != "ask", "by": "aac",
                                       **({"proposed": True} if plans == "ask" else {})})
                self.data["entries"] = [e for e in self.entries if e is not found] + [entry]
            elif function == "change":
                if found is None:
                    return {"error": "no entry of yours with that name"}
                changes = {k: args[k] for k in ("at", "text", "enabled") if args.get(k) is not None}
                if args.get("new_name"):
                    changes["name"] = args["new_name"]
                if args.get("days") is not None:
                    changes["weekdays"] = args["days"]
                if args.get("date") is not None:
                    changes["date"] = None if str(args["date"]).lower() in ("", "none") else args["date"]
                entry = self._checked({**found, **changes, **({"proposed": True, "enabled": False} if plans == "ask" else {})})
                self.data["entries"] = [entry if e is found else e for e in self.entries]
            else:
                if found is None:
                    return {"error": "no entry of yours with that name"}
                self.data["entries"] = [e for e in self.entries if e is not found]
        except (ValueError, KeyError) as err:
            return {"error": f"{err} (at: HH:MM, days: e.g. 'Mon-Fri', date: YYYY-MM-DD)"}
        self.log(f"{function}: {args.get('name')}" + (" (waits for the owner's OK)" if plans == "ask" and function != "remove" else ""), what="plan")
        await self._saved()
        return {"done": function, **({"waiting": "for the owner's OK on the Schedule's page"} if plans == "ask" and function != "remove" else {})}

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """Its page: the owner sees and changes every entry, and says yes or no to what AAC asks for."""
        if action == "entries":
            return {"entries": self.entries, "log": self.data["log"], "plans": self.settings.get("plans") or "allowed"}
        entry_id = str(args.get("id") or (args.get("entry") or {}).get("id") or "")
        found = next((e for e in self.entries if e["id"] == entry_id), None)
        if action == "save":  # a new entry is the owner's; a changed one stays whose it was
            entry = self._checked({**(found or {"id": new_id(), "by": "owner", "enabled": True}), **(args.get("entry") or {})})
            self.data["entries"] = [entry if e is found else e for e in self.entries] if found else [*self.entries, entry]
        elif action == "remove" and found:
            self.data["entries"] = [e for e in self.entries if e is not found]
        elif action == "allow" and found:  # yes or no to an entry AAC asked for
            if args.get("ok"):
                found.pop("proposed", None)
                found["enabled"] = True
            else:
                self.data["entries"] = [e for e in self.entries if e is not found]
        elif action == "run" and found:
            self._ring(found)
        else:
            return await super().ui_call(action, args)
        await self._saved()
        return {"entries": self.entries}
