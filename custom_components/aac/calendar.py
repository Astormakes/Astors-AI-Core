"""AAC as a calendar in Home Assistant: one calendar entity per AAC domain.

It shows AAC's own entries (dated list entries, AAC's plans, other modules) so HA's
calendar page and the HA app see them. Entries of HA calendars (Google, …) are left
out: HA shows those already. Creating an event here puts it on AAC's "Calendar"
list; changing or deleting goes to the entry's home, identified by its uid.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AACConfigEntry
from .capabilities import parse_time
from .const import DEFAULT_NAME, DOMAIN
from .domains import Domain

SCAN_INTERVAL = timedelta(minutes=5)
HA_SOURCE = "calendar"  # the adapter for HA's own calendars


async def async_setup_entry(
    hass: HomeAssistant, entry: AACConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """One calendar per AAC domain."""
    async_add_entities([AACCalendar(entry, dom) for dom in entry.runtime_data.domains.values()], True)


def _when(value: Any, all_day: bool) -> date | datetime | None:
    when = parse_time(value)
    if when is None:
        return None
    return dt_util.as_local(when).date() if all_day else dt_util.as_local(when)


def _to_event(entry: dict[str, Any]) -> CalendarEvent | None:
    all_day = bool(entry.get("all_day"))
    start = _when(entry.get("start"), all_day)
    if start is None:
        return None
    end = _when(entry.get("end"), all_day) or (start + (timedelta(days=1) if all_day else timedelta(hours=1)))
    if end <= start:
        end = start + (timedelta(days=1) if all_day else timedelta(minutes=30))
    data = entry.get("data") or {}
    notes = data.get("notes") or data.get("instruction") or ""
    where = " › ".join(entry.get("tags") or [])
    return CalendarEvent(
        start=start, end=end, summary=entry["name"],
        description="\n".join(x for x in (notes, f"From AAC: {where}" if where else "") if x) or None,
        location=data.get("location"), uid=entry.get("uid"), recurrence_id=entry.get("recurrence_id"),
    )


def _changes(event: dict[str, Any]) -> dict[str, Any]:
    """HA's event fields → AAC's change fields."""
    changes: dict[str, Any] = {}
    if event.get("summary"):
        changes["name"] = event["summary"]
    if (start := event.get("dtstart")) is not None:
        changes["all_day"] = not isinstance(start, datetime)
        changes["start"] = start.isoformat()
    if (end := event.get("dtend")) is not None:
        changes["end"] = end.isoformat()
    if "description" in event:
        changes["notes"] = event.get("description") or ""
    return changes


class AACCalendar(CalendarEntity):
    """AAC's own calendar entries of one domain."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.UPDATE_EVENT | CalendarEntityFeature.DELETE_EVENT
    )

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize."""
        self.entry = entry
        self.domain = domain
        self._attr_unique_id = f"{entry.entry_id}_calendar_{domain.id}"
        self._attr_name = "Calendar" if domain.first else f"{domain.name} calendar"
        self._attr_device_info = dr.DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name=DEFAULT_NAME)
        self._next: CalendarEvent | None = None

    @property
    def event(self) -> CalendarEvent | None:
        """The next (or current) entry."""
        return self._next

    async def _entries(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        found = await self.entry.runtime_data.capabilities.async_calendar(self.domain.id, start, end)
        return [e for e in found if e.get("source") != HA_SOURCE]

    async def async_update(self) -> None:
        """Refresh the next entry (for the entity's state)."""
        now = dt_util.now()
        events = [ev for e in await self._entries(now - timedelta(days=1), now + timedelta(days=30)) if (ev := _to_event(e))]
        upcoming = sorted((ev for ev in events if ev.end_datetime_local > now), key=lambda ev: ev.start_datetime_local)
        self._next = upcoming[0] if upcoming else None

    async def async_get_events(self, hass: HomeAssistant, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        """Entries in a period (HA's calendar page, the app, get_events)."""
        return [ev for e in await self._entries(start_date, end_date) if (ev := _to_event(e))]

    async def async_create_event(self, **kwargs: Any) -> None:
        """A new event from HA lands on AAC's "Calendar" list."""
        lists = self.entry.runtime_data.capabilities.calendar.get("lists")
        if lists is None or lists.create is None or not self.entry.runtime_data.capabilities.module_enabled(self.domain.id, "lists"):
            raise HomeAssistantError("AAC's lists module is off in this domain; it keeps new calendar entries")
        changes = _changes(kwargs)
        await lists.create(self.domain.id, {"name": changes.get("name", "(no title)"), "start": changes.get("start"),
                                            "end": changes.get("end"), "all_day": changes.get("all_day"),
                                            "data": {"notes": changes["notes"]} if changes.get("notes") else {}})
        await self.async_update_ha_state(force_refresh=True)

    async def async_update_event(self, uid: str, event: dict[str, Any], recurrence_id: str | None = None,
                                 recurrence_range: str | None = None) -> None:
        """Change an entry in its home."""
        try:
            await self.entry.runtime_data.capabilities.async_change(self.domain.id, uid, _changes(event), recurrence_id)
        except (LookupError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_update_ha_state(force_refresh=True)

    async def async_delete_event(self, uid: str, recurrence_id: str | None = None,
                                 recurrence_range: str | None = None) -> None:
        """Take an entry off the calendar in its home."""
        try:
            await self.entry.runtime_data.capabilities.async_remove(self.domain.id, uid, recurrence_id)
        except (LookupError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_update_ha_state(force_refresh=True)
