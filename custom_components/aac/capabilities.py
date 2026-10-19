"""Capabilities: shared interfaces that modules provide and use (the core keeps no data for them).

Owner design (2026-10-19): things several modules want to share, like one calendar,
are contracts, not tables. The core only knows the shape and keeps a registry of
providers; each entry stays in the module it comes from. Asking collects from all
providers at that moment, so nothing is copied and removing a module removes its
entries. Repetition is the provider's job: it delivers the occurrences of the
requested period.

Calendar entry (spec/calendar.md): only `name` is required.
    {"name": "Dentist", "start": "2026-10-23T09:00:00+02:00", "end": …, "all_day": false,
     "source": "lists", "ref": {"module": "lists", "list": "…", "item": "…"},
     "links": [{"kind": "memory", "id": "…"}, {"kind": "url", "url": "…"}],
     "data": {…anything…}, "tags": ["appointment"]}
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from .const import LOGGER

# provider(domain_id, start, end) → entries in that period (start/end are aware datetimes)
CalendarQuery = Callable[[str, datetime, datetime], Awaitable[list[dict[str, Any]]]]
# writer(domain_id, entry) → the stored entry
CalendarWrite = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class CalendarProvider:
    """A module that delivers calendar entries (and maybe takes new ones)."""

    id: str
    title: str
    query: CalendarQuery
    create: CalendarWrite | None = None


class Capabilities:
    """The registry. More interfaces (people, places, tasks) follow the same pattern."""

    def __init__(self) -> None:
        """Initialize."""
        self.calendar: dict[str, CalendarProvider] = {}
        # Choices for module settings that change at runtime: name → fn(domain_id) → [(value, label)]
        self.options: dict[str, Callable[[str], list[tuple[str, str]]]] = {}
        # Where new calendar entries go: fn(domain_id) → provider id (None = the first writer)
        self.writer_choice: Callable[[str], str | None] | None = None

    def add_calendar(self, provider: CalendarProvider) -> Callable[[], None]:
        """Register a provider; returns the function that removes it again."""
        self.calendar[provider.id] = provider

        def remove() -> None:
            self.calendar.pop(provider.id, None)

        return remove

    async def async_calendar(self, domain_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """All entries of a period from every provider, sorted by start (entries without a time last)."""
        entries: list[dict[str, Any]] = []
        for provider in list(self.calendar.values()):
            try:
                found = await provider.query(domain_id, start, end)
            except Exception as err:  # noqa: BLE001 - one broken provider must not hide the others
                LOGGER.warning("Calendar provider %s failed: %s", provider.id, err)
                continue
            for entry in found:
                if entry.get("name"):
                    entries.append({"source": provider.id, **entry})
        return sorted(entries, key=lambda e: (e.get("start") is None, str(e.get("start") or "")))

    def writers(self, domain_id: str | None = None) -> list[CalendarProvider]:
        """Providers that accept new entries, the owner's choice first."""
        found = [p for p in self.calendar.values() if p.create]
        chosen = self.writer_choice(domain_id) if self.writer_choice and domain_id else None
        return sorted(found, key=lambda p: p.id != chosen)


def parse_time(value: Any) -> datetime | None:
    """ISO date or datetime → aware datetime (a plain date = local midnight)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt_util.get_default_time_zone())
    text = str(value)
    if len(text) == 10:
        date = dt_util.parse_date(text)
        return dt_util.start_of_local_day(date) if date else None
    parsed = dt_util.parse_datetime(text)
    if parsed and not parsed.tzinfo:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def in_period(entry_start: Any, start: datetime, end: datetime) -> bool:
    """Does an entry's start fall into [start, end)?"""
    when = parse_time(entry_start)
    return when is not None and start <= when < end


def plan_occurrences(triggers: list[dict[str, Any]], start: datetime, end: datetime, limit: int = 200) -> list[dict[str, Any]]:
    """AAC's plans and the owner's time triggers as calendar entries (repeating ones once per occurrence)."""
    from datetime import timedelta  # noqa: PLC0415

    entries: list[dict[str, Any]] = []
    for trig in triggers:
        when = trig.get("when") or {}
        if not trig.get("enabled") or when.get("type") != "time" or not when.get("at"):
            continue
        hour, minute = (int(x) for x in when["at"].split(":"))
        day = dt_util.as_local(start).date()
        while len(entries) < limit:
            moment = dt_util.start_of_local_day(day).replace(hour=hour, minute=minute)
            if moment >= end:
                break
            if moment >= start and (
                (when.get("date") and day.isoformat() == when["date"])
                or (not when.get("date") and (not when.get("weekdays") or day.weekday() in when["weekdays"]))
            ):
                entries.append({
                    "name": trig.get("name") or "Plan", "start": moment.isoformat(), "all_day": False,
                    "ref": {"module": "core", "trigger": trig["id"]},
                    "data": {"instruction": (trig.get("do") or {}).get("instruction", ""),
                             "by": trig.get("created_by", "owner"), "repeating": not when.get("date")},
                    "tags": ["plan" if trig.get("created_by") == "agent" else "trigger"],
                })
            if when.get("date"):
                if day.isoformat() >= when["date"]:
                    break
            day += timedelta(days=1)
    return entries
