"""AAC plans for itself: schedule / list_schedules / cancel_schedule.

Framework: agents organize tasks, not rules. The master may set its own
reminders and check-ins (time triggers marked created_by "agent"); it may
never change or delete the owner's triggers. How much it may do alone is
the owner's autonomy setting: off (tools not offered), ask (a proposal the
owner approves in the panel) or allowed (active at once, the owner is told).
Every change is posted to the "From AAC" chat, so nothing happens unseen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import re
from typing import TYPE_CHECKING

import probatio

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from .capabilities import parse_time
from .chats import AAC_CHAT_ID
from .const import DOMAIN

if TYPE_CHECKING:
    from .domains import Domain
    from . import AACConfigEntry

API_ID = "aac_schedule"
MAX_AGENT_TRIGGERS = 20
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

API_PROMPT = (
    "You can plan for yourself: schedule a reminder or a check-in at a time (once, or on "
    "weekdays) with an instruction for your future self. Use it when the user asks to be "
    "reminded, or when following up later would genuinely help them (e.g. checking in on "
    "something they want to achieve). Keep it sparing; the user sees every plan you make."
)


_DAY_NAMES = {
    **{n: i for i, n in enumerate(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])},
    **{n: i for i, n in enumerate(["mo", "di", "mi", "do", "fr", "sa", "so"])},
}
_DAY_GROUPS = {"weekdays": [0, 1, 2, 3, 4], "workdays": [0, 1, 2, 3, 4], "weekend": [5, 6], "daily": [], "every day": []}


def parse_weekdays(values) -> list[int]:
    """[0, 4], ["mon", "fri"], ["weekdays"], "Mo-Fr" → sorted day numbers (0 = Monday).

    Small models get number-coded weekdays wrong ("weekdays" became Mo/We/Fr/Su),
    so names and groups are accepted too."""
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    days: set[int] = set()
    for value in values or []:
        if isinstance(value, int) and 0 <= value <= 6:
            days.add(value)
            continue
        text = str(value).strip().lower()
        if text.isdigit() and 0 <= int(text) <= 6:
            days.add(int(text))
        elif text in _DAY_GROUPS:
            days.update(_DAY_GROUPS[text])
        elif "-" in text and all(p[:3] in _DAY_NAMES or p[:2] in _DAY_NAMES for p in text.split("-", 1)):
            a, b = (
                _DAY_NAMES.get(p[:3], _DAY_NAMES.get(p[:2])) for p in text.split("-", 1)
            )
            days.update(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
        elif text[:3] in _DAY_NAMES:
            days.add(_DAY_NAMES[text[:3]])
        elif text[:2] in _DAY_NAMES:
            days.add(_DAY_NAMES[text[:2]])
    return sorted(days)


def _when_text(when: dict) -> str:
    days = when.get("weekdays") or []
    names = "Mo Tu We Th Fr Sa Su".split()
    when_day = when.get("date") or (" ".join(names[d] for d in days) if days else "every day")
    return f"{when_day} at {when['at']}"


class ScheduleTool(llm.Tool):
    """Create a time trigger for AAC itself."""

    name = "schedule"
    title = "Schedule"
    description = (
        "Plan something for later: a reminder or a check-in. Give either in_minutes, or a time "
        "'at' (HH:MM, local) with an optional date (YYYY-MM-DD, once) or weekdays (e.g. ['weekdays'], ['mon','fri'], repeating)."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required("name", description="Short name, e.g. 'Weigh-in check'."): str,
            probatio.Required(
                "instruction", description="What your future self should do then, e.g. 'Ask how the walk went.'"
            ): str,
            probatio.Optional("in_minutes", description="Once, this many minutes from now."): int,
            probatio.Optional("at", description="Time HH:MM (local)."): str,
            probatio.Optional("date", description="YYYY-MM-DD for a one-off."): str,
            probatio.Optional(
                "weekdays",
                description="Repeat on these days: names like ['mon', 'wed'] or groups ['weekdays'] / ['weekend']; empty = every day.",
            ): [probatio.Any(str, int)],
        }
    )

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize (plans belong to one AAC domain)."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Validate, store, tell the owner."""
        args = tool_input.tool_args
        dom = self._domain
        mode = dom.settings.autonomy.get("schedule", "allowed")
        if mode == "off":
            return llm.ToolResult(data={"error": "The owner does not allow AAC to schedule things."}, error=True)
        if sum(t.get("created_by") == "agent" for t in dom.settings.triggers) >= MAX_AGENT_TRIGGERS:
            return llm.ToolResult(data={"error": "Too many own plans already; cancel one first."}, error=True)

        when: dict = {"type": "time", "weekdays": []}
        if args.get("in_minutes"):
            due = dt_util.now() + timedelta(minutes=max(1, int(args["in_minutes"])))
            when.update(at=due.strftime("%H:%M"), date=due.date().isoformat())
        elif args.get("at") and _TIME_RE.match(args["at"]):
            when["at"] = args["at"]
            if args.get("date"):
                if not _DATE_RE.match(args["date"]):
                    return llm.ToolResult(data={"error": "date must be YYYY-MM-DD"}, error=True)
                when["date"] = args["date"]
            else:
                when["weekdays"] = parse_weekdays(args.get("weekdays"))
        else:
            return llm.ToolResult(data={"error": "Give in_minutes, or at (HH:MM)."}, error=True)

        proposed = mode == "ask"
        trig = await dom.settings.async_save_trigger(
            {
                "name": args["name"],
                "enabled": not proposed,
                "when": when,
                "do": {"template": "event-reaction", "instruction": args["instruction"]},
                "cooldown": 0,
                "quiet_hours": None,
                "created_by": "agent",
                "proposed": proposed,
            }
        )
        dom.triggers.async_start()
        text = (
            f"I'd like to plan \"{args['name']}\" ({_when_text(when)}): {args['instruction']} "
            "Approve or reject it under SETTINGS → Triggers."
            if proposed
            else f"I planned \"{args['name']}\" ({_when_text(when)}): {args['instruction']}"
        )
        dom.chats.add_message(AAC_CHAT_ID, "aac", text, proactive=True, trigger="Own plan")
        return llm.ToolResult(
            data={"id": trig["id"], "status": "waiting for the owner's approval" if proposed else "active",
                  "when": _when_text(when)}
        )


