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
import re
from datetime import datetime, time
from typing import Any

from homeassistant.util import dt as dt_util

from .const import LOGGER

# provider(domain_id, start, end) → entries in that period (start/end are aware datetimes)
CalendarQuery = Callable[[str, datetime, datetime], Awaitable[list[dict[str, Any]]]]
# writer(domain_id, entry) → the stored entry
CalendarWrite = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
# changer(domain_id, key, changes, recurrence_id) → the changed entry; changes: name, start, end, all_day, notes
CalendarChange = Callable[[str, str, dict[str, Any], str | None], Awaitable[dict[str, Any]]]
# remover(domain_id, key, recurrence_id) → removed from the calendar?
CalendarRemove = Callable[[str, str, str | None], Awaitable[bool]]


# open_loops(domain_id) → lines for an automatic run's "open loops" (e.g. AAC's own plans, triggers module)
OpenLoops = Callable[[str], Awaitable[list[str]]]


@dataclass
class CalendarProvider:
    """A module that delivers calendar entries (and maybe takes new ones)."""

    id: str
    title: str
    query: CalendarQuery
    create: CalendarWrite | None = None
    update: CalendarChange | None = None
    delete: CalendarRemove | None = None
    module: str | None = None  # set by the loader; a module switched off in a domain is skipped there


class Capabilities:
    """The registry. More interfaces (people, places, tasks) follow the same pattern."""

    def __init__(self) -> None:
        """Initialize."""
        self.calendar: dict[str, CalendarProvider] = {}
        self.open_loops: dict[str, tuple[OpenLoops, str | None]] = {}  # id → (provider, module)
        # Choices for module settings that change at runtime: name → fn(domain_id) → [(value, label)]
        self.options: dict[str, Callable[[str], list[tuple[str, str]]]] = {}
        # Where new calendar entries go: fn(domain_id) → provider id (None = the first writer)
        self.writer_choice: Callable[[str], str | None] | None = None
        # Is a module on in a domain? fn(domain_id, module_id) → bool (set by the module loader)
        self.module_enabled: Callable[[str, str], bool] = lambda domain_id, module_id: True

    def add_open_loops(self, provider_id: str, provider: OpenLoops, module: str | None = None) -> Callable[[], None]:
        """Register lines for the open loops of automatic runs; returns the function that removes it again."""
        self.open_loops[provider_id] = (provider, module)

        def remove() -> None:
            self.open_loops.pop(provider_id, None)

        return remove

    async def async_open_loops(self, domain_id: str) -> list[str]:
        """Every provider's lines (modules switched off in that domain are left out)."""
        lines: list[str] = []
        for provider_id, (provider, module) in list(self.open_loops.items()):
            if module and not self.module_enabled(domain_id, module):
                continue
            try:
                lines += await provider(domain_id)
            except Exception as err:  # noqa: BLE001 - one broken provider must not hide the others
                LOGGER.warning("Open loops provider %s failed: %s", provider_id, err)
        return lines

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
            if provider.module and not self.module_enabled(domain_id, provider.module):
                continue
            try:
                found = await provider.query(domain_id, start, end)
            except Exception as err:  # noqa: BLE001 - one broken provider must not hide the others
                LOGGER.warning("Calendar provider %s failed: %s", provider.id, err)
                continue
            for entry in found:
                if entry.get("name"):
                    # Like HA/iCalendar: a uid per entry ("<source>:<key>", the key is the provider's own)
                    uid = f"{provider.id}:{entry['key']}" if entry.get("key") else None
                    entries.append({"source": provider.id, "uid": uid,
                                    **{k: v for k, v in entry.items() if k != "key"}})
        return sorted(entries, key=lambda e: (e.get("start") is None, str(e.get("start") or "")))

    def _home(self, domain_id: str, uid: str) -> tuple[CalendarProvider, str]:
        source, _, key = uid.partition(":")
        provider = self.calendar.get(source)
        if not key or provider is None or (provider.module and not self.module_enabled(domain_id, provider.module)):
            raise LookupError(f"No calendar entry '{uid}' here")
        return provider, key

    async def async_change(self, domain_id: str, uid: str, changes: dict[str, Any],
                           recurrence_id: str | None = None) -> dict[str, Any]:
        """Change an entry in its home (the module it comes from); uid = "<source>:<key>"."""
        provider, key = self._home(domain_id, uid)
        if provider.update is None:
            raise LookupError(f"{provider.title} cannot change entries")
        return await provider.update(domain_id, key, changes, recurrence_id)

    async def async_remove(self, domain_id: str, uid: str, recurrence_id: str | None = None) -> bool:
        """Remove an entry from the calendar, in its home."""
        provider, key = self._home(domain_id, uid)
        if provider.delete is None:
            raise LookupError(f"{provider.title} cannot remove entries")
        return await provider.delete(domain_id, key, recurrence_id)

    def writers(self, domain_id: str | None = None) -> list[CalendarProvider]:
        """Providers that accept new entries, the owner's choice first."""
        found = [p for p in self.calendar.values()
                 if p.create and not (p.module and domain_id and not self.module_enabled(domain_id, p.module))]
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


def parse_hhmm(value: str) -> time | None:
    """'HH:MM' → time (None when it isn't one)."""
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """True between start and end (local time, may wrap midnight; the same time twice = never)."""
    t_start, t_end = parse_hhmm(start), parse_hhmm(end)
    if t_start is None or t_end is None or t_start == t_end:
        return False
    current = now.time()
    if t_start < t_end:
        return t_start <= current < t_end
    return current >= t_start or current < t_end


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
