"""Schedule, HA event, Heartbeat, HA Assist, Decide, Memory, File, Timer, Builder."""

from datetime import datetime

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import conversation
from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.aac.flow import Step
from custom_components.aac.message import Message

from .conftest import model_answers, reply, setup_aac, v1_files


async def test_v1_trigger_becomes_a_schedule_that_fires(hass: HomeAssistant, mock_entry, aioclient_mock, freezer: FrozenDateTimeFactory):
    """v1's 05:25 trigger is a Schedule entry; at the time it wakes the chain and the answer lands in its log."""
    import json
    from pathlib import Path

    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    Path(hass.config.config_dir, ".storage", "aac.mod.triggers.triggers").write_text(json.dumps({"data": {"triggers": [
        {"id": "t1", "name": "Bed light", "enabled": True, "when": {"type": "time", "at": "05:25", "weekdays": []},
         "do": {"instruction": "Turn on the bed light."}}]}}))
    asked = model_answers(aioclient_mock, reply("The bed light is on."))
    core = await setup_aac(hass, mock_entry)
    schedule = core.domain.cards["schedule"]
    assert schedule.settings["entries"][0]["at"] == "05:25" and {"from": "model.out", "to": "schedule.answers"} in core.domain.wires
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
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    core = await setup_aac(hass, mock_entry)
    await core.domain.add_card("assist", "HA Assist", card_id="assist")
    core.domain.set_wires([*core.domain.wires, {"from": "assist.said", "to": "charter.in"}, {"from": "model.out", "to": "assist.answers"}])
    model_answers(aioclient_mock, reply("It is noon."))
    result = await conversation.async_converse(hass, "what time is it?", None, Context(), agent_id=mock_entry.entry_id)
    assert result.response.speech["plain"]["speech"] == "It is noon."
    assert core.domain.cards["assist"].get(result.conversation_id)["messages"][0]["text"] == "what time is it?"


async def test_decide_picks_an_output(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The model's word picks the output."""
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    core = await setup_aac(hass, mock_entry)
    decide = await core.domain.add_card("decide", "For me?", {"outputs": [{"name": "yes", "description": "for AAC"}, {"name": "no", "description": "else"}]})
    yes = await core.domain.add_card("relay", "Go")
    core.domain.set_wires([*core.domain.wires, {"from": "for_me.yes", "to": "go.in"}, {"from": "models.models", "to": "for_me.pool"}])
    model_answers(aioclient_mock, reply("yes"))
    message = Message()
    message.add("chat", "said", "computer, lights on")
    assert await decide.handle(message, "in", Step(core.domain.flow, decide, "in", message)) == [("yes", yes.id)]


async def test_memory_file_timer_builder(hass: HomeAssistant, mock_entry):
    """Memory by words, a file, a timer, the builder within its permissions."""
    core = await setup_aac(hass, mock_entry)
    memory = await core.domain.add_card("memory", "Memory")
    await memory.call("remember", {"text": "The drill is on basement shelf 2."})
    message = Message()
    message.add("chat", "said", "where is the drill?")
    await memory.handle(message, "in", None)
    assert "basement shelf 2" in message.last("note")["text"]
    file = await core.domain.add_card("file", "Notes", {"name": "test"})
    await file.call("write", {"text": "# Hi"})
    await file.call("append", {"text": "more"})
    assert await file.call("read", {}) == "# Hi\nmore\n"
    timer = await core.domain.add_card("timer", "Wait", {"seconds": 0})
    assert await timer.handle(Message(), "in", None) is None
    builder = await core.domain.add_card("builder", "Builder")
    added = await builder.call("add_card", {"type": "buffer", "name": "Room"})
    assert added["id"] == "room" and (await builder.call("remove_card", {"card": "room"}))["error"]
    assert (await builder.call("wire", {"from": "room.out", "to": "notes.in"}))["wired"]
