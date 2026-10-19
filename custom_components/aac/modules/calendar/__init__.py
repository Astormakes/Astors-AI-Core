"""Calendar module: HA's calendars as a calendar provider, the owner's choice where new entries go,
and an agenda page over everything the providers know.

HA already unifies calendars: every integration (Google, CalDAV, Local Calendar, …) is a
`calendar.*` entity with the actions get_events and create_event, so one adapter covers them all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.calendar import CalendarEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...capabilities import CalendarProvider, parse_time
from ...const import DOMAIN, LOGGER
from .. import ModuleContext


def _calendars(hass: HomeAssistant, chosen: Any) -> list[str]:
    ids = sorted(hass.states.async_entity_ids("calendar"))
    return ids if chosen in (None, "all") else [e for e in ids if e in (chosen or [])]


async def async_setup_module(ctx: ModuleContext) -> None:
    """Register the HA calendar adapter, the target choices and the agenda page."""
    hass = ctx.hass

    async def query(domain_id: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        entity_ids = _calendars(hass, ctx.setting(domain_id, "read"))
        if not entity_ids:
            return []
        result = await hass.services.async_call(
            "calendar", "get_events", {"entity_id": entity_ids, "start_date_time": start, "end_date_time": end},
            blocking=True, return_response=True,
        )
        entries = []
        for entity_id, data in (result or {}).items():
            state = hass.states.get(entity_id)
            label = state.attributes.get("friendly_name", entity_id) if state else entity_id
            for event in data.get("events", []):
                entries.append({
                    "name": event.get("summary") or "(no title)",
                    "start": parse_time(event.get("start")).isoformat() if parse_time(event.get("start")) else None,
                    "end": parse_time(event.get("end")).isoformat() if parse_time(event.get("end")) else None,
                    "all_day": len(str(event.get("start", ""))) == 10,
                    "ref": {"module": "calendar", "entity_id": entity_id, "uid": event.get("uid")},
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
        await hass.services.async_call("calendar", "create_event", data, blocking=True)
        return {"name": entry["name"], "start": start.isoformat(), "calendar": target}

    caps = ctx.entry.runtime_data.capabilities
    ctx.add_calendar(CalendarProvider("calendar", "Home Assistant calendars", query, create))

    def targets(domain_id: str) -> list[tuple[str, str]]:
        found = [("lists", "A list called \"Calendar\" (lists module)")]
        for state in hass.states.async_all("calendar"):
            if state.attributes.get("supported_features", 0) & CalendarEntityFeature.CREATE_EVENT:
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
    ctx.add_ws_commands(ws_agenda)
    ctx.add_panel_page("agenda", "Calendar", "aac-calendar-page", "page.js", visible_setting="show_page")


@websocket_api.websocket_command(
    {vol.Required("type"): "aac/calendar/agenda", vol.Optional("domain"): vol.Any(str, None),
     vol.Optional("days", default=14): vol.All(int, vol.Range(1, 120))}
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
    start = dt_util.start_of_local_day()
    try:
        found = await entry.runtime_data.capabilities.async_calendar(dom.id, start, start + timedelta(days=msg["days"]))
    except Exception as err:  # noqa: BLE001
        LOGGER.warning("Agenda failed: %s", err)
        found = []
    connection.send_result(msg["id"], {"from": start.isoformat(), "entries": found})
