"""Lists module and the unified calendar."""

from datetime import datetime, timedelta
import json

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.aac.modules.triggers import plan_occurrences

from .conftest import setup_aac, triggers_of
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
        _call("aac-calendar__add_to_calendar", {"name": "Dentist", "start": soon}),
        _call("aac-calendar__calendar", {}),
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
    """HA calendars are read and written through their entities; settings choose where entries go."""
    from unittest.mock import patch

    from homeassistant.components.calendar import CalendarEvent
    from homeassistant.components.calendar.const import DATA_COMPONENT

    created = []
    day = dt_util.start_of_local_day() + timedelta(days=1)

    class Work:
        name = "Work"
        supported_features = 1

        async def async_get_events(self, hass, start, end):
            return [CalendarEvent(start=day.replace(hour=9), end=day.replace(hour=10), summary="Team meeting",
                                  location="Office", uid="m1")]

        async def async_create_event(self, **kwargs):
            created.append(kwargs)

    hass.states.async_set("calendar.work", "off", {"friendly_name": "Work", "supported_features": 1})
    await setup_aac(hass, mock_entry)
    component = hass.data[DATA_COMPONENT]
    real = component.get_entity
    with patch.object(component, "get_entity", side_effect=lambda eid: Work() if eid == "calendar.work" else real(eid)):
        ws = await hass_ws_client(hass)
        await ws.send_json_auto_id({"type": "aac/calendar/agenda", "days": 7})
        agenda = (await ws.receive_json())["result"]["entries"]
        assert [(e["name"], e["source"], e["uid"], e["data"]) for e in agenda] == [
            ("Team meeting", "calendar", "calendar:calendar.work/m1", {"location": "Office"})]

        await ws.send_json_auto_id({"type": "aac/modules"})
        mods = {m["id"]: m for m in (await ws.receive_json())["result"]["modules"]}
        options = mods["calendar"]["settings"]["new_entries"]["options"]
        assert ["calendar.work", "HA: Work"] in options
        assert not any(o[0].startswith("calendar.aac") for o in options)  # AAC's own calendar is not a target
        assert mods["lists"]["values"]["in_calendar"] is True

        await ws.send_json_auto_id({"type": "aac/module/settings", "module": "calendar", "values": {"new_entries": "calendar.work"}})
        assert (await ws.receive_json())["result"]["values"]["new_entries"] == "calendar.work"
        _queue(aioclient_mock, _call("aac-calendar__add_to_calendar", {"name": "Dentist", "start": day.strftime("%Y-%m-%dT14:00")}), _text("Done."))
        await _chat(ws, "Put the dentist on my calendar tomorrow at 14:00.")
        assert created and created[0]["summary"] == "Dentist" and created[0]["dtstart"].hour == 14

    await ws.send_json_auto_id({"type": "aac/module/settings", "module": "calendar", "values": {"show_page": False}})
    await ws.receive_json()
    await ws.send_json_auto_id({"type": "aac/state"})
    pages = {m["id"]: m["pages"] for m in (await ws.receive_json())["result"]["modules"]}
    assert pages["calendar"] == [] and pages["lists"]


async def test_calendar_entries_have_uids_and_change_in_their_home(hass: HomeAssistant, mock_entry, configured):
    """Entries carry "<source>:<key>" uids; changing/removing goes to the entry's home (HA entity, list, own plan)."""
    from homeassistant.components.calendar import CalendarEvent
    from homeassistant.components.calendar.const import DATA_COMPONENT

    day = dt_util.start_of_local_day() + timedelta(days=1)
    calls = []

    class FakeCalendar:
        name = "Google"
        supported_features = 7

        async def async_get_events(self, hass, start, end):
            return [CalendarEvent(start=day.replace(hour=9), end=day.replace(hour=10), summary="Dentist", uid="abc/1")]

        async def async_update_event(self, uid, event, recurrence_id=None):
            calls.append(("update", uid, event["summary"], event["dtstart"].hour))

        async def async_delete_event(self, uid, recurrence_id=None):
            calls.append(("delete", uid))

    from unittest.mock import patch

    hass.states.async_set("calendar.google", "off", {"friendly_name": "Google", "supported_features": 7})
    await setup_aac(hass, mock_entry)
    component = hass.data[DATA_COMPONENT]
    real = component.get_entity
    patcher = patch.object(component, "get_entity", side_effect=lambda eid: FakeCalendar() if eid == "calendar.google" else real(eid))
    patcher.start()
    caps = mock_entry.runtime_data.capabilities
    found = await caps.async_calendar("assistant", day, day + timedelta(days=1))
    dentist = next(e for e in found if e["name"] == "Dentist")
    assert dentist["uid"] == "calendar:calendar.google/abc/1"
    await caps.async_change("assistant", dentist["uid"], {"name": "Dentist", "start": day.replace(hour=11).isoformat()})
    await caps.async_remove("assistant", dentist["uid"])
    assert calls == [("update", "abc/1", "Dentist", 11), ("delete", "abc/1")]

    # an own plan moves; the owner's trigger is refused
    store = triggers_of(hass)
    own = await store.async_save_trigger({"name": "Stretch", "enabled": True, "cooldown": 0, "quiet_hours": None, "created_by": "agent",
                                          "when": {"type": "time", "at": "10:00", "date": day.date().isoformat(), "weekdays": []},
                                          "do": {"instruction": "Remind me."}})
    owner = await store.async_save_trigger({"name": "Pills", "enabled": True, "cooldown": 0, "quiet_hours": None,
                                            "when": {"type": "time", "at": "08:00", "weekdays": []}, "do": {"instruction": "Pills."}})
    await caps.async_change("assistant", f"triggers:{own['id']}", {"start": day.replace(hour=15).isoformat()})
    assert store.triggers[0]["when"]["at"] == "15:00"
    import pytest
    with pytest.raises(LookupError):
        await caps.async_remove("assistant", f"triggers:{owner['id']}")
    with pytest.raises(LookupError):
        await caps.async_remove("assistant", "nonsense:1")
    patcher.stop()


async def test_aac_is_a_calendar_in_home_assistant(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """HA's calendar page can read, create, change and delete AAC's entries; they live in AAC (a list)."""
    from homeassistant.components.calendar.const import DATA_COMPONENT

    await setup_aac(hass, mock_entry)
    entity = hass.data[DATA_COMPONENT].get_entity("calendar.aac_calendar")
    assert entity is not None
    day = dt_util.start_of_local_day() + timedelta(days=2)
    await entity.async_create_event(summary="Haircut", dtstart=day.replace(hour=16), dtend=day.replace(hour=17))
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/lists"})
    cal = next(lst for lst in (await ws.receive_json())["result"]["lists"] if lst["name"] == "Calendar")
    assert cal["items"][0]["text"] == "Haircut"

    events = await entity.async_get_events(hass, day, day + timedelta(days=1))
    assert [(e.summary, e.start.hour) for e in events] == [("Haircut", 16)]
    uid = events[0].uid
    assert uid.startswith("lists:")
    await entity.async_update_event(uid, {"summary": "Haircut", "dtstart": day.replace(hour=18), "dtend": day.replace(hour=19)})
    assert (await entity.async_get_events(hass, day, day + timedelta(days=1)))[0].start.hour == 18
    await entity.async_delete_event(uid)
    assert await entity.async_get_events(hass, day, day + timedelta(days=1)) == []
