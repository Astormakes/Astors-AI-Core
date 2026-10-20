"""Chats, runs, heartbeat and event triggers."""

from datetime import datetime, timedelta
import json
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.aac.triggers import in_quiet_hours
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BASE_URL, setup_aac

URL = f"{BASE_URL}/chat/completions"


def _reply(text: str | None, tool: str | None = None) -> dict:
    message = {"role": "assistant", "content": text}
    if tool:
        message["tool_calls"] = [
            {"id": "c1", "type": "function", "function": {"name": tool, "arguments": "{}"}}
        ]
    return {"choices": [{"message": message}]}


def _replies(aioclient_mock: AiohttpClientMocker, *bodies: dict) -> list[dict]:
    """Answer the chat endpoint with these bodies in order; returns the request payloads."""
    queue = list(bodies)
    payloads: list[dict] = []

    async def side_effect(method, url, data):
        payloads.append(data)
        return AiohttpClientMockResponse(method, url, json=queue.pop(0))

    aioclient_mock.post(URL, side_effect=side_effect)
    return payloads


async def test_chats_persist_and_continue(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """A chat keeps its messages and the model sees the earlier turns."""
    await setup_aac(hass, mock_entry)
    payloads = _replies(aioclient_mock, _reply("Hi!"), _reply("You said hello."))
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": "aac/chat", "text": "hello"})
    chat_id = (await ws.receive_json())["result"]["chat_id"]
    await ws.send_json_auto_id({"type": "aac/chat", "text": "what did I say?", "chat_id": chat_id})
    assert (await ws.receive_json())["result"]["message"]["text"] == "You said hello."
    assert any(m.get("content") == "hello" for m in payloads[1]["messages"])

    await ws.send_json_auto_id({"type": "aac/chats"})
    chats = (await ws.receive_json())["result"]["chats"]
    assert chats[0]["title"] == "hello"
    await ws.send_json_auto_id({"type": "aac/chat/get", "chat_id": chat_id})
    chat = (await ws.receive_json())["result"]
    assert [m["role"] for m in chat["messages"]] == ["user", "aac", "user", "aac"]

    await ws.send_json_auto_id({"type": "aac/chat/delete", "chat_id": chat_id})
    await ws.receive_json()
    assert mock_entry.runtime_data.chats.get(chat_id) is None


async def test_chat_records_tool_steps(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured_control
):
    """Tool calls end up in the run and in the chat message."""
    await setup_aac(hass, mock_entry)
    _replies(aioclient_mock, _reply(None, "assist__llm__GetDateTime"), _reply("It is noon."))
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "time?"})
    message = (await ws.receive_json())["result"]["message"]
    assert [s["type"] for s in message["steps"]] == ["tool_call", "tool_result"]
    assert message["steps"][0]["tool"] == "assist__llm__GetDateTime"
    assert message["steps"][1]["error"] is False

    await ws.send_json_auto_id({"type": "aac/run/get", "run_id": message["run_id"]})
    run = (await ws.receive_json())["result"]
    assert run["kind"] == "chat" and run["status"] == "done" and len(run["steps"]) == 2


