"""Triggers module: what wakes AAC on its own, the owner's triggers and AAC's own plans (spec/trigger.md).

Moved out of the core in 0.45 (owner, 2026-10-21: "a light and open core"). A trigger watches an
entity, an HA event or a time; when it fires, its card "triggers" sends a message through the graph and the
core's broker queues the run (reminders are time-critical: without a model their text reaches the owner
anyway; event reactions expire). When the model has something to say it lands in "From AAC"; otherwise
it answers NOTHING and stays silent (little noise is a core principle).

AAC plans for itself through the module's tools (plans.py): reminders, check-ins and things to watch,
stored as triggers marked created_by "agent". The owner's setting "own_plans" decides how much it may do
alone. Its plans show in the calendar and in the open loops of every automatic run.

Each domain keeps its triggers in .storage/aac.mod.triggers.triggers[.<domain>]; up to 0.44 they lived
in the AAC config (moved over on the first start).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
import uuid

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from ...broker import SILENT, async_send
from ...capabilities import CalendarProvider, in_quiet_hours, parse_hhmm, parse_time
from ...const import DOMAIN, LOGGER
from ...pools import role_configured
from ...streams import Node
from .. import ModuleContext
from .plans import PlansAPI

if TYPE_CHECKING:
    from ...domains import Domain

NODE = "triggers"
_CTX = f"{DOMAIN}_triggers_ctx"
_ALL = f"{DOMAIN}_triggers"  # hass.data: domain id → Triggers

SILENCE_RULE = f"If nothing needs to be said, answer exactly {SILENT}."


def of(hass: HomeAssistant, domain_id: str | None = None) -> Triggers:
    """A domain's triggers (None = the first domain)."""
    found = hass.data[_ALL]
    return found[domain_id] if domain_id in found else next(iter(found.values()))


def _trigger_v1(trig: dict[str, Any]) -> dict[str, Any]:
    """0.4/0.5 stored flat state triggers; convert them to the contract format."""
    if "when" in trig:
        return trig
    return {
        "id": trig["id"], "name": trig.get("name", ""), "enabled": trig.get("enabled", True),
        "when": {"type": "state", "entity_id": trig.get("entity_id", ""), "to": trig.get("to", "")},
        "do": {"template": "event-reaction", "instruction": trig.get("instruction", "")},
        "cooldown": trig.get("cooldown", 300), "quiet_hours": None, "created_by": "owner", "proposed": False,
    }


