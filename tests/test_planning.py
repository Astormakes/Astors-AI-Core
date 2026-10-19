"""AAC planning for itself: own schedules, autonomy, open loops."""

from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BASE_URL, setup_aac

URL = f"{BASE_URL}/chat/completions"


def _tool_reply(name: str, args: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": name, "arguments": args}}]}}]}


def _text(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _queue(aioclient_mock, *bodies):
    queue, payloads = list(bodies), []

    async def side_effect(method, url, data):
        payloads.append(data)
        return AiohttpClientMockResponse(method, url, json=queue.pop(0))

    aioclient_mock.post(URL, side_effect=side_effect)
    return payloads


async def _chat(ws, text):
    await ws.send_json_auto_id({"type": "aac/chat", "text": text})
    return (await ws.receive_json())["result"]["message"]


async def test_master_schedules_itself(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Allowed (default): an active agent trigger, and the owner is told in From AAC."""
    await setup_aac(hass, mock_entry)
    _queue(
        aioclient_mock,
        _tool_reply("aac-planning__schedule",
                    '{"name": "Walk check", "instruction": "Ask how the walk went.", "at": "18:30", "weekdays": [0, 2, 4]}'),
        _text("I'll check in on Monday, Wednesday and Friday evening."),
    )
    ws = await hass_ws_client(hass)
    await _chat(ws, "Check in on my evening walks, please.")
    trig = mock_entry.runtime_data.store.triggers[0]
    assert trig["created_by"] == "agent" and trig["enabled"] and not trig["proposed"]
    assert trig["when"] == {"type": "time", "weekdays": [0, 2, 4], "at": "18:30"}
    told = mock_entry.runtime_data.chats.get("aac")["messages"][-1]["text"]
    assert told.startswith('I planned "Walk check"')


async def test_ask_mode_needs_approval(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Ask: the plan waits, the owner approves it."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/autonomy", "schedule": "ask"})
    assert (await ws.receive_json())["result"]["autonomy"]["schedule"] == "ask"
    _queue(aioclient_mock, _tool_reply("aac-planning__schedule", '{"name": "Pills", "instruction": "Remind.", "in_minutes": 30}'), _text("ok"))
    await _chat(ws, "Remind me in half an hour.")
    trig = mock_entry.runtime_data.store.triggers[0]
    assert trig["proposed"] and not trig["enabled"]
    assert trig["when"]["date"]  # in_minutes = a one-off
    await ws.send_json_auto_id({"type": "aac/trigger/approve", "trigger_id": trig["id"]})
    await ws.receive_json()
    assert mock_entry.runtime_data.store.triggers[0]["enabled"] is True


async def test_off_mode_hides_tools(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Off: the planning tools are not offered at all."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/autonomy", "schedule": "off"})
    await ws.receive_json()
    payloads = _queue(aioclient_mock, _text("ok"))
    await _chat(ws, "hi")
    assert not any("schedule" in t["function"]["name"] for t in payloads[0]["tools"])


async def test_cannot_touch_owner_triggers(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Agents organize tasks, not rules: the owner's triggers are off-limits."""
    await setup_aac(hass, mock_entry)
    owner = await mock_entry.runtime_data.store.async_save_trigger(
        {"name": "Door", "enabled": True, "cooldown": 0, "quiet_hours": None,
         "when": {"type": "state", "entity_id": "binary_sensor.door", "to": "on"},
         "do": {"template": "event-reaction", "instruction": "Tell me."}}
    )
    _queue(aioclient_mock, _tool_reply("aac-planning__cancel_schedule", f'{{"id": "{owner["id"]}"}}'), _text("I can't."))
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "Delete the door trigger.")
    result = next(s for s in message["steps"] if s["type"] == "tool_result")
    assert result["error"] and "owner" in result["data"]
    assert mock_entry.runtime_data.store.triggers[0]["id"] == owner["id"]


async def test_one_off_fires_once_and_goes(hass: HomeAssistant, aioclient_mock, mock_entry, configured, freezer):
    """A dated plan fires on its day and removes itself."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-10-19 09:59:00+00:00")
    await setup_aac(hass, mock_entry)
    payloads = _queue(aioclient_mock, _text("Time to stretch."), _text('{"speak": true, "message": "Time to stretch."}'))
    await mock_entry.runtime_data.store.async_save_trigger(
        {"name": "Stretch", "enabled": True, "cooldown": 0, "quiet_hours": None, "created_by": "agent",
         "when": {"type": "time", "at": "10:00", "date": "2026-10-19", "weekdays": []},
         "do": {"template": "event-reaction", "instruction": "Remind the user to stretch."}}
    )
    mock_entry.runtime_data.triggers.async_start()
    freezer.move_to("2026-10-19 10:00:00+00:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(payloads) == 2
    assert "your own earlier instruction" in payloads[0]["messages"][0]["content"]
    assert mock_entry.runtime_data.store.triggers == []


async def test_plans_are_open_loops(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Memories of kind 'plan' show up in the heartbeat's context, without any goal-specific code."""
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.memory.async_remember("Wants to lose 4 kg by spring, feel fitter.", "plan")
    await mock_entry.runtime_data.memory.async_remember("Weight 82.4 kg", "event")
    payloads = _queue(aioclient_mock, _text('{"speak": false, "message": ""}'))
    await mock_entry.runtime_data.triggers.async_heartbeat(force=True)
    system = payloads[0]["messages"][0]["content"]
    assert "Open loops" in system and "lose 4 kg" in system
    assert "Weight 82.4" not in system  # events are recalled when needed, not listed



def test_weekday_names():
    """Names, groups and ranges, because small models get the numbers wrong."""
    from custom_components.aac.schedule_api import parse_weekdays

    assert parse_weekdays(["weekdays"]) == [0, 1, 2, 3, 4]
    assert parse_weekdays(["mon", "Friday"]) == [0, 4]
    assert parse_weekdays("Mo-Fr") == [0, 1, 2, 3, 4]
    assert parse_weekdays(["weekend"]) == [5, 6]
    assert parse_weekdays([0, 2, "4"]) == [0, 2, 4]
    assert parse_weekdays(["sa-mo"]) == [0, 5, 6]
    assert parse_weekdays([]) == []


async def test_attribute_and_event_triggers(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """AAC watches an attribute (Steam game) and an HA event; noisy events and unreadable entities are refused."""
    import json

    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    hass.states.async_set("sensor.astor", "online", {"friendly_name": "Astor", "game": None})
    hass.states.async_set("sensor.hidden", "1")
    await setup_aac(hass, mock_entry)
    async_expose_entity(hass, "conversation", "sensor.astor", True)
    _queue(
        aioclient_mock,
        _tool_reply("aac-planning__watch", json.dumps({"name": "Gaming", "instruction": "Free the PC.", "entity_id": "sensor.astor", "attribute": "game"})),
        _tool_reply("aac-planning__watch", json.dumps({"name": "Bad", "instruction": "x", "event_type": "state_changed"})),
        _tool_reply("aac-planning__watch", json.dumps({"name": "Hidden", "instruction": "x", "entity_id": "sensor.hidden"})),
        _tool_reply("aac-planning__watch", json.dumps({"name": "Button", "instruction": "Toggle the lamp.", "event_type": "zha_event", "event_data": {"command": "on"}})),
        _text("Done."),
    )
    ws = await hass_ws_client(hass)
    message = await _chat(ws, "Free my PC when I start a game, and react to my button.")
    results = [s for s in message["steps"] if s["type"] == "tool_result"]
    assert [bool(r.get("error")) for r in results] == [False, True, True, False]
    store = mock_entry.runtime_data.store
    assert [t["when"]["type"] for t in store.triggers] == ["state", "event"]

    aioclient_mock.clear_requests()  # the first mock would still answer
    payloads = _queue(aioclient_mock, *[_text("NOTHING"), _text('{"speak": false, "message": ""}')] * 3)
    hass.states.async_set("sensor.astor", "online", {"friendly_name": "Astor", "game": None, "level": 5})  # other attribute
    await hass.async_block_till_done(wait_background_tasks=True)
    assert payloads == []
    hass.states.async_set("sensor.astor", "online", {"friendly_name": "Astor", "game": "Factorio"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "attribute game changed from nothing to Factorio" in payloads[0]["messages"][-1]["content"]
    hass.bus.async_fire("zha_event", {"command": "off"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(payloads) == 2  # filtered out (off ≠ on); only the decision call of the game run
    hass.bus.async_fire("zha_event", {"command": "on", "device": "button"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "zha_event" in payloads[2]["messages"][-1]["content"]