class ListSchedulesTool(llm.Tool):
    """See what is scheduled."""

    name = "list_schedules"
    title = "List schedules"
    description = "List scheduled triggers (yours and the owner's)."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    integration = DOMAIN

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize (plans belong to one AAC domain)."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """List."""
        return llm.ToolResult(
            data={
                "schedules": [
                    {"id": t["id"], "name": t["name"], "by": t.get("created_by", "owner"),
                     "when": _when_text(t["when"]) if t["when"]["type"] == "time" else t["when"].get("entity_id"),
                     "instruction": (t.get("do") or {}).get("instruction", ""),
                     "status": "proposed" if t.get("proposed") else ("on" if t.get("enabled") else "off")}
                    for t in self._domain.settings.triggers
                ]
            }
        )


class CancelScheduleTool(llm.Tool):
    """Cancel one of AAC's own plans."""

    name = "cancel_schedule"
    title = "Cancel schedule"
    description = "Cancel one of YOUR OWN plans by id. The owner's triggers cannot be changed."
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema({probatio.Required("id", description="The plan id."): str})

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize (plans belong to one AAC domain)."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Only agent-made triggers."""
        dom = self._domain
        trig = next((t for t in dom.settings.triggers if t["id"] == tool_input.tool_args["id"]), None)
        if trig is None:
            return llm.ToolResult(data={"error": "No such plan."}, error=True)
        if trig.get("created_by") != "agent":
            return llm.ToolResult(data={"error": "That is the owner's trigger; agents cannot change it."}, error=True)
        await dom.settings.async_delete_trigger(trig["id"])
        dom.triggers.async_start()
        dom.chats.add_message(AAC_CHAT_ID, "aac", f"I cancelled my plan \"{trig['name']}\".", proactive=True, trigger="Own plan")
        return llm.ToolResult(data={"cancelled": trig["id"]})


class CalendarTool(llm.Tool):
    """Read the unified calendar."""

    name = "calendar"
    title = "Calendar"
    description = (
        "What is on the calendar in a period: entries from every module (lists with dates, your own plans, "
        "calendars, …). Default: the next 7 days."
    )
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Optional("from", description="YYYY-MM-DD or ISO time (default: now)"): str,
            probatio.Optional("to", description="YYYY-MM-DD or ISO time (default: 7 days later)"): str,
        }
    )

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Collect from all providers."""
        args = tool_input.tool_args
        start = parse_time(args.get("from")) or dt_util.now()
        end = parse_time(args.get("to")) or start + timedelta(days=7)
        if end <= start:
            end = start + timedelta(days=1)
        entries = await self._entry.runtime_data.capabilities.async_calendar(self._domain.id, start, end)
        return llm.ToolResult(data={"from": start.isoformat(), "to": end.isoformat(), "entries": entries[:100]})


class AddCalendarTool(llm.Tool):
    """Put something on the calendar."""

    name = "add_to_calendar"
    title = "Add to calendar"
    description = (
        "Put an entry on the calendar (an appointment, a date). Only name is required; add start (and end) when "
        "known, all_day for dates, data for anything else. It is stored by a module that takes entries "
        "(e.g. lists); set a reminder separately with schedule if the user wants one."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required("name"): str,
            probatio.Optional("start", description="YYYY-MM-DD or YYYY-MM-DDTHH:MM"): str,
            probatio.Optional("end"): str,
            probatio.Optional("all_day"): bool,
            probatio.Optional("notes"): str,
        }
    )

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Hand it to the first module that takes entries."""
        writers = self._entry.runtime_data.capabilities.writers(self._domain.id)
        if not writers:
            return llm.ToolResult(data={"error": "No module takes calendar entries yet."}, error=True)
        args = dict(tool_input.tool_args)
        entry = {k: v for k, v in args.items() if k != "notes"}
        if args.get("notes"):
            entry["data"] = {"notes": args["notes"]}
        stored = await writers[0].create(self._domain.id, entry)
        return llm.ToolResult(data={"stored_by": writers[0].id, "entry": stored})


@dataclass(kw_only=True)
class AACScheduleAPI(llm.API):
    """The planning tools as an LLM API."""

    entry: AACConfigEntry
    domain: Domain

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        return llm.APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=[ScheduleTool(self.entry, self.domain), ListSchedulesTool(self.entry, self.domain),
                   CancelScheduleTool(self.entry, self.domain), CalendarTool(self.entry, self.domain),
                   AddCalendarTool(self.entry, self.domain)],
        )
