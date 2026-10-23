"""AAC's calendar in Home Assistant: the entries of the running domain's Calendar cards (`calendar.aac_calendar`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """The calendar."""
    async_add_entities([AACCalendar(entry)])


def _event(e: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(summary=e["summary"], start=e["start_v"], end=e["end_v"], description=e.get("description") or None, uid=e["uid"])


class AACCalendar(CalendarEntity):
    """The Calendar cards' entries."""

    _attr_has_entity_name = True
    _attr_name = "AAC calendar"
    _attr_supported_features = CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.UPDATE_EVENT | CalendarEntityFeature.DELETE_EVENT

    def __init__(self, entry) -> None:
        """Initialize (v1's unique id: the entity stays calendar.aac_calendar)."""
        self._attr_unique_id = f"{entry.entry_id}_calendar_assistant"
        self._event: CalendarEvent | None = None

    def _cards(self):
        core = self.hass.data.get(DOMAIN)
        return [c for c in core.domain.cards.values() if c.type == "calendar"] if core and core.domain else []

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
        upcoming = [e for card in self._cards() for e in card.events(now, now.replace(year=now.year + 1))]
        self._event = _event(upcoming[0]) if upcoming else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        """Entries in a range."""
        return [_event(e) for card in self._cards() for e in card.events(start_date, end_date)]

    async def async_create_event(self, **kwargs: Any) -> None:
        """A new entry from HA (into the first Calendar card)."""
        if cards := self._cards():
            await cards[0].call("add", {"summary": kwargs["summary"], "start": str(kwargs["dtstart"]),
                                        "end": str(kwargs.get("dtend") or ""), "description": kwargs.get("description")})

    async def async_update_event(self, uid: str, event: dict[str, Any], recurrence_id: str | None = None,
                                 recurrence_range: str | None = None) -> None:
        """Change an entry from HA."""
        for card in self._cards():
            await card.call("change", {"uid": uid, "summary": event.get("summary"), "start": str(event["dtstart"]) if event.get("dtstart") else None,
                                       "end": str(event["dtend"]) if event.get("dtend") else None, "description": event.get("description")})

    async def async_delete_event(self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None) -> None:
        """Delete an entry from HA."""
        for card in self._cards():
            await card.call("remove", {"uid": uid})
