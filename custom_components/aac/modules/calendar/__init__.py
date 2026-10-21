"""Calendar module: HA's calendars as a calendar provider, the owner's choice where new entries go,
and an agenda page over everything the providers know.

HA already unifies calendars: every integration (Google, CalDAV, Local Calendar, …) is a
`calendar.*` entity with the actions get_events and create_event, so one adapter covers them all.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.calendar import CalendarEntityFeature
from homeassistant.components.calendar.const import DATA_COMPONENT as CALENDAR_COMPONENT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ...capabilities import CalendarProvider, parse_time
from ...const import DOMAIN, LOGGER
from .. import ModuleContext, setting
from .tools import CalendarAPI


def _foreign(hass: HomeAssistant) -> list[str]:
    """HA's calendars without AAC's own (reading those would ask AAC about itself, in a loop)."""
    ent_reg = er.async_get(hass)
    return sorted(e for e in hass.states.async_entity_ids("calendar")
                  if not ((reg := ent_reg.async_get(e)) and reg.platform == DOMAIN))


def _calendars(hass: HomeAssistant, chosen: Any) -> list[str]:
    ids = _foreign(hass)
    return ids if chosen in (None, "all") else [e for e in ids if e in (chosen or [])]


async def async_setup_module(ctx: ModuleContext) -> None:
    """Register the HA calendar adapter, the target choices and the agenda page."""
    hass = ctx.hass

    def _iso(value: Any) -> str | None:
        when = parse_time(value)
        return when.isoformat() if when else None

    async def query(domain_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        entity_ids = _calendars(hass, ctx.setting(domain_id, "read"))
        if not entity_ids:
            return []
        component = hass.data.get(CALENDAR_COMPONENT)
        entries = []
        for entity_id in entity_ids:
            state = hass.states.get(entity_id)
            label = state.attributes.get("friendly_name", entity_id) if state else entity_id
            if component and (entity := component.get_entity(entity_id)):
                # Straight from the entity: with uid and recurrence_id (HA's get_events action leaves them out)
                events = [dataclasses.asdict(e) for e in await entity.async_get_events(hass, start, end)]
            else:
                result = await hass.services.async_call(
                    "calendar", "get_events", {"entity_id": [entity_id], "start_date_time": start, "end_date_time": end},
                    blocking=True, return_response=True,
                )
                events = (result or {}).get(entity_id, {}).get("events", [])
            for event in events:
                uid = event.get("uid")
                entries.append({
                    **({"key": f"{entity_id}/{uid}"} if uid else {}),
                    **({"recurrence_id": event["recurrence_id"]} if event.get("recurrence_id") else {}),
                    "name": event.get("summary") or "(no title)",
                    "start": _iso(event.get("start")), "end": _iso(event.get("end")),
                    "all_day": not isinstance(event.get("start"), datetime) and len(str(event.get("start", ""))) == 10,
                    "data": {k: event[k] for k in ("description", "location") if event.get(k)},
                    "tags": [label],
                })
        return entries

    async def create(domain_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        target = ctx.setting(domain_id, "new_entries")
        if not str(target).startswith("calendar."):
            raise ValueError("No Home Assistant calendar chosen for new entries")
        start = parse_time(entry.get("start")) or dt_util.start_of_local_day()
        all_day = bool(entry.get("all_day")) or len(str(entry.get("start", ""))) == 10
        data: dict[str, Any] = {"entity_id": target, "summary": entry["name"]}
        if all_day:
            end = parse_time(entry.get("end")) or start + timedelta(days=1)
            data |= {"start_date": start.date().isoformat(), "end_date": end.date().isoformat()}
        else:
            data |= {"start_date_time": start, "end_date_time": parse_time(entry.get("end")) or start + timedelta(hours=1)}
        if notes := (entry.get("data") or {}).get("notes"):
            data["description"] = notes
        component = hass.data.get(CALENDAR_COMPONENT)
        if component and (entity := component.get_entity(target)):
            # Straight to the entity (as HA's own calendar page does)
            await entity.async_create_event(
                summary=entry["name"],
                dtstart=start.date() if all_day else start,
                dtend=(parse_time(entry.get("end")) or start + timedelta(days=1)).date() if all_day
                else parse_time(entry.get("end")) or start + timedelta(hours=1),
                **({"description": data["description"]} if "description" in data else {}),
            )
        else:
            await hass.services.async_call("calendar", "create_event", data, blocking=True)
        return {"name": entry["name"], "start": start.isoformat(), "calendar": target}

    def _entity(key: str, feature: CalendarEntityFeature):
        entity_id, _, uid = key.partition("/")  # entity ids have no "/", event uids may
        component = hass.data.get(CALENDAR_COMPONENT)
        entity = component.get_entity(entity_id) if component else None
        if entity is None or not uid:
            raise LookupError("That calendar entry cannot be found")
        if not (entity.supported_features or 0) & feature:
            raise LookupError(f"{entity.name or entity_id} does not allow that")
        return entity, entity_id, uid

    async def update(domain_id: str, key: str, changes: dict[str, Any], recurrence_id: str | None = None) -> dict[str, Any]:
        entity, entity_id, uid = _entity(key, CalendarEntityFeature.UPDATE_EVENT)
        event: dict[str, Any] = {}
        if changes.get("name"):
            event["summary"] = changes["name"]
        if start := parse_time(changes.get("start")):
            all_day = bool(changes.get("all_day")) or len(str(changes["start"])) == 10
            end = parse_time(changes.get("end")) or (start + (timedelta(days=1) if all_day else timedelta(hours=1)))
            event |= {"dtstart": start.date() if all_day else start, "dtend": end.date() if all_day else end}
        if "notes" in changes:
            event["description"] = changes["notes"]
        if "summary" not in event:  # HA wants the whole event
            event["summary"] = changes.get("current_name") or "(no title)"
        await entity.async_update_event(uid, event, recurrence_id=recurrence_id)
        return {"name": event["summary"], "calendar": entity_id}

    async def delete(domain_id: str, key: str, recurrence_id: str | None = None) -> bool:
        entity, _, uid = _entity(key, CalendarEntityFeature.DELETE_EVENT)
        await entity.async_delete_event(uid, recurrence_id=recurrence_id)
        return True

    caps = ctx.entry.runtime_data.capabilities
    ctx.add_calendar(CalendarProvider("calendar", "Home Assistant calendars", query, create, update, delete))

    def targets(domain_id: str) -> list[tuple[str, str]]:
        found = [("lists", "A list called \"Calendar\" (lists module)")]
        for state in (hass.states.get(e) for e in _foreign(hass)):
            if state and state.attributes.get("supported_features", 0) & CalendarEntityFeature.CREATE_EVENT:
                found.append((state.entity_id, f"HA: {state.attributes.get('friendly_name', state.entity_id)}"))
        return found

    caps.options["calendar_targets"] = targets
    caps.writer_choice = lambda domain_id: (
        "calendar" if str(ctx.setting(domain_id, "new_entries")).startswith("calendar.") else "lists"
    )

    def _reset() -> None:
        caps.options.pop("calendar_targets", None)
        caps.writer_choice = None

    ctx.entry.async_on_unload(_reset)
    ctx.add_llm_api(lambda dom, api_id, name: CalendarAPI(hass=hass, id=api_id, name=name, entry=ctx.entry, domain=dom))
    ctx.add_ws_commands(ws_agenda)
    ctx.add_panel_page("agenda", "Calendar", "aac-calendar-page", "page.js", visible_setting="show_page")


@websocket_api.websocket_command(
    {vol.Required("type"): "aac/calendar/agenda", vol.Optional("domain"): vol.Any(str, None),
     vol.Optional("days"): vol.All(int, vol.Range(1, 120)), vol.Optional("start"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_agenda(hass: HomeAssistant, connection, msg) -> None:
    """Everything the calendar providers know from today on."""
    entries_ = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries_:
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return
    entry = entries_[0]
    dom = entry.runtime_data.domain(msg.get("domain")) or entry.runtime_data.domain()
    module = next(m for m in entry.runtime_data.modules if m.id == "calendar")
    view = setting(entry, module.manifest, dom.id, "view")
    days = msg.get("days") or int(setting(entry, module.manifest, dom.id, "days") or 7)
    first = dt_util.parse_date(msg["start"]) if msg.get("start") else None
    start = dt_util.start_of_local_day(first) if first else dt_util.start_of_local_day()
    try:
        found = await entry.runtime_data.capabilities.async_calendar(dom.id, start, start + timedelta(days=days))
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("Agenda failed: %s", err)
        found = []
    connection.send_result(msg["id"], {"from": start.isoformat(), "days": days, "view": view, "entries": found})
