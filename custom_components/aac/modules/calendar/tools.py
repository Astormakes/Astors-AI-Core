"""The unified calendar as tools: read it, add to it, change or remove an entry in its home.

Moved from the core's planning API in 0.45; the entries come from every module's calendar provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import probatio

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from ...capabilities import parse_time
from ...const import DOMAIN

if TYPE_CHECKING:
    from ... import AACConfigEntry
    from ...domains import Domain

API_PROMPT = (
    "The calendar shows entries from everything AAC knows (lists with dates, your own plans, the owner's "
    "calendars). Change or remove an entry only where it lives, with its uid from the calendar tool."
)


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


class ChangeCalendarTool(llm.Tool):
    """Move or rename a calendar entry in its home."""

    name = "change_calendar_entry"
    title = "Change calendar entry"
    description = (
        "Move, rename or annotate a calendar entry. uid (and recurrence_id for one occurrence) from the calendar "
        "tool; the change happens where the entry lives (e.g. Google, a list). Only what is given changes."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=True)
    integration = DOMAIN
    parameters = probatio.Schema(
        {
            probatio.Required("uid"): str,
            probatio.Optional("recurrence_id"): str,
            probatio.Optional("name"): str,
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
        """Route to the entry's home."""
        args = dict(tool_input.tool_args)
        uid, recurrence_id = args.pop("uid"), args.pop("recurrence_id", None)
        try:
            changed = await self._entry.runtime_data.capabilities.async_change(self._domain.id, uid, args, recurrence_id)
        except (LookupError, ValueError) as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)
        return llm.ToolResult(data={"changed": changed})


class RemoveCalendarTool(llm.Tool):
    """Remove a calendar entry in its home."""

    name = "remove_calendar_entry"
    title = "Remove calendar entry"
    description = (
        "Take an entry off the calendar where it lives (cancels it in e.g. Google; a list entry only loses its "
        "date). Only when the owner asked for it."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=True)
    integration = DOMAIN
    parameters = probatio.Schema({probatio.Required("uid"): str, probatio.Optional("recurrence_id"): str})

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize."""
        self._entry = entry
        self._domain = domain

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        """Only with the owner in the chat (like deleting in modules)."""
        from ...setup_api import owner_present  # noqa: PLC0415

        if not await owner_present(hass, llm_context.context.user_id if llm_context.context else None):
            return llm.ToolResult(data={"error": "Removing calendar entries needs the owner in the chat."}, error=True)
        args = tool_input.tool_args
        try:
            done = await self._entry.runtime_data.capabilities.async_remove(self._domain.id, args["uid"], args.get("recurrence_id"))
        except (LookupError, ValueError) as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)
        return llm.ToolResult(data={"removed": done})


@dataclass(kw_only=True)
class CalendarAPI(llm.API):
    """The calendar tools as an LLM API."""

    entry: AACConfigEntry
    domain: Domain

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """Tools + prompt."""
        args = (self.entry, self.domain)
        return llm.APIInstance(
            api=self, api_prompt=API_PROMPT, llm_context=llm_context,
            tools=[CalendarTool(*args), AddCalendarTool(*args), ChangeCalendarTool(*args), RemoveCalendarTool(*args)],
        )