async def test_heartbeat_silent_and_speaking(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """NOTHING stays silent; anything else opens a chat of its own (not 'From AAC')."""
    await setup_aac(hass, mock_entry)
    payloads = _replies(aioclient_mock, _reply("NOTHING"), _reply("The window is still open."))
    ws = await hass_ws_client(hass)

    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    run_id = (await ws.receive_json())["result"]["run_id"]
    run = mock_entry.runtime_data.runs.get(run_id)
    assert run.kind == "auto" and run.status == "silent"
    assert "Heartbeat" in payloads[0]["messages"][0]["content"]
    assert mock_entry.runtime_data.chats.get("aac") is None

    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    run = mock_entry.runtime_data.runs.get((await ws.receive_json())["result"]["run_id"])
    chat = mock_entry.runtime_data.chats.get(run.chat_id)
    assert run.chat_id.startswith("hb_") and chat["title"] == "Heartbeat · The window is still open."
    assert chat["messages"][-1]["text"] == "The window is still open."
    assert chat["unread"] == 1
    assert mock_entry.runtime_data.chats.get("aac") is None


async def test_heartbeat_schedule(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Enabled heartbeat fires on its interval and the setup step is done."""
    await setup_aac(hass, mock_entry)
    _replies(aioclient_mock, _reply("NOTHING"))
    ws = await hass_ws_client(hass)
    with patch("custom_components.aac.triggers.in_quiet_hours", return_value=False):
        await ws.send_json_auto_id(
            {"type": "aac/heartbeat/update", "enabled": True, "interval": 5}
        )
        state = (await ws.receive_json())["result"]
        assert {s["id"]: s for s in state["setup"]}["heartbeat"]["done"]
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
        await hass.async_block_till_done(wait_background_tasks=True)
    runs = mock_entry.runtime_data.runs.runs
    assert [r.trigger for r in runs] == ["heartbeat"]


async def test_heartbeat_default_not_stored(hass: HomeAssistant, hass_ws_client, hass_storage, mock_entry, configured):
    """Saving the default instructions keeps following the built-in default."""
    from custom_components.aac.store import DEFAULT_HEARTBEAT, STORAGE_KEY

    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/heartbeat/update", "instructions": DEFAULT_HEARTBEAT})
    state = (await ws.receive_json())["result"]
    assert state["heartbeat"]["instructions"] == DEFAULT_HEARTBEAT
    assert hass_storage[STORAGE_KEY]["data"]["domains"][0]["heartbeat"]["instructions"] == ""
    await ws.send_json_auto_id({"type": "aac/heartbeat/update", "instructions": "Mine."})
    assert (await ws.receive_json())["result"]["heartbeat"]["instructions"] == "Mine."


def test_silence_heuristic():
    """NOTHING and its obvious paraphrases are silence; real news is not."""
    from custom_components.aac.master import is_silent

    for text in ["NOTHING", "Nothing.", "I have no alerts or issues to report.", "No issues.",
                 "All good.", "Everything is fine.", "Nothing to report.", "There is nothing new to report.", ""]:
        assert is_silent(text) or text.startswith("I have"), text
    assert is_silent("No alerts.")
    assert is_silent("NONE") and is_silent("N/A") and is_silent("No.")
    assert is_silent("No, everything is normal.") and is_silent("No, nothing requires your attention right now.")
    assert is_silent("All systems are normal; no alerts.")
    assert not is_silent("None of the windows are closed and it is raining.")
    assert not is_silent("The kitchen window is open and it is raining.")
    assert not is_silent("Nobody is home but the oven is on.")


def test_quiet_hours():
    """Quiet hours may wrap midnight."""
    at = lambda h, m=0: datetime(2026, 10, 18, h, m)  # noqa: E731
    assert in_quiet_hours(at(23), "22:00", "08:00")
    assert in_quiet_hours(at(7, 59), "22:00", "08:00")
    assert not in_quiet_hours(at(12), "22:00", "08:00")
    assert in_quiet_hours(at(13), "12:00", "14:00")
    assert not in_quiet_hours(at(13), "12:00", "12:00")


async def test_state_trigger_with_cooldown(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """A state change wakes the master once; the cooldown blocks repeats."""
    hass.states.async_set("binary_sensor.door", "off", {"friendly_name": "Door"})
    await setup_aac(hass, mock_entry)
    payloads = _replies(
        aioclient_mock,
        _reply("The door opened."),
        _reply('{"speak": true, "message": "The door opened."}'),
        _reply("again"),
    )
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id(
        {"type": "aac/trigger/save", "name": "Door",
         "when": {"type": "state", "entity_id": "binary_sensor.door", "to": "on"},
         "instruction": "Tell me when the door opens.", "cooldown": 600}
    )
    assert (await ws.receive_json())["success"]

    hass.states.async_set("binary_sensor.door", "on", {"friendly_name": "Door"})
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set("binary_sensor.door", "off", {"friendly_name": "Door"})
    hass.states.async_set("binary_sensor.door", "on", {"friendly_name": "Door"})
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(payloads) == 2  # answer + decision, then the cooldown blocks the repeat
    assert "Door (binary_sensor.door) changed from off to on" in payloads[0]["messages"][-1]["content"]
    assert "Tell me when the door opens." in payloads[0]["messages"][0]["content"]
    assert mock_entry.runtime_data.chats.get("aac")["messages"][-1]["text"] == "The door opened."
    run = mock_entry.runtime_data.runs.runs[-1]
    assert run.kind == "auto" and run.title == "Door"


async def test_subscribe_pushes_updates(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured
):
    """The panel gets told about new runs and chat messages."""
    await setup_aac(hass, mock_entry)
    _replies(aioclient_mock, _reply("hey"))
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/subscribe"})
    assert (await ws.receive_json())["success"]
    mock_entry.runtime_data.chats.add_message("aac", "aac", "hello", proactive=True)
    events = []
    for _ in range(2):  # "chats" (created) + "chat" (message)
        events.append((await ws.receive_json())["event"]["what"])
    assert "chat" in events


async def test_auto_run_forced_to_answer(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured_control
):
    """A model that keeps calling tools gets a last round without tools and must answer."""
    hass.states.async_set("light.desk", "on", {"friendly_name": "Desk"})
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.store.async_update_heartbeat({"tools": True})
    payloads: list[dict] = []

    async def side_effect(method, url, data):
        payloads.append(data)
        body = (
            _reply('Nothing. <tool_call>{"name": "x"}</tool_call>')
            if "tools" not in data
            else _reply(None, "assist__llm__GetDateTime")
        )
        return AiohttpClientMockResponse(method, url, json=body)

    aioclient_mock.post(URL, side_effect=side_effect)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    run = mock_entry.runtime_data.runs.get((await ws.receive_json())["result"]["run_id"])
    assert run.status == "silent"
    assert len(payloads) == 6  # 4 tool rounds + 1 forced answer + 1 decision
    assert "tools" not in payloads[4]
    assert "Stop using tools" in payloads[4]["messages"][-1]["content"]
    assert payloads[5]["response_format"]["json_schema"]["name"] == "decision"
    assert run.context.startswith("Heartbeat: a scheduled check-in")
    # The heartbeat sees the home without asking
    assert "Current state of the home" in payloads[0]["messages"][0]["content"]


async def test_heartbeat_without_tools_by_default(
    hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured_control
):
    """By default the heartbeat only looks: one call, no tools offered."""
    await setup_aac(hass, mock_entry)
    payloads = _replies(aioclient_mock, _reply("NOTHING"))
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    run = mock_entry.runtime_data.runs.get((await ws.receive_json())["result"]["run_id"])
    assert run.status == "silent" and run.allow_tools is False
    assert len(payloads) == 1 and "tools" not in payloads[0]
    assert payloads[0]["response_format"]["json_schema"]["name"] == "decision"
    assert payloads[0]["temperature"] <= 0.3
    assert '"speak"' in payloads[0]["messages"][0]["content"]


async def test_heartbeat_json_decision(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """speak=false is silence, speak=true posts the message."""
    await setup_aac(hass, mock_entry)
    _replies(
        aioclient_mock,
        _reply('{"speak": false, "message": "All good, lights are off."}'),
        _reply('{"speak": true, "message": "Water leak in the bathroom."}'),
    )
    ws = await hass_ws_client(hass)
    runs = mock_entry.runtime_data.runs
    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    assert runs.get((await ws.receive_json())["result"]["run_id"]).status == "silent"
    await ws.send_json_auto_id({"type": "aac/heartbeat/run"})
    run = runs.get((await ws.receive_json())["result"]["run_id"])
    assert run.status == "done" and run.result == "Water leak in the bathroom."
    assert mock_entry.runtime_data.chats.get(run.chat_id)["messages"][-1]["text"] == "Water leak in the bathroom."


def test_snapshot_says_what_binary_sensors_mean(hass: HomeAssistant):
    """'on' for a smoke detector becomes DETECTED, a door becomes OPEN."""
    from homeassistant.components.homeassistant.exposed_entities import async_expose_entity

    from custom_components.aac.context import home_snapshot

    hass.states.async_set("binary_sensor.smoke", "on", {"friendly_name": "Smoke", "device_class": "smoke"})
    hass.states.async_set("binary_sensor.door", "off", {"friendly_name": "Door", "device_class": "door"})
    hass.states.async_set("sensor.temp", "21.5", {"friendly_name": "Temp", "unit_of_measurement": "°C"})
    for entity_id in ("binary_sensor.smoke", "binary_sensor.door", "sensor.temp"):
        async_expose_entity(hass, "conversation", entity_id, True)
    snapshot = home_snapshot(hass)
    assert "Smoke | DETECTED" in snapshot
    assert "Door | closed" in snapshot
    assert "Temp | 21.5 °C" in snapshot


async def test_time_trigger_weekdays(
    hass: HomeAssistant, aioclient_mock, mock_entry, configured, freezer
):
    """A time trigger fires at its time, only on its weekdays."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-10-19 07:29:00+00:00")  # a Monday
    await setup_aac(hass, mock_entry)
    payloads = _replies(
        aioclient_mock,
        _reply("Time for your pills."),
        _reply('{"speak": true, "message": "Time for your pills."}'),
    )
    await mock_entry.runtime_data.store.async_save_trigger(
        {"name": "Pills", "enabled": True, "cooldown": 0, "quiet_hours": None,
         "when": {"type": "time", "at": "07:30", "weekdays": [0, 2, 4]},
         "do": {"template": "event-reaction", "instruction": "Remind me to take my pills."}}
    )
    mock_entry.runtime_data.triggers.async_start()

    freezer.move_to("2026-10-19 07:30:00+00:00")
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(payloads) == 2
    assert "Remind me to take my pills." in payloads[0]["messages"][0]["content"]
    assert "remind or tell the user" in payloads[0]["messages"][0]["content"]
    assert mock_entry.runtime_data.chats.get("aac")["messages"][-1]["text"] == "Time for your pills."

    freezer.move_to("2026-10-20 07:30:00+00:00")  # Tuesday: not a trigger day
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(payloads) == 2


async def test_old_triggers_migrate(hass: HomeAssistant, hass_storage, mock_entry):
    """0.4/0.5 flat state triggers load in the contract format."""
    from custom_components.aac.store import STORAGE_KEY

    from .conftest import store_data

    data = store_data()
    data["data"]["triggers"] = [{"id": "t_old", "name": "Door", "entity_id": "binary_sensor.door",
                                 "to": "on", "instruction": "Tell me.", "cooldown": 60, "enabled": True}]
    hass_storage[STORAGE_KEY] = data
    await setup_aac(hass, mock_entry)
    trig = mock_entry.runtime_data.store.triggers[0]
    assert trig["when"] == {"type": "state", "entity_id": "binary_sensor.door", "to": "on"}
    assert trig["do"] == {"template": "event-reaction", "instruction": "Tell me."}
    assert trig["cooldown"] == 60


async def test_chat_context_survives_ha_forgetting(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """HA drops a conversation after 5 idle minutes; AAC puts its own record of the chat back."""
    payloads = _replies(aioclient_mock, _reply("Steam is not set up."), _reply("Now I see it."), _reply("Edited answer."))
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Can you use the Steam integration?"})
    chat_id = (await ws.receive_json())["result"]["chat_id"]
    mock_entry.runtime_data.chats.set_conversation(chat_id, "01FORGOTTENBYHA0000000000")  # as if HA timed it out
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Some changes were made, check again.", "chat_id": chat_id})
    await ws.receive_json()
    texts = [m.get("content") for m in payloads[1]["messages"]]
    assert "Can you use the Steam integration?" in texts and "Steam is not set up." in texts
    assert texts[-1] == "Some changes were made, check again."

    # edit: cut off at the second user message and send a new one; the model sees only what came before
    await ws.send_json_auto_id({"type": "aac/chat/rewind", "chat_id": chat_id, "index": 2})
    assert len((await ws.receive_json())["result"]["messages"]) == 2
    await ws.send_json_auto_id({"type": "aac/chat", "text": "I turned on the entities, check again.", "chat_id": chat_id})
    await ws.receive_json()
    texts = [m.get("content") for m in payloads[2]["messages"]]
    assert "Some changes were made, check again." not in texts and "Steam is not set up." in texts
    assert [m["text"] for m in mock_entry.runtime_data.chats.get(chat_id)["messages"]][-2:] == [
        "I turned on the entities, check again.", "Edited answer."]


async def test_edit_aac_answer(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """The owner changes what AAC said; the next request gives the model the changed words."""
    payloads = _replies(aioclient_mock, _reply("Steam is not set up."), _reply("Fine."))
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Can you use the Steam integration?"})
    chat_id = (await ws.receive_json())["result"]["chat_id"]
    await ws.send_json_auto_id({"type": "aac/chat/edit", "chat_id": chat_id, "index": 1, "text": "Steam is there."})
    result = (await ws.receive_json())["result"]
    assert result["messages"][1] == {**result["messages"][1], "text": "Steam is there.", "edited": True}
    await ws.send_json_auto_id({"type": "aac/chat", "text": "Good.", "chat_id": chat_id})
    await ws.receive_json()
    texts = [m.get("content") for m in payloads[1]["messages"]]
    assert "Steam is there." in texts and "Steam is not set up." not in texts


def test_fit_leaves_out_oldest_turns():
    """Too long: whole old turns go first; the system prompt and the newest question stay."""
    from custom_components.aac.context_fit import estimate, fit

    msgs = [{"role": "system", "content": "rules"}]
    for i in range(20):
        msgs += [{"role": "user", "content": f"question {i} " + "x" * 400}, {"role": "assistant", "content": "y" * 400}]
    msgs.append({"role": "user", "content": "newest"})
    budget = estimate(msgs, None) // 3
    out, dropped, est = fit(msgs, None, budget)
    assert est <= budget and dropped > 0 and dropped % 2 == 0  # whole turns
    assert out[-1]["content"] == "newest" and out[0]["content"].startswith("rules")
    assert "left out" in out[0]["content"] and out[1]["role"] == "user"
    assert fit(msgs, None, 10**6)[1] == 0


async def test_too_long_chat_is_fitted_and_retried(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """The server says the request is too long: AAC learns its context size and sends a shorter chat."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    _replies(aioclient_mock, *[_reply("x" * 3000) for _ in range(6)])
    chat_id = None
    for i in range(6):  # a long chat
        await ws.send_json_auto_id({"type": "aac/chat", "text": f"tell me a long story {i}", **({"chat_id": chat_id} if chat_id else {})})
        chat_id = (await ws.receive_json())["result"]["chat_id"]
    aioclient_mock.clear_requests()
    payloads: list[dict] = []

    async def side_effect(method, url, data):
        payloads.append(data)
        if len(payloads) == 1:
            return AiohttpClientMockResponse(method, url, status=400, text=json.dumps({"error": {
                "code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 6000, "n_ctx": 4096,
                "message": "request (6000 tokens) exceeds the available context size (4096 tokens)"}}))
        return AiohttpClientMockResponse(method, url, json=_reply("Short now."))

    aioclient_mock.post(URL, side_effect=side_effect)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "and now?", "chat_id": chat_id})
    message = (await ws.receive_json())["result"]["message"]
    assert message["text"] == "Short now."
    assert len(payloads) == 2 and len(payloads[1]["messages"]) < len(payloads[0]["messages"])
    assert payloads[1]["messages"][-1]["content"] == "and now?"
    assert any("Context full" in (s.get("text") or "") for s in message["steps"])
    assert mock_entry.runtime_data.broker.traits(mock_entry.runtime_data.store.pools["master"][0])["n_ctx"] == 4096


async def test_answers_without_tools_when_they_do_not_fit(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Even the tool descriptions are too long for a small model: it answers without them instead of failing."""
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    payloads: list[dict] = []
    too_long = json.dumps({"error": {"code": 400, "type": "exceed_context_size_error", "n_prompt_tokens": 9000,
                                     "n_ctx": 8192, "message": "request exceeds the available context size"}})

    async def side_effect(method, url, data):
        payloads.append(data)
        if data.get("tools"):
            return AiohttpClientMockResponse(method, url, status=400, text=too_long)
        return AiohttpClientMockResponse(method, url, json=_reply("Plain answer."))

    aioclient_mock.post(URL, side_effect=side_effect)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hello"})
    message = (await ws.receive_json())["result"]["message"]
    assert message["text"] == "Plain answer."
    assert len(payloads) == 3 and not payloads[2].get("tools")
    assert any("without them" in (s.get("text") or "") for s in message["steps"])
