"""AAC plans for itself (triggers module): schedule / watch / list_schedules / cancel_schedule.

Framework: agents organize tasks, not rules. The master may set its own
reminders and check-ins (time triggers marked created_by "agent"); it may
never change or delete the owner's triggers. How much it may do alone is
the module's setting own_plans: off (tools not offered), ask (a proposal the
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

from ...capabilities import parse_weekdays
from ...chats import AAC_CHAT_ID
from ...const import DOMAIN

if TYPE_CHECKING:
    from .. import ModuleContext
    from . import Triggers

MAX_AGENT_TRIGGERS = 20
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

API_PROMPT = (
    "You can plan for yourself: schedule a reminder or a check-in at a time (once, or on "
    "weekdays) with an instruction for your future self. Use it when the user asks to be "
    "reminded, or when following up later would genuinely help them (e.g. checking in on "
    "something they want to achieve). Keep it sparing; the user sees every plan you make."
)


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

    def __init__(self, ctx: ModuleContext, triggers: Triggers) -> None:
        """Initialize (plans belong to one AAC domain)."""
        self._ctx = ctx
        self._triggers = triggers

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Validate, store, tell the owner."""
        args = tool_input.tool_args
        dom = self._triggers.domain
        mode = self._ctx.setting(dom.id, "own_plans")
        if mode == "off":
            return llm.ToolResult(data={"error": "The owner does not allow AAC to schedule things."}, error=True)
        if sum(t.get("created_by") == "agent" for t in self._triggers.triggers) >= MAX_AGENT_TRIGGERS:
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
        trig = await self._triggers.async_save_trigger(
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
        text = (
            f"I'd like to plan \"{args['name']}\" ({_when_text(when)}): {args['instruction']} "
            "Approve or reject it under Triggers."
            if proposed
            else f"I planned \"{args['name']}\" ({_when_text(when)}): {args['instruction']}"
        )
        dom.chats.add_message(AAC_CHAT_ID, "aac", text, proactive=True, trigger="Own plan")
        return llm.ToolResult(
            data={"id": trig["id"], "status": "waiting for the owner's approval" if proposed else "active",
                  "when": _when_text(when)}
        )


# Events that fire constantly; AAC's own triggers may not listen to them
NOISY_EVENTS = {"state_changed", "time_changed", "call_service", "service_registered", "service_removed",
                "entity_registry_updated", "device_registry_updated", "area_registry_updated", "recorder_5min_statistics_generated",
                "homeassistant_final_write", "component_loaded", "core_config_updated", "themes_updated", "panels_updated",
                "lovelace_updated", "persistent_notifications_updated", "user_added", "user_updated"}


class WatchTool(llm.Tool):
    """Let AAC react to something in Home Assistant on its own."""

    name = "watch"
    title = "Watch"
    description = (
        "Plan to react when something happens in Home Assistant: an entity's state (or one attribute, e.g. "
        "'game' on a Steam sensor) changes, optionally to a value; or an HA event (event_type, optional "
        "event_data filter). instruction = what your future self should do then. Only entities you may read."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required("name"): str,
            probatio.Required("instruction"): str,
            probatio.Optional("entity_id"): str,
            probatio.Optional("attribute", description="Watch this attribute instead of the state."): str,
            probatio.Optional("to", description="Only when it becomes this value."): str,
            probatio.Optional("event_type"): str,
            probatio.Optional("event_data", description="Only events whose data has these values."): dict,
            probatio.Optional("cooldown_minutes", description="At least this long between two reactions (default 10)."): int,
        }
    )

    def __init__(self, ctx: ModuleContext, triggers: Triggers) -> None:
        """Initialize."""
        self._ctx = ctx
        self._triggers = triggers

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Same rules as schedule: the owner's own_plans setting, a cap, everything visible in From AAC."""
        from ...entities import level  # noqa: PLC0415

        args = tool_input.tool_args
        dom = self._triggers.domain
        mode = self._ctx.setting(dom.id, "own_plans")
        if mode == "off":
            return llm.ToolResult(data={"error": "The owner does not allow AAC to plan things."}, error=True)
        if sum(t.get("created_by") == "agent" for t in self._triggers.triggers) >= MAX_AGENT_TRIGGERS:
            return llm.ToolResult(data={"error": "Too many own plans already; cancel one first."}, error=True)
        if args.get("entity_id"):
            if level(hass, dom.settings, args["entity_id"]) not in ("read", "control"):
                return llm.ToolResult(data={"error": "You may not read that entity (request_access first)."}, error=True)
            when = {"type": "state", "entity_id": args["entity_id"], "attribute": args.get("attribute", ""), "to": args.get("to", "")}
            what = f"{args['entity_id']}{' ' + args['attribute'] if args.get('attribute') else ''} changes"
        elif args.get("event_type"):
            if args["event_type"] in NOISY_EVENTS:
                return llm.ToolResult(data={"error": "That event fires constantly; watch an entity instead."}, error=True)
            when = {"type": "event", "event_type": args["event_type"],
                    "event_data": {k: v for k, v in (args.get("event_data") or {}).items() if isinstance(v, (str, int, float, bool))}}
            what = f"event {args['event_type']}"
        else:
            return llm.ToolResult(data={"error": "Give entity_id or event_type."}, error=True)
        if when.get("to"):
            what += f" to {when['to']}"
        proposed = mode == "ask"
        trig = await self._triggers.async_save_trigger({
            "name": args["name"], "enabled": not proposed, "when": when,
            "do": {"template": "event-reaction", "instruction": args["instruction"]},
            "cooldown": max(60, int(args.get("cooldown_minutes", 10)) * 60), "quiet_hours": None,
            "created_by": "agent", "proposed": proposed,
        })
        text = (f"I'd like to watch \"{args['name']}\" (when {what}): {args['instruction']} Approve or reject it under Triggers."
                if proposed else f"I'm now watching \"{args['name']}\" (when {what}): {args['instruction']}")
        dom.chats.add_message(AAC_CHAT_ID, "aac", text, proactive=True, trigger="Own plan")
        return llm.ToolResult(data={"id": trig["id"], "status": "waiting for the owner's approval" if proposed else "active"})


