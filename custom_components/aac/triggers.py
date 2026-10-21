"""What wakes the master on its own: the owner's and AAC's own triggers (spec/trigger.md).

Automatic runs show up under CHATS → Auto. When the master has something to say it lands in the
"From AAC" chat; otherwise it answers NOTHING and stays silent (little noise is a core principle).
The heartbeat is a module now (modules/heartbeat, on the stream bus); triggers follow it there.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .context import home_snapshot
from .jobs import Job
from .master import SILENT
from .master_node import async_auto_run, async_open_loops
from .pools import role_configured

if TYPE_CHECKING:
    from .domains import Domain
    from . import AACConfigEntry

SILENCE_RULE = f"If nothing needs to be said, answer exactly {SILENT}."


def _parse(value: str) -> time | None:
    try:
        hour, minute = value.split(":")
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return None


def in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    """True between start and end (local time, may wrap midnight)."""
    t_start, t_end = _parse(start), _parse(end)
    if t_start is None or t_end is None or t_start == t_end:
        return False
    current = now.time()
    if t_start < t_end:
        return t_start <= current < t_end
    return current >= t_start or current < t_end



class TriggerEngine:
    """Listens for event and time triggers."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize (one engine per AAC domain)."""
        self.hass = hass
        self.entry = entry
        self.domain = domain
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_fired: dict[str, datetime] = {}

    @property
    def _store(self):
        return self.domain.settings

    @callback
    def async_start(self) -> None:
        """(Re)subscribe according to the current settings."""
        self.async_stop()
        active = [t for t in self._store.triggers if t.get("enabled")]
        entity_ids = sorted(
            {t["when"]["entity_id"] for t in active if t["when"]["type"] == "state" and t["when"].get("entity_id")}
        )
        if entity_ids:
            self._unsubs.append(
                async_track_state_change_event(self.hass, entity_ids, self._async_state_changed)
            )
        for event_type in sorted({t["when"].get("event_type") for t in active if t["when"]["type"] == "event"} - {None, ""}):
            self._unsubs.append(self.hass.bus.async_listen(event_type, self._async_bus_event))
        for trig in active:
            if trig["when"]["type"] == "time" and (at := _parse(trig["when"].get("at", ""))):
                self._unsubs.append(
                    async_track_time_change(
                        self.hass,
                        self._time_callback(trig["id"]),
                        hour=at.hour,
                        minute=at.minute,
                        second=0,
                    )
                )

    @callback
    def async_stop(self) -> None:
        """Unsubscribe everything."""
        while self._unsubs:
            self._unsubs.pop()()

    def _master_ready(self) -> bool:
        return role_configured(self.hass, self.entry.runtime_data.store, "master")

    def _busy(self, trigger: str) -> bool:
        return any(r.trigger == trigger for r in self.domain.runs.running("auto"))

    def _may_fire(self, trig: dict) -> bool:
        """Cooldown, quiet hours, master ready, not already running."""
        now = dt_util.utcnow()
        last = self._last_fired.get(trig["id"])
        if last and now - last < timedelta(seconds=int(trig.get("cooldown") or 0)):
            return False
        quiet = trig.get("quiet_hours")
        if quiet and in_quiet_hours(dt_util.now(), quiet.get("start", ""), quiet.get("end", "")):
            return False
        if not self._master_ready() or self._busy(f"event: {trig['id']}"):
            return False
        self._last_fired[trig["id"]] = now
        return True

    def _fire(self, trig: dict, title: str, text: str) -> None:
        self.hass.async_create_background_task(
            self._async_fire(trig, title, text), f"aac trigger {trig['id']}"
        )

    async def _async_fire(self, trig: dict, title: str, text: str) -> None:
        await self.entry.runtime_data.jobs.async_submit(self.trigger_job(trig, title, text))

    def trigger_job(self, trig: dict, title: str, text: str, saved: dict | None = None) -> Job:
        """The job for a fired trigger (also rebuilt from storage after a restart: `saved`)."""
        instruction = (trig.get("do") or {}).get("instruction") or "tell me if it matters"
        if trig["when"]["type"] == "time":
            # A scheduled trigger usually *is* the message (a reminder); silence only
            # makes sense for "check whether ..." instructions
            intro = "A scheduled time has come; nobody asked you anything else."
            rule = (
                "Follow the instruction now. If it asks you to remind or tell the user something, "
                f"write that message. Only if it asks you to check something and all is fine, answer exactly {SILENT}."
            )
        else:
            intro = "An event woke you; nobody asked you anything."
            rule = SILENCE_RULE
        who = "your own earlier instruction" if trig.get("created_by") == "agent" else "the user's instruction"
        do = trig.get("do") or {}
        name = trig.get("name") or title
        tier = do.get("tier") or "master"

        async def start(note: str = ""):
            prompt = (
                f"{intro}\n"
                f"Local time: {dt_util.now().strftime('%A %Y-%m-%d %H:%M')}.\n"
                "Current state of the home (area | name | state):\n"
                f"{home_snapshot(self.hass, self.domain.settings)}"
                f"{await async_open_loops(self.entry, self.domain)}\n\n"
                f"Instruction ({who}): {instruction}\n"
                f"{rule}"
                f"{chr(10) + note if note else ''}"
            )
            return await async_auto_run(self.hass, self.entry, self.domain, f"event: {trig['id']}", name, text, prompt, tier=tier)

        # Job classes: reminders/appointments (time triggers) are high and time-critical: if no model
        # comes in time, the instruction reaches the owner as a plain notification. Events are normal.
        timed = trig["when"]["type"] == "time"
        job = Job(
            self.domain, name, tier, do.get("priority") or ("high" if timed else "normal"), start,
            due=dt_util.utcnow() + timedelta(minutes=int(do.get("due_minutes") or (30 if timed else 15))),
            critical=bool(do.get("critical", timed)),
            fallback=f"{name}: {instruction}",
            tags=["reminder" if timed else "event", *(["own plan"] if trig.get("created_by") == "agent" else [])],
            # Enough to rebuild it after a restart (one-off triggers are already gone by then)
            saved={"kind": "trigger", "trigger": trig, "title": title, "text": text},
        )
        if saved:  # restored: keep its original times
            job.id, job.created = saved["id"], dt_util.parse_datetime(saved["created"])
            job.due = dt_util.parse_datetime(saved["due"]) if saved.get("due") else None
        return job

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None:
            return  # removed
        for trig in self._store.triggers:
            when = trig["when"]
            if not trig.get("enabled") or when["type"] != "state" or when.get("entity_id") != new.entity_id:
                continue
            # A state trigger watches the state, or one attribute (e.g. the game on a Steam sensor)
            attr = when.get("attribute")
            before = (old.attributes.get(attr) if attr else old.state) if old is not None else None
            after = new.attributes.get(attr) if attr else new.state
            if old is not None and before == after:
                continue  # nothing changed in what this trigger watches
            if when.get("to") and str(after) != when["to"]:
                continue
            if not self._may_fire(trig):
                continue
            name = new.attributes.get("friendly_name", new.entity_id)
            what = f"{name} ({new.entity_id}){f' attribute {attr}' if attr else ''}"
            self._fire(
                trig,
                name,
                f"Event: {what} changed from {before if before is not None else ('nothing' if attr else 'unknown')} to {after}. "
                "Does this matter to me right now?",
            )

    @callback
    def _async_bus_event(self, event: Event) -> None:
        """An HA event (any type) that an AAC trigger listens to."""
        for trig in self._store.triggers:
            when = trig["when"]
            if not trig.get("enabled") or when["type"] != "event" or when.get("event_type") != event.event_type:
                continue
            wanted = when.get("event_data") or {}
            if any(str(event.data.get(k)) != str(v) for k, v in wanted.items()):
                continue
            if not self._may_fire(trig):
                continue
            data = {k: v for k, v in event.data.items() if isinstance(v, (str, int, float, bool)) or v is None}
            self._fire(trig, event.event_type,
                       f"Event: Home Assistant event {event.event_type} with {data}. Does this matter to me right now?")

    def _time_callback(self, trigger_id: str):
        @callback
        def fired(now: datetime) -> None:
            trig = next((t for t in self._store.triggers if t["id"] == trigger_id), None)
            if trig is None or not trig.get("enabled"):
                return
            local = dt_util.as_local(now)
            weekdays = trig["when"].get("weekdays")  # 0 = Monday … 6 = Sunday; empty = every day
            if weekdays and local.weekday() not in weekdays:
                return
            once = trig["when"].get("date")  # one-off: only on this date, then removed
            if once and local.date().isoformat() != once:
                return
            if not self._may_fire(trig):
                return
            if once:
                self.hass.async_create_task(self._async_remove(trig["id"]))
            self._fire(
                trig,
                trig.get("name") or "Scheduled",
                f"It is {trig['when'].get('at')}: time for \"{trig.get('name') or 'your scheduled task'}\".",
            )

        return fired

    async def _async_remove(self, trigger_id: str) -> None:
        await self._store.async_delete_trigger(trigger_id)
        self.async_start()

