"""Lists module and the unified calendar."""

from datetime import datetime, timedelta
import json

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.aac.capabilities import plan_occurrences

from .conftest import setup_aac
from .test_planning import _chat, _queue, _text, _tool_reply


def _call(name: str, args: dict) -> dict:
    return _tool_reply(name, json.dumps(args))


async def test_agent_builds_a_recipe_and_a_shopping_list(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Nested entries with fields; ticking off; the recipe's ingredients go to the shopping list."""
    await setup_aac(hass, mock_entry)
    _queue(
        aioclient_mock,
        _call("aac-lists__add_items", {"list": "Chili con carne", "items": [
            {"text": "Ingredients", "children": [{"text": "Minced beef", "fields": {"qty": "500 g"}}, "Kidney beans"]},
            {"text": "Steps", "children": ["Brown the meat"]}]}),
        _call("aac-lists__add_items", {"list": "Shopping", "items": [{"text": "Minced beef", "fields": {"qty": "500 g"}}]}),
        _call("aac-lists__update_item", {"list": "Shopping", "item": "minced beef", "done": True}),
        _text("Done."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "I want to make chili con carne; keep the recipe and put the meat on my shopping list.")
    assert not any(s.get("error") for s in message["steps"] if s["type"] == "tool_result")
    await ws.send_json_auto_id({"type": "aac/lists"})
    lists = {lst["name"]: lst for lst in (await ws.receive_json())["result"]["lists"]}
    chili = lists["Chili con carne"]["items"]
    assert [e["text"] for e in chili] == ["Ingredients", "Steps"]
    assert chili[0]["children"][0]["fields"] == {"qty": "500 g"} and chili[0]["children"][1]["text"] == "Kidney beans"
    assert lists["Shopping"]["items"][0]["done"] is True


async def test_dates_in_lists_reach_the_calendar(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """An entry with fields.when is a calendar entry; add_to_calendar lands in the Calendar list."""
    await setup_aac(hass, mock_entry)
    soon = (dt_util.now() + timedelta(days=2)).strftime("%Y-%m-%dT10:00")
    payloads = _queue(
        aioclient_mock,
        _call("aac-lists__add_items", {"list": "Projects", "items": [{"text": "Drone parts arrive", "fields": {"when": soon[:10]}}]}),
        _call("aac-planning__add_to_calendar", {"name": "Dentist", "start": soon}),
        _call("aac-planning__calendar", {}),
        _text("You have two things."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "What's coming up?")
    result = [s for s in message["steps"] if s["type"] == "tool_result"][-1]
    assert "Drone parts arrive" in result["data"] and "Dentist" in result["data"]
    await ws.send_json_auto_id({"type": "aac/lists"})
    names = [lst["name"] for lst in (await ws.receive_json())["result"]["lists"]]
    assert names == ["Projects", "Calendar"]
    assert payloads


async def test_panel_operations(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """Create, add (nested), tick, move, remove, delete from the panel."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)

    async def op(**kw):
        await ws.send_json_auto_id({"type": "aac/lists/op", **kw})
        reply = await ws.receive_json()
        assert reply["success"], reply
        return reply["result"]["lists"]

    lst = (await op(op="create", name="Packing", view="outline"))[0]
    lists = await op(op="add", list=lst["id"], text="Clothes")
    clothes = lists[0]["items"][0]["id"]
    await op(op="add", list="Packing", text="Socks", parent=clothes)
    lists = await op(op="add", list="Packing", text="Charger")
    await op(op="update", list="Packing", item="Charger", done=True, fields={"bag": "small"})
    lists = await op(op="move", list="Packing", item="Charger", parent=clothes)
    assert [c["text"] for c in lists[0]["items"][0]["children"]] == ["Socks", "Charger"]
    lists = await op(op="remove", list="Packing", item="Clothes")
    assert lists[0]["items"] == []
    assert await op(op="delete", list="Packing") == []


def test_repeating_plans_become_occurrences():
    """A weekday plan at 07:30 shows up five times in a week; a one-off once."""
    start = dt_util.start_of_local_day(datetime(2026, 10, 19))  # a Monday
    triggers = [
        {"id": "a", "name": "Weigh-in", "enabled": True, "created_by": "agent",
         "when": {"type": "time", "at": "07:30", "weekdays": [0, 1, 2, 3, 4]}, "do": {"instruction": "Ask."}},
        {"id": "b", "name": "Call mum", "enabled": True,
         "when": {"type": "time", "at": "18:00", "date": "2026-10-21", "weekdays": []}, "do": {}},
    ]
    entries = plan_occurrences(triggers, start, start + timedelta(days=7))
    assert sum(e["name"] == "Weigh-in" for e in entries) == 5
    assert [e["start"][:16] for e in entries if e["name"] == "Call mum"] == ["2026-10-21T18:00"]


async def test_ha_calendars_and_module_settings(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """HA calendars are read and written through their actions; settings choose where entries go."""
    from homeassistant.core import SupportsResponse

    created = []
    day = (dt_util.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    async def get_events(call):
        return {eid: {"events": [{"summary": "Team meeting", "start": f"{day}T09:00:00+00:00",
                                  "end": f"{day}T10:00:00+00:00", "location": "Office"}]} for eid in call.data["entity_id"]}

    async def create_event(call):
        created.append(dict(call.data))

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    hass.services.async_register("calendar", "create_event", create_event)
    hass.states.async_set("calendar.work", "off", {"friendly_name": "Work", "supported_features": 1})
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": "aac/calendar/agenda", "days": 7})
    agenda = (await ws.receive_json())["result"]["entries"]
    assert [(e["name"], e["source"], e["data"]) for e in agenda] == [("Team meeting", "calendar", {"location": "Office"})]

    await ws.send_json_auto_id({"type": "aac/modules"})
    mods = {m["id"]: m for m in (await ws.receive_json())["result"]["modules"]}
    assert ["calendar.work", "HA: Work"] in mods["calendar"]["settings"]["new_entries"]["options"]
    assert mods["lists"]["values"]["in_calendar"] is True

    await ws.send_json_auto_id({"type": "aac/module/settings", "module": "calendar", "values": {"new_entries": "calendar.work"}})
    assert (await ws.receive_json())["result"]["values"]["new_entries"] == "calendar.work"
    _queue(aioclient_mock, _call("aac-planning__add_to_calendar", {"name": "Dentist", "start": f"{day}T14:00"}), _text("Done."))
    await _chat(ws, "Put the dentist on my calendar tomorrow at 14:00.")
    assert created and created[0]["summary"] == "Dentist" and created[0]["entity_id"] == "calendar.work"

    await ws.send_json_auto_id({"type": "aac/module/settings", "module": "calendar", "values": {"show_page": False}})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/state"})
    pages = {m["id"]: m["pages"] for m in (await ws.receive_json())["result"]["modules"]}
    assert pages["calendar"] == [] and pages["lists"]
