"""AAC's calendar in Home Assistant (`calendar.aac_calendar`): the entries the running domain's cards offer, asked of
the card layer (its calendar, calendars.py), never a card."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .calendars import parse
from .const import DOMAIN, SIGNAL


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """The calendar."""
    async_add_entities([AACCalendar(entry)])


def _event(e: dict[str, Any]) -> CalendarEvent:
    """An entry as the card layer gives it (its start and end as text)."""
    return CalendarEvent(summary=e["summary"], start=parse(e["start_v"]), end=parse(e["end_v"]),
                         description=e.get("description") or None, uid=e["uid"])


class AACCalendar(CalendarEntity):
    """The cards' calendar entries."""

    _attr_has_entity_name = True
    _attr_name = "AAC calendar"
    _attr_supported_features = CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.UPDATE_EVENT | CalendarEntityFeature.DELETE_EVENT

    def __init__(self, entry) -> None:
        """Initialize (the entity is calendar.aac_calendar)."""
        self._attr_unique_id = f"{entry.entry_id}_calendar_assistant"
        self._event: CalendarEvent | None = None

    async def _ask(self, op: str, **args: Any) -> Any:
        """The card layer's calendar (the entries its cards offer)."""
        return await self.hass.data[DOMAIN].layer_ask("calendar", {"op": op, **args})

    async def async_added_to_hass(self) -> None:
        """Follow changes."""
        @callback
        def changed(event: dict[str, Any]) -> None:
            if event.get("what") in ("calendar", "setup", "card"):
                self.async_schedule_update_ha_state(force_refresh=True)
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, changed))

    @property
    def event(self) -> CalendarEvent | None:
        """The next entry."""
        return self._event

    async def async_update(self) -> None:
        """Find the next entry."""
        now = dt_util.now()
        upcoming = await self._ask("entries", start=now.isoformat(), end=now.replace(year=now.year + 1).isoformat()) \
            if DOMAIN in self.hass.data else []
        self._event = _event(upcoming[0]) if upcoming else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        """Entries in a range."""
        return [_event(e) for e in await self._ask("entries", start=start_date.isoformat(), end=end_date.isoformat())]

    async def async_create_event(self, **kwargs: Any) -> None:
        """A new entry from HA (into the first card that takes new ones: a Calendar card)."""
        await self._do(self._ask("add", entry={"summary": kwargs["summary"], "start": str(kwargs["dtstart"]),
                                               "end": str(kwargs.get("dtend") or ""), "description": kwargs.get("description")}))

    async def async_update_event(self, uid: str, event: dict[str, Any], recurrence_id: str | None = None,
                                 recurrence_range: str | None = None) -> None:
        """Change an entry from HA (where it lives)."""
        await self._do(self._ask("change", uid=uid, entry={"summary": event.get("summary"), "description": event.get("description"),
                                                          "start": str(event["dtstart"]) if event.get("dtstart") else None,
                                                          "end": str(event["dtend"]) if event.get("dtend") else None}))

    async def async_delete_event(self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None) -> None:
        """Delete an entry from HA (where it lives)."""
        await self._do(self._ask("remove", uid=uid))

    @staticmethod
    async def _do(job) -> None:
        try:
            await job
        except ValueError as err:  # a card that does not offer it says so
            raise HomeAssistantError(str(err)) from err
