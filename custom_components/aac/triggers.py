"""What wakes the master on its own: the heartbeat and event triggers.

Automatic runs show up under WORKERS → AUTO. When the master has something
to say it lands in the "From AAC" chat; otherwise it answers NOTHING and
stays silent (little noise is a core principle).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING

from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .chats import AAC_CHAT_ID
from .context import home_snapshot
from .const import LOGGER
from .master import SILENT, async_run_master
from .store import CONF_MASTER_MODEL
from .models import resolve_model

if TYPE_CHECKING:
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
    """Schedules the heartbeat and listens for event triggers."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_fired: dict[str, datetime] = {}

    @property
    def _store(self):
        return self.entry.runtime_data.store

    @callback
    def async_start(self) -> None:
        """(Re)subscribe according to the current settings."""
        self.async_stop()
        heartbeat = self._store.heartbeat
        if heartbeat["enabled"]:
            self._unsubs.append(
                async_track_time_interval(
                    self.hass,
                    self._async_heartbeat_tick,
                    timedelta(minutes=max(1, int(heartbeat["interval"]))),
                    name="AAC heartbeat",
                )
            )
        entity_ids = sorted(
            {t["entity_id"] for t in self._store.triggers if t.get("enabled") and t.get("entity_id")}
        )
        if entity_ids:
            self._unsubs.append(
                async_track_state_change_event(self.hass, entity_ids, self._async_state_changed)
            )

    @callback
    def async_stop(self) -> None:
        """Unsubscribe everything."""
        while self._unsubs:
            self._unsubs.pop()()

    def _master_ready(self) -> bool:
        return resolve_model(self.hass, self._store, self._store.master[CONF_MASTER_MODEL]) is not None

    def _busy(self, trigger: str) -> bool:
        return any(r.trigger == trigger for r in self.entry.runtime_data.runs.running("auto"))

    async def _async_heartbeat_tick(self, _now: datetime) -> None:
        await self.async_heartbeat()

    async def async_heartbeat(self, force: bool = False) -> str | None:
        """One heartbeat. Returns the run id, or None if skipped."""
        hb = self._store.heartbeat
        now = dt_util.now()
        if not force and in_quiet_hours(now, hb["quiet_start"], hb["quiet_end"]):
            return None
        if not self._master_ready() or self._busy("heartbeat"):
            return None
        prompt = (
            "Heartbeat: a scheduled check-in, nobody asked you anything.\n"
            f"Local time: {now.strftime('%A %Y-%m-%d %H:%M')}.\n"
            "Current state of the home (area | name | state):\n"
            f"{home_snapshot(self.hass)}\n\n"
            f"{hb['instructions']}"
        )
        return await self._async_auto_run(
            "heartbeat",
            "Heartbeat",
            "Check-in: anything I should know right now?",
            prompt,
            allow_tools=bool(hb["tools"]),
        )

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or (old is not None and old.state == new.state):
            return  # removed, or only attributes changed
        now = dt_util.utcnow()
        for trig in self._store.triggers:
            if not trig.get("enabled") or trig.get("entity_id") != new.entity_id:
                continue
            if trig.get("to") and new.state != trig["to"]:
                continue
            last = self._last_fired.get(trig["id"])
            if last and now - last < timedelta(seconds=int(trig.get("cooldown") or 0)):
                continue
            if not self._master_ready() or self._busy(f"event: {trig['id']}"):
                continue
            self._last_fired[trig["id"]] = now
            name = new.attributes.get("friendly_name", new.entity_id)
            text = (
                f"Event: {name} ({new.entity_id}) changed from "
                f"{old.state if old else 'unknown'} to {new.state}. Does this matter to me right now?"
            )
            prompt = (
                "An event woke you; nobody asked you anything.\n"
                "Current state of the home (area | name | state):\n"
                f"{home_snapshot(self.hass)}\n\n"
                f"The user's instruction for this event: {trig.get('instruction') or 'tell me if it matters'}\n"
                f"{SILENCE_RULE}"
            )
            self.hass.async_create_background_task(
                self._async_auto_run(f"event: {trig['id']}", trig.get("name") or name, text, prompt),
                f"aac trigger {trig['id']}",
            )

    async def _async_auto_run(
        self, trigger: str, title: str, text: str, prompt: str, allow_tools: bool = True
    ) -> str:
        run, result = await async_run_master(
            self.hass,
            self.entry,
            text,
            kind="auto",
            trigger=trigger,
            title=title,
            extra_system_prompt=prompt,
            allow_tools=allow_tools,
        )
        if result is not None and run.status == "done" and run.result:
            run.chat_id = AAC_CHAT_ID
            self.entry.runtime_data.chats.add_message(
                AAC_CHAT_ID, "aac", run.result, proactive=True, run_id=run.id, trigger=title
            )
        LOGGER.debug("Automatic run %s (%s): %s", run.id, trigger, run.status)
        return run.id

