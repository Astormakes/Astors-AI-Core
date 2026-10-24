"""Schedule, HA event, Heartbeat, HA Assist, Decide, Memory, File, Timer, Builder."""

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import conversation
from homeassistant.core import Context as HaContext, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.aac.flow import Step
from custom_components.aac.context import Context

from .conftest import model_answers, reply, setup_aac


async def test_a_schedule_fires_at_its_time(hass: HomeAssistant, mock_entry, aioclient_mock, freezer: FrozenDateTimeFactory):
    """A Schedule entry at 05:25 wakes the chain; the answer lands in its log."""
    asked = model_answers(aioclient_mock, reply("The bed light is on."))
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    schedule = await d.add_card("schedule", "Schedule", {"entries": [{"name": "Bed light", "at": "05:25", "weekdays": [],
                                                                       "text": "Turn on the bed light.", "enabled": True}]}, card_id="schedule")
    d.set_wires([*d.wires, {"from": "schedule.out", "to": "charter.in"}, {"from": "model.out", "to": "schedule.answers"}])
    freezer.move_to(dt_util.as_utc(datetime(2026, 10, 26, 5, 25, 0, tzinfo=dt_util.get_default_time_zone())))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "Turn on the bed light." in asked[-1]["messages"][-1]["content"]
    assert schedule.data["log"][-1]["answer"] == "The bed light is on."


async def test_ha_event_and_heartbeat_fire(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A watched entity change starts the chain (once, cooldown)."""
    core = await setup_aac(hass, mock_entry)
    event = await core.domain.add_card("ha_event", "Door", {"watches": [{"name": "door", "entity_id": "binary_sensor.door", "to": "on", "text": "Tell me."}]})
    fired = []
    event.fire = lambda name, text: fired.append(text)
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("binary_sensor.door", "on")
    hass.states.async_set("binary_sensor.door", "off")
    hass.states.async_set("binary_sensor.door", "on")
    await hass.async_block_till_done()
    assert len(fired) == 1 and fired[0].endswith("Tell me.")
    beat = await core.domain.add_card("heartbeat", "Beat", {"quiet_from": "00:00", "quiet_to": "00:00"})
    assert not beat._quiet(dt_util.now())


async def test_ha_assist_is_a_conversation_agent(hass: HomeAssistant, mock_entry, aioclient_mock):
    """HA's conversation API reaches the HA Assist card; a follow-up continues the same chat."""
    core = await setup_aac(hass, mock_entry)
    await core.domain.add_card("assist", "HA Assist", card_id="assist")
    core.domain.set_wires([*core.domain.wires, {"from": "assist.said", "to": "charter.in"}, {"from": "model.out", "to": "assist.answers"}])
    model_answers(aioclient_mock, reply("It is noon."))
    result = await conversation.async_converse(hass, "what time is it?", None, HaContext(), agent_id=mock_entry.entry_id)
    assert result.response.speech["plain"]["speech"] == "It is noon."
    assert core.domain.cards["assist"].get(result.conversation_id)["messages"][0]["text"] == "what time is it?"


async def test_decide_picks_an_output(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The model's word picks the output."""
    core = await setup_aac(hass, mock_entry)
    decide = await core.domain.add_card("decide", "For me?", {"outputs": [{"name": "yes", "description": "for AAC"}, {"name": "no", "description": "else"}]})
    yes = await core.domain.add_card("relay", "Go")
    core.domain.set_wires([*core.domain.wires, {"from": "for_me.yes", "to": "go.in"}, {"from": "models.models", "to": "for_me.pool"}])
    model_answers(aioclient_mock, reply("yes"))
    context = Context()
    context.add("chat", "said", "computer, lights on")
    assert await decide.handle(context, "in", Step(core.domain.flow, decide, "in", context)) == [("yes", yes.id)]


async def test_memory_file_timer_builder(hass: HomeAssistant, mock_entry):
    """Memory by words, a file, a timer, the builder within its permissions."""
    core = await setup_aac(hass, mock_entry)
    memory = await core.domain.add_card("memory", "Memory")
    await memory.call("remember", {"text": "The drill is on basement shelf 2."})
    context = Context()
    context.add("chat", "said", "where is the drill?")
    await memory.handle(context, "in", None)
    assert "basement shelf 2" in context.last("note")["text"]
    file = await core.domain.add_card("file", "Notes", {"name": "test"})
    await file.call("write", {"text": "# Hi"})
    await file.call("append", {"text": "more"})
    assert await file.call("read", {}) == "# Hi\nmore\n"
    timer = await core.domain.add_card("timer", "Wait", {"seconds": 0})
    assert await timer.handle(Context(), "in", None) is None
    builder = await core.domain.add_card("builder", "Builder")
    added = await builder.call("add_card", {"type": "buffer", "name": "Room"})
    assert added["id"] == "room" and (await builder.call("remove_card", {"card": "room"}))["error"]
    assert (await builder.call("wire", {"from": "room.out", "to": "notes.in"}))["wired"]


async def test_calendar_card_and_entity(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The default Calendar card is the model's tool; its entries and dated list entries show in calendar.aac_calendar."""
    from datetime import timedelta

    core = await setup_aac(hass, mock_entry)
    cal = core.domain.cards["calendar"]
    assert {"from": "model.out", "to": "calendar.in"} in core.domain.wires
    tomorrow = (dt_util.now() + timedelta(days=1)).date().isoformat()
    assert (await cal.call("add", {"summary": "Dentist", "start": f"{tomorrow}T10:00"}))["done"] == "add"
    lists = core.domain.cards["lists"]
    await lists.call("create_list", {"list": "Errands"})
    await lists.call("add_items", {"list": "Errands", "items": ["Pay rent"]})
    item = lists.data["lists"][-1]["items"][0]
    item["fields"]["due"] = tomorrow
    found = await cal.call("calendar", {})
    assert [e["summary"] for e in found] == ["Pay rent (Errands)", "Dentist"]
    uid = next(e["uid"] for e in found if e["summary"] == "Dentist")
    await cal.call("change", {"uid": uid, "summary": "Dentist (moved)"})
    await hass.async_block_till_done()
    state = hass.states.get("calendar.aac_calendar")
    assert state is not None
    start, end = dt_util.now(), dt_util.now() + timedelta(days=3)
    events = await hass.services.async_call("calendar", "get_events", {"entity_id": "calendar.aac_calendar", "start_date_time": start,
                                                                        "end_date_time": end}, blocking=True, return_response=True)
    assert {e["summary"] for e in events["calendar.aac_calendar"]["events"]} == {"Pay rent (Errands)", "Dentist (moved)"}
    await cal.call("remove", {"uid": uid})
    assert len(await cal.call("calendar", {})) == 1


async def test_chat_commands_take_every_chat_card(hass: HomeAssistant, mock_entry, aioclient_mock, hass_ws_client):
    """The panel lists and deletes the chats of HA Assist and Voice chat cards too."""
    core = await setup_aac(hass, mock_entry)
    assist = core.domain.cards["assist"]
    chat = assist._new("Test")
    await assist.save()
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "aac/chat/list", "card": "assist"})
    assert [c["id"] for c in (await client.receive_json())["result"]["chats"]] == [chat["id"]]
    await client.send_json_auto_id({"type": "aac/chat/delete", "card": "assist", "chat_id": chat["id"]})
    assert (await client.receive_json())["success"]
    assert assist.chats() == []
