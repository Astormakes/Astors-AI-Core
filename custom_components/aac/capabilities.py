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
# changer(domain_id, key, changes, recurrence_id) → the changed entry; changes: name, start, end, all_day, notes
CalendarChange = Callable[[str, str, dict[str, Any], str | None], Awaitable[dict[str, Any]]]
# remover(domain_id, key, recurrence_id) → removed from the calendar?
CalendarRemove = Callable[[str, str, str | None], Awaitable[bool]]


# listener(source, line, earlier) → reacts to a heard line (source: audio_hub.Source, line: the transcript
# record, which the listener may mark: line["addressed"] = True, line["note"] = "…"; earlier: the lines
# heard on that source before it, oldest first)
HearingListener = Callable[[Any, dict[str, Any], list[dict[str, Any]]], Awaitable[None]]


@dataclass
class Stage:
    """A module that works on the audio and adds what it finds to the buffer.

    kind "segment": run(source, pcm, line) for every speech segment, in `order`; returns fields for the line
        (text, language, speaker, …), {"drop": True} to throw the segment away, or None.
        partial(source, pcm) → the text so far while someone still talks (optional, for the live view).
    kind "window": run(source, pcm) every `window` seconds of all audio (speech or not); returns lines of
        their own, e.g. [{"text": "[beeping]", "labels": [...]}] (sound captions).
    """

    id: str
    title: str
    kind: str
    run: Callable[..., Awaitable[Any]]
    order: int = 50
    partial: Callable[[Any, bytes], Awaitable[str | None]] | None = None
    window: float = 1.0
    module: str | None = None


@dataclass
class Hearing:
    """A module that listens to what AAC's microphones hear (wake word, companion, …)."""

    id: str
    title: str
    heard: HearingListener
    module: str | None = None  # set by the loader; only modules on in the voice domain hear


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
        self.hearing: dict[str, Hearing] = {}
        self.stages: dict[str, Stage] = {}
        # Choices for module settings that change at runtime: name → fn(domain_id) → [(value, label)]
        self.options: dict[str, Callable[[str], list[tuple[str, str]]]] = {}
        # Where new calendar entries go: fn(domain_id) → provider id (None = the first writer)
        self.writer_choice: Callable[[str], str | None] | None = None
        # Is a module on in a domain? fn(domain_id, module_id) → bool (set by the module loader)
        self.module_enabled: Callable[[str, str], bool] = lambda domain_id, module_id: True

    def add_hearing(self, listener: Hearing) -> Callable[[], None]:
        """Register a listener for heard lines; returns the function that removes it again."""
        self.hearing[listener.id] = listener

        def remove() -> None:
            self.hearing.pop(listener.id, None)

        return remove

    def add_stage(self, stage: Stage) -> Callable[[], None]:
        """Register an audio stage; returns the function that removes it again."""
        self.stages[stage.id] = stage

        def remove() -> None:
            self.stages.pop(stage.id, None)

        return remove

    def active_stages(self, domain_id: str, kind: str) -> list[Stage]:
        """The stages of a kind whose module is on in that domain (the voice domain), in order."""
        found = [s for s in self.stages.values() if s.kind == kind
                 and (not s.module or self.module_enabled(domain_id, s.module))]
        return sorted(found, key=lambda s: s.order)

    def listeners(self, domain_id: str) -> list[Hearing]:
        """The listeners whose module is on in that domain (the voice domain)."""
        return [h for h in self.hearing.values() if not h.module or self.module_enabled(domain_id, h.module)]

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
                    "key": trig["id"], **({} if when.get("date") else {"recurrence_id": day.isoformat()}),
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