class ListSchedulesTool(llm.Tool):
    """See what is scheduled."""

    name = "list_schedules"
    title = "List schedules"
    description = "List scheduled triggers (yours and the owner's)."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    integration = DOMAIN

    def __init__(self, ctx: ModuleContext, triggers: Triggers) -> None:
        """Initialize (plans belong to one AAC domain)."""
        self._ctx = ctx
        self._triggers = triggers

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """List."""
        return llm.ToolResult(
            data={
                "schedules": [
                    {"id": t["id"], "name": t["name"], "by": t.get("created_by", "owner"),
                     "when": _when_text(t["when"]) if t["when"]["type"] == "time"
                     else t["when"].get("event_type") if t["when"]["type"] == "event"
                     else f"{t['when'].get('entity_id')} {t['when'].get('attribute') or ''}".strip(),
                     "instruction": (t.get("do") or {}).get("instruction", ""),
                     "status": "proposed" if t.get("proposed") else ("on" if t.get("enabled") else "off")}
                    for t in self._triggers.triggers
                ]
            }
        )


class CancelScheduleTool(llm.Tool):
    """Cancel one of AAC's own plans."""

    name = "cancel_schedule"
    title = "Cancel schedule"
    description = "Cancel one of YOUR OWN plans by id. The owner's triggers cannot be changed."
    # Not "destructive": it only removes AAC's own plans, also in automatic runs (the owner's are refused)
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    integration = DOMAIN
    parameters = probatio.Schema({probatio.Required("id", description="The plan id."): str})

    def __init__(self, ctx: ModuleContext, triggers: Triggers) -> None:
        """Initialize (plans belong to one AAC domain)."""
        self._ctx = ctx
        self._triggers = triggers

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Only agent-made triggers."""
        dom = self._triggers.domain
        trig = self._triggers.get(tool_input.tool_args["id"])
        if trig is None:
            return llm.ToolResult(data={"error": "No such plan."}, error=True)
        if trig.get("created_by") != "agent":
            return llm.ToolResult(data={"error": "That is the owner's trigger; agents cannot change it."}, error=True)
        await self._triggers.async_delete_trigger(trig["id"])
        dom.chats.add_message(AAC_CHAT_ID, "aac", f"I cancelled my plan \"{trig['name']}\".", proactive=True, trigger="Own plan")
        return llm.ToolResult(data={"cancelled": trig["id"]})


@dataclass(kw_only=True)
class PlansAPI(llm.API):
    """AAC's planning tools as an LLM API (none while the owner set own_plans to off)."""

    ctx: ModuleContext
    triggers: Triggers

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        args = (self.ctx, self.triggers)
        off = self.ctx.setting(self.triggers.domain.id, "own_plans") == "off"
        return llm.APIInstance(
            api=self, api_prompt=API_PROMPT, llm_context=llm_context,
            tools=[] if off else [ScheduleTool(*args), WatchTool(*args), ListSchedulesTool(*args), CancelScheduleTool(*args)],
        )