class Triggers:
    """One domain's triggers: stored, listened to, fired as requests to the broker."""

    def __init__(self, ctx: ModuleContext, domain: Domain) -> None:
        """Initialize."""
        self.ctx = ctx
        self.hass = ctx.hass
        self.domain = domain
        self.triggers: list[dict[str, Any]] = []
        self._store = ctx.storage(domain, "triggers")
        self._unsubs: list[CALLBACK_TYPE] = []
        self._last_fired: dict[str, datetime] = {}

    async def async_load(self) -> None:
        """Load, or take over the triggers the AAC config kept up to 0.44."""
        stored = await self._store.async_load()
        old = self.domain.settings.data.get("triggers")
        if stored is None and old:
            self.triggers = [_trigger_v1(t) for t in old]
            await self._store.async_save({"triggers": self.triggers})
            LOGGER.info("AAC: %d triggers moved to the triggers module (%s)", len(self.triggers), self.domain.id)
        else:
            self.triggers = (stored or {}).get("triggers", [])
        if "triggers" in self.domain.settings.data:  # only once they are safely in the module's storage
            self.domain.settings.data.pop("triggers")
            await self.domain.settings.async_save()

    def get(self, trigger_id: str) -> dict[str, Any] | None:
        """A trigger by id."""
        return next((t for t in self.triggers if t["id"] == trigger_id), None)

    async def async_save_trigger(self, trigger: dict[str, Any]) -> dict[str, Any]:
        """Add or update a trigger (contract format), and listen to it."""
        existing = self.get(trigger.get("id") or "")
        if existing is None:
            existing = {"id": f"t_{uuid.uuid4().hex[:8]}"}
            self.triggers.append(existing)
        for key in ("name", "enabled", "when", "do", "cooldown", "quiet_hours"):
            existing[key] = trigger.get(key)
        existing["created_by"] = trigger.get("created_by", existing.get("created_by", "owner"))
        existing["proposed"] = bool(trigger.get("proposed", False))
        await self._store.async_save({"triggers": self.triggers})
        self.async_start()
        return existing

    async def async_delete_trigger(self, trigger_id: str) -> None:
        """Remove a trigger."""
        self.triggers = [t for t in self.triggers if t["id"] != trigger_id]
        await self._store.async_save({"triggers": self.triggers})
        self.async_start()

    # ------------------------------------------------------------ listening
    @callback
    def async_start(self) -> None:
        """(Re)subscribe according to the current triggers (only once HA runs: entities exist)."""
        self.async_stop()
        if not self.hass.is_running:
            return
        active = [t for t in self.triggers if t.get("enabled")]
        entity_ids = sorted({t["when"]["entity_id"] for t in active if t["when"]["type"] == "state" and t["when"].get("entity_id")})
        if entity_ids:
            self._unsubs.append(async_track_state_change_event(self.hass, entity_ids, self._async_state_changed))
        for event_type in sorted({t["when"].get("event_type") for t in active if t["when"]["type"] == "event"} - {None, ""}):
            self._unsubs.append(self.hass.bus.async_listen(event_type, self._async_bus_event))
        for trig in active:
            if trig["when"]["type"] == "time" and (at := parse_hhmm(trig["when"].get("at", ""))):
                self._unsubs.append(async_track_time_change(
                    self.hass, self._time_callback(trig["id"]), hour=at.hour, minute=at.minute, second=0))

    @callback
    def async_stop(self) -> None:
        """Unsubscribe everything."""
        while self._unsubs:
            self._unsubs.pop()()

    def _may_fire(self, trig: dict) -> bool:
        """Module on, cooldown, quiet hours, a master model configured."""
        if not self.ctx.enabled(self.domain.id):
            return False
        now = dt_util.utcnow()
        last = self._last_fired.get(trig["id"])
        if last and now - last < timedelta(seconds=int(trig.get("cooldown") or 0)):
            return False
        quiet = trig.get("quiet_hours")
        if quiet and in_quiet_hours(dt_util.now(), quiet.get("start", ""), quiet.get("end", "")):
            return False
        if not role_configured(self.hass, self.ctx.entry.runtime_data.store, "master"):
            return False
        self._last_fired[trig["id"]] = now
        return True

    def request(self, trig: dict, title: str, text: str) -> dict[str, Any]:
        """The broker request for a fired trigger."""
        do = trig.get("do") or {}
        instruction = do.get("instruction") or "tell me if it matters"
        timed = trig["when"]["type"] == "time"
        if timed:
            # A scheduled trigger usually *is* the message (a reminder); silence only
            # makes sense for "check whether ..." instructions
            intro = "A scheduled time has come; nobody asked you anything else."
            rule = ("Follow the instruction now. If it asks you to remind or tell the user something, "
                    f"write that message. Only if it asks you to check something and all is fine, answer exactly {SILENT}.")
        else:
            intro = "An event woke you; nobody asked you anything."
            rule = SILENCE_RULE
        who = "your own earlier instruction" if trig.get("created_by") == "agent" else "the user's instruction"
        name = trig.get("name") or title
        # Job classes: reminders/appointments (time triggers) are high and time-critical: if no model
        # comes in time, the instruction reaches the owner as a plain notification. Events are normal.
        return {
            "trigger": f"event: {trig['id']}", "title": name, "text": text, "intro": intro,
            "context": ["time", "home", "open_loops"], "instructions": f"Instruction ({who}): {instruction}\n{rule}",
            "tier": do.get("tier") or "master", "tools": True,
            "priority": do.get("priority") or ("high" if timed else "normal"),
            "due_minutes": int(do.get("due_minutes") or (30 if timed else 15)),
            "critical": bool(do.get("critical", timed)), "fallback": f"{name}: {instruction}",
            "tags": ["reminder" if timed else "event", *(["own plan"] if trig.get("created_by") == "agent" else [])],
            "coalesce": True, "keep": True,
        }

    def _fire(self, trig: dict, title: str, text: str) -> None:
        self.hass.async_create_background_task(
            async_send(self.hass, self.ctx.entry, self.domain, self.request(trig, title, text), NODE), f"aac trigger {trig['id']}")

    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None:
            return  # removed
        for trig in self.triggers:
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
            self._fire(trig, name,
                       f"Event: {what} changed from {before if before is not None else ('nothing' if attr else 'unknown')} to {after}. "
                       "Does this matter to me right now?")

    @callback
    def _async_bus_event(self, event: Event) -> None:
        """An HA event (any type) that a trigger listens to."""
        for trig in self.triggers:
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
            trig = self.get(trigger_id)
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
                self.hass.async_create_task(self.async_delete_trigger(trig["id"]))
            self._fire(trig, trig.get("name") or "Scheduled",
                       f"It is {trig['when'].get('at')}: time for \"{trig.get('name') or 'your scheduled task'}\".")

        return fired


