"""The cards on their own."""

from homeassistant.core import HomeAssistant

from custom_components.aac.flow import Step
from custom_components.aac.message import Message

from .conftest import setup_aac


async def test_conditional_edits_tags_calls_and_routes(hass: HomeAssistant, mock_entry):
    """If the newest said entry contains 'computer': replace, tag, call a wired Buffer, go to output 'aac'."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    cond = await domain.add_card("conditional", "Check", {"outputs": ["aac"], "rules": [{
        "name": "for me", "select": {"by": "kind", "value": "said", "which": "newest"}, "test": "contains", "value": "computer",
        "actions": [{"do": "replace", "a": "computer,", "b": ""}, {"do": "trim"}, {"do": "tag", "a": "addressed"},
                    {"do": "call", "card": "room", "function": "add", "args": {"text": "{text}"}},
                    {"do": "output", "a": "aac"}]}]})
    buffer = await domain.add_card("buffer", "Room", {"position": "nothing"})
    yes = await domain.add_card("relay", "Yes")
    no = await domain.add_card("relay", "No")
    domain.set_wires([{"from": "check.aac", "to": "yes.in"}, {"from": "check.out", "to": "no.in"},
                      {"from": "check.out", "to": "room.in"}, {"from": "room.out", "to": "check.in"}])
    message = Message()
    message.add("chat", "said", "Computer, lights on")
    route = await cond.handle(message, "in", Step(domain.flow, cond, "in", message))
    said = message.last("said")
    assert route == [("aac", "yes")] and said["text"] == "lights on" and "user.addressed" in said["tags"]
    assert buffer.data["lines"] == ["lights on"]
    other = Message()
    other.add("chat", "said", "hello")
    assert await cond.handle(other, "in", Step(domain.flow, cond, "in", other)) == [("out", "no")]  # the buffer is a tool


async def test_buffer_keeps_and_writes(hass: HomeAssistant, mock_entry):
    """The last N lines, written as one note; its functions change it."""
    core = await setup_aac(hass, mock_entry)
    buffer = await core.domain.add_card("buffer", "Room", {"size": 2})
    for text in ("one", "two", "three"):
        message = Message()
        message.add("stt", "said", text)
        await buffer.handle(message, "in", None)
    assert buffer.data["lines"] == ["two", "three"]
    assert message.last("note")["text"].endswith("1. two\n2. three") and message.last("note")["tags"] == ["room.lines"]
    await buffer.call("remove_matching", {"text": "two"})
    assert (await buffer.call("read", {}))["lines"] == ["three"]


async def test_lists_functions_and_undo(hass: HomeAssistant, mock_entry):
    """Add under an entry, change a field, remove and bring back."""
    core = await setup_aac(hass, mock_entry)
    lists = await core.domain.add_card("lists", "Mine")
    await lists.call("add_items", {"list": "Car", "items": ["Radio"]})
    await lists.call("add_items", {"list": "Car", "items": ["Bezel"], "under": "radio"})
    await lists.call("update_item", {"list": "Car", "item": "Bezel", "field": "status", "value": "printing", "done": True})
    assert (await lists.call("read_list", {"list": "car"}))["items"] == [
        {"text": "Radio", "done": False, "children": [{"text": "Bezel", "done": True, "fields": {"status": "printing"}}]}]
    await lists.call("remove_items", {"list": "Car", "items": ["Radio"]})
    assert (await lists.call("read_list", {"list": "Car"}))["items"] == []
    await lists.call("undo_remove", {})
    assert (await lists.call("read_list", {"list": "Car"}))["items"][0]["text"] == "Radio"


async def test_home_assistant_card_access(hass: HomeAssistant, mock_entry):
    """Hidden entities do not exist for AAC; read-only can be seen; only full may be switched."""
    core = await setup_aac(hass, mock_entry)
    home = await core.domain.add_card("homeassistant", "Home", {"levels": {"switch.lamp": "full", "sensor.t": "read"}})
    hass.states.async_set("switch.lamp", "off", {"friendly_name": "Lamp"})
    hass.states.async_set("sensor.t", "21")
    hass.states.async_set("lock.door", "locked")
    found = {e["entity_id"] for e in (await home.call("find", {}))["entities"]}
    assert found == {"switch.lamp", "sensor.t"}
    assert (await home.call("get_state", {"entity_ids": ["lock.door"]}))["lock.door"] == "unknown or not allowed"
    refused = await home.call("call_service", {"domain": "switch", "service": "turn_on", "entity_id": "sensor.t"})
    assert "may not be switched" in refused["error"]


async def test_card_values_in_templates(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A Relay's template reads other cards' values: {{ lists.total }}, {{ cards['lists'].lists }}."""
    core = await setup_aac(hass, mock_entry)
    lists = core.domain.cards["lists"]
    await lists.call("add_items", {"list": "Shopping", "items": ["milk", "eggs"]})
    relay = await core.domain.add_card("relay", "Counts", {"text": "{{ lists.total }} open; {{ cards['lists'].lists | join(',') }}"})
    assert relay.render() == f"{lists.current()['total']} open; {','.join(lists.current()['lists'])}" and "Shopping" in relay.render()


async def test_template_test_reads_when_aac_last_spoke(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A Conditional's template test sees the core's last-spoke times (overall and per satellite) and the entry."""
    import time

    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "kitchen", "name": "Kitchen"}, {"id": "office", "name": "Office"}]
    core.audio._spoke = {"kitchen": time.time() - 4, "office": time.time() - 60}
    card = await core.domain.add_card("conditional", "Follow-up", {"outputs": ["yes"], "rules": [
        {"select": {"by": "kind", "value": "said"}, "test": "template",
         "value": "{{ audio.satellites[entry.data.mic].seconds < 10 }}", "actions": [{"do": "output", "a": "yes"}]}]})
    for mic, port in (("kitchen", "yes"), ("office", "out")):
        message = Message(origin="ears")
        message.add("ears", "said", "and tomorrow?", mic=mic)
        assert await card.handle(message, "in", Step(core.domain.flow, card, "in", message)) == []  # nothing wired
        assert card._template(card.settings["rules"][0], [message.last("said")], message) == (port == "yes")
    assert core.audio.spoken()["last"]["satellite"] == "kitchen"