def plan_occurrences(triggers: list[dict[str, Any]], start: datetime, end: datetime, limit: int = 200) -> list[dict[str, Any]]:
    """AAC's plans and the owner's time triggers as calendar entries (repeating ones once per occurrence)."""
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
                    # open_loop: automatic runs list these themselves (open loops), not again as "coming up"
                    "ref": {"module": "triggers", "trigger": trig["id"], "open_loop": True},
                    "data": {"instruction": (trig.get("do") or {}).get("instruction", ""),
                             "by": trig.get("created_by", "owner"), "repeating": not when.get("date")},
                    "tags": ["plan" if trig.get("created_by") == "agent" else "trigger"],
                })
            if when.get("date"):
                if day.isoformat() >= when["date"]:
                    break
            day += timedelta(days=1)
    return entries


async def async_setup_module(ctx: ModuleContext) -> None:
    """Every domain's triggers, the node, the calendar entries, AAC's planning tools and the page."""
    hass, data = ctx.hass, ctx.entry.runtime_data
    hass.data[_CTX] = ctx
    found = hass.data[_ALL] = {}
    for dom in ctx.domains.values():
        found[dom.id] = Triggers(ctx, dom)
        await found[dom.id].async_load()
        ctx.entry.async_on_unload(found[dom.id].async_stop)
    ctx.add_node(Node(id=NODE, title="Triggers", outputs={"out": "text"}, role="source",
                      description="Your triggers and AAC's own plans: when an entity changes, an HA event happens or a time "
                                  "comes, a message with the trigger's instruction starts here (reminders are time-critical)."))


    async def own_plans(domain_id: str) -> list[str]:
        return [f"- your own scheduled check-in: {t['name']} ({t['when'].get('date') or 'repeating'} at {t['when'].get('at')})"
                for t in of(hass, domain_id).triggers
                if t.get("created_by") == "agent" and t.get("enabled") and t["when"]["type"] == "time"]

    ctx.add_open_loops(own_plans)

    async def plans(domain_id: str, start, end):
        return plan_occurrences(of(hass, domain_id).triggers, start, end)

    def _own_plan(domain_id: str, key: str):
        trig = of(hass, domain_id).get(key)
        if trig is None:
            raise LookupError("That plan no longer exists")
        if trig.get("created_by") != "agent":
            raise LookupError("That is the owner's trigger; change it in the panel")  # agents organize tasks, not rules
        return trig

    async def change_plan(domain_id: str, key: str, changes: dict, recurrence_id: str | None = None):
        # A repeating plan moves as a whole (single occurrences are not kept apart yet)
        trig = _own_plan(domain_id, key)
        when = dict(trig["when"])
        if start := parse_time(changes.get("start")):
            local = dt_util.as_local(start)
            when["at"] = local.strftime("%H:%M")
            if when.get("date"):
                when["date"] = local.date().isoformat()
        saved = await of(hass, domain_id).async_save_trigger({**trig, "when": when, "name": changes.get("name") or trig["name"]})
        return {"name": saved["name"], "when": saved["when"]}

    async def cancel_plan(domain_id: str, key: str, recurrence_id: str | None = None) -> bool:
        await of(hass, domain_id).async_delete_trigger(_own_plan(domain_id, key)["id"])
        return True

    ctx.add_calendar(CalendarProvider("triggers", "AAC's plans and timed triggers", plans, update=change_plan, delete=cancel_plan))
    ctx.add_llm_api(lambda dom, api_id, name: PlansAPI(hass=hass, id=api_id, name=name, ctx=ctx, triggers=found[dom.id]))
    ctx.add_ws_commands(ws_info, ws_save, ws_delete, ws_approve)
    ctx.add_panel_page("list", "Triggers", "aac-triggers-page", "page.js")

    @callback
    def _start(_event: Event | None = None) -> None:
        for triggers in found.values():
            triggers.async_start()

    if hass.is_running:
        _start()
    else:
        ctx.entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start))


def _triggers(hass: HomeAssistant, connection, msg) -> Triggers | None:
    if hass.data.get(_CTX) is None or not hass.config_entries.async_loaded_entries(DOMAIN):
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return None
    return of(hass, msg.get("domain"))


def _info(t: Triggers) -> dict[str, Any]:
    return {"enabled": t.ctx.enabled(t.domain.id), "own_plans": t.ctx.setting(t.domain.id, "own_plans"),
            "triggers": t.triggers}


_TIME = vol.Match(r"^\d{1,2}:\d{2}$")
_WHEN = vol.Any(
    {
        vol.Required("type"): "state",
        vol.Required("entity_id"): vol.All(str, vol.Length(min=3)),
        vol.Optional("attribute", default=""): str,
        vol.Optional("to", default=""): str,
    },
    {
        vol.Required("type"): "event",
        vol.Required("event_type"): vol.All(str, vol.Length(min=2)),
        vol.Optional("event_data", default={}): {str: vol.Any(str, int, float, bool)},
    },
    {
        vol.Required("type"): "time",
        vol.Required("at"): _TIME,
        vol.Optional("weekdays", default=[]): [vol.All(int, vol.Range(0, 6))],
    },
)


@websocket_api.websocket_command({vol.Required("type"): "aac/triggers/info", vol.Optional("domain"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_info(hass: HomeAssistant, connection, msg) -> None:
    """A domain's triggers, for the page."""
    if t := _triggers(hass, connection, msg):
        connection.send_result(msg["id"], _info(t))


@websocket_api.websocket_command({
    vol.Required("type"): "aac/trigger/save",
    vol.Optional("domain"): vol.Any(str, None),
    vol.Optional("trigger_id"): str,
    vol.Required("name"): str,
    vol.Required("when"): _WHEN,
    vol.Optional("instruction", default=""): str,
    vol.Optional("tier", default="master"): vol.All(str, vol.Length(min=1)),
    vol.Optional("cooldown", default=300): vol.All(vol.Coerce(int), vol.Range(0, 86400)),
    vol.Optional("quiet_hours", default=None): vol.Any(None, {vol.Required("start"): _TIME, vol.Required("end"): _TIME}),
    vol.Optional("enabled", default=True): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection, msg) -> None:
    """Add or change a trigger (contract format, spec/trigger.md)."""
    if not (t := _triggers(hass, connection, msg)):
        return
    trig = {
        "name": msg["name"], "enabled": msg["enabled"], "when": msg["when"],
        "do": {"template": "event-reaction", "instruction": msg["instruction"], "tier": msg["tier"]},
        "cooldown": msg["cooldown"], "quiet_hours": msg["quiet_hours"],
    }
    if "trigger_id" in msg:
        trig["id"] = msg["trigger_id"]
        if old := t.get(msg["trigger_id"]):  # the owner editing AAC's plan keeps it AAC's
            trig["created_by"] = old.get("created_by", "owner")
    connection.send_result(msg["id"], await t.async_save_trigger(trig))


@websocket_api.websocket_command({vol.Required("type"): "aac/trigger/delete", vol.Optional("domain"): vol.Any(str, None),
                                  vol.Required("trigger_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection, msg) -> None:
    """Remove a trigger (also: reject a plan AAC proposed)."""
    if t := _triggers(hass, connection, msg):
        await t.async_delete_trigger(msg["trigger_id"])
        connection.send_result(msg["id"], _info(t))


@websocket_api.websocket_command({vol.Required("type"): "aac/trigger/approve", vol.Optional("domain"): vol.Any(str, None),
                                  vol.Required("trigger_id"): str})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_approve(hass: HomeAssistant, connection, msg) -> None:
    """Approve a plan AAC proposed (reject = aac/trigger/delete)."""
    if not (t := _triggers(hass, connection, msg)):
        return
    if (trig := t.get(msg["trigger_id"])) is None:
        connection.send_error(msg["id"], "not_found", "Trigger not found")
        return
    await t.async_save_trigger({**trig, "enabled": True, "proposed": False})
    connection.send_result(msg["id"], _info(t))
