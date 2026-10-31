"""The cards on their own."""

from homeassistant.core import HomeAssistant

from custom_components.aac.flow import Step
from custom_components.aac.context import Context

from .conftest import setup_aac


async def test_relay_script_edits_tags_calls_and_routes(hass: HomeAssistant, mock_entry):
    """If the newest said entry contains 'computer': replace, tag, call a wired Buffer, go to output 'aac'."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    check = await domain.add_card("relay", "Check", {"script": '''
@on("*", kind="said")
def for_me(e):
    if "computer" in e.text.lower():
        e.text = e.text.replace("Computer,", "").strip()
        e.add_tag("addressed")
        room.add(e.text)
        output("aac")
'''})
    assert check.ports()[1] == {"out": "text", "aac": "text"} and not check.summary()["problems"]
    buffer = await domain.add_card("buffer", "Room", {"position": "nothing"})
    yes = await domain.add_card("relay", "Yes")
    no = await domain.add_card("relay", "No")
    domain.set_wires([{"from": "check.aac", "to": "yes.in"}, {"from": "check.out", "to": "no.in"},
                      {"from": "check.out", "to": "room.in"}, {"from": "room.out", "to": "check.in"}])
    context = Context()
    context.add("chat", "said", "Computer, lights on")
    route = await check.handle(context, "in", Step(domain.flow, check, "in", context))
    said = context.last("said")
    assert route == [("aac", "yes")] and said["text"] == "lights on" and "user.addressed" in said["tags"]
    assert buffer.data["lines"] == ["lights on"] and [c["name"] for c in context.last("answer")["data"]["calls"]] == ["add"]  # the call travelled
    other = Context()
    other.add("chat", "said", "hello")
    assert await check.handle(other, "in", Step(domain.flow, check, "in", other)) == [("out", "no")]  # the buffer is a tool


async def test_relay_new_texts_and_places(hass: HomeAssistant, mock_entry):
    """@new at the start (replaced in place), at the end (a new one each turn), before a tag; other tags read."""
    core = await setup_aac(hass, mock_entry)
    relay = await core.domain.add_card("relay", "Texts", {"script": '''
@new("role")
def role(e):
    e.text = """You are AAC."""

@new("time", at="end", replace=False)
def time(e):
    e.text = f"Asked: {chat.said.text}"

@new("room", before="chat.said")
def room(e):
    e.text = "In the kitchen."
'''})
    context = Context()
    context.add("chat", "said", "hi", ["said"])
    await relay.handle(context, "in", None)
    context.add("chat", "said", "and?", ["said"])
    await relay.handle(context, "in", None)
    assert [(e["kind"], e["text"]) for e in context.entries] == [
        ("system", "You are AAC."), ("said", "hi"), ("trailer", "Asked: hi"), ("note", "In the kitchen."), ("said", "and?"),
        ("trailer", "Asked: and?")]
    assert {"texts.role", "texts.time", "texts.room"} <= {t["tag"] for t in core.domain.tag_pool()}
    assert set(relay.functions) == {"role", "time", "room"} and relay.offered() == {}  # a function per block, off until ticked
    assert await relay.call("role", {}) == "You are AAC."  # as a tool: that block's text, now


async def test_relay_script_problems(hass: HomeAssistant, mock_entry):
    """What is not allowed is named with its line; a mistake while running leaves the context as it was."""
    core = await setup_aac(hass, mock_entry)
    relay = await core.domain.add_card("relay", "Bad", {"script": "import os\n@on(\"*\")\ndef x(e):\n    e.__class__\n"})
    problems = relay.summary()["problems"]
    assert any("line 1: import" in p for p in problems) and any("line 4: .__class__" in p for p in problems)
    relay = await core.domain.update_card(relay.id, settings={"script": '@on("*")\ndef x(e):\n    e.text = 1 / 0\n'})
    context = Context()
    context.add("chat", "said", "hi")
    await relay.handle(context, "in", None)
    assert context.last("said")["text"] == "hi" and "ZeroDivisionError" in relay.last_run["problems"][0]
    assert (await relay.ui_call("check", {"script": "while True:\n    pass"}))["problems"] == ["line 1: while (use for) is not allowed here",
                                                                                         "line 1: at the top only def blocks and x = … (put it inside a def)"]


async def test_relay_holds_a_growing_entry(hass: HomeAssistant, mock_entry):
    """A snapshot: an open entry a block works on is held back (its text empty); others pass; live blocks see it."""
    core = await setup_aac(hass, mock_entry)
    relay = await core.domain.add_card("relay", "Clean", {"script": '@on("model.answer")\ndef clean(e):\n    e.text = e.text.upper()\n'})
    context = Context()
    context.add("chat", "said", "hi")
    answer = context.add("model", "answer", "Hel", ["answer"])
    answer["state"] = "open"
    await relay.progress(context, "in")
    assert answer["text"] == "" and context.last("said")["text"] == "hi"
    answer["text"], answer["state"] = "Hello", "closed"
    await relay.handle(context, "in", None)
    assert answer["text"] == "HELLO"
    live = await core.domain.add_card("relay", "Live", {"script": '@on("model.answer", live=True)\ndef words(e):\n    output("seen")\n'})
    answer["text"], answer["state"] = "Hel", "open"
    assert await live.progress(context, "in") == []  # nothing wired to seen; it was picked
    assert live.ports()[1] == {"out": "text", "seen": "text"}


async def test_buffer_keeps_and_writes(hass: HomeAssistant, mock_entry):
    """The last N lines, written as one note; its functions change it."""
    core = await setup_aac(hass, mock_entry)
    buffer = await core.domain.add_card("buffer", "Room", {"size": 2})
    for text in ("one", "two", "three"):
        context = Context()
        context.add("stt", "said", text)
        await buffer.handle(context, "in", None)
    assert buffer.data["lines"] == ["two", "three"]
    assert context.last("note")["text"].endswith("1. two\n2. three") and context.last("note")["tags"] == ["room.lines"]
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


async def test_lists_undo_puts_entries_back_in_place(hass: HomeAssistant, mock_entry):
    """Undo brings entries back where they were (a sub-entry under its parent, in its place), and a list too."""
    core = await setup_aac(hass, mock_entry)
    lists = await core.domain.add_card("lists", "Mine")
    await lists.call("add_items", {"list": "Car", "items": ["Radio", "Seats", "Tyres"]})
    await lists.call("add_items", {"list": "Car", "items": ["Bezel", "Knob", "Cable"], "under": "radio"})
    before = (await lists.call("read_list", {"list": "Car"}))["items"]
    await lists.call("remove_items", {"list": "Car", "items": ["Seats", "Knob", "Cable"]})
    assert [i["text"] for i in (await lists.call("read_list", {"list": "Car"}))["items"]] == ["Radio", "Tyres"]
    await lists.call("undo_remove", {})
    assert (await lists.call("read_list", {"list": "Car"}))["items"] == before
    await lists.call("create_list", {"list": "House"})
    await lists.call("delete_list", {"list": "Car"})
    await lists.call("undo_remove", {})
    assert [x["name"] for x in await lists.call("lists", {})] == ["Car", "House"]


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


async def test_relay_reads_values_and_when_aac_last_spoke(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A script reads other cards' values (lists.total) and the core's last-spoke times per satellite."""
    import time

    core = await setup_aac(hass, mock_entry)
    lists = core.domain.cards["lists"]
    await lists.call("add_items", {"list": "Shopping", "items": ["milk", "eggs"]})
    core.audio.settings["known"] = [{"id": "kitchen", "name": "Kitchen"}, {"id": "office", "name": "Office"}]
    core.audio._spoke = {"kitchen": time.time() - 4, "office": time.time() - 60}
    card = await core.domain.add_card("relay", "Follow-up", {"script": '''
@on("*", kind="said")
def soon(e):
    e.text = f"{lists.total} open"
    if audio.satellites[e.data["mic"]].seconds < 10:
        output("yes")
'''})
    for mic, picked in (("kitchen", True), ("office", False)):
        context = Context(origin="ears")
        context.add("ears", "said", "and tomorrow?", mic=mic)
        await card.handle(context, "in", Step(core.domain.flow, card, "in", context))
        assert card.last_run["outputs"] == (["yes"] if picked else ["out"]) and context.text == f"{lists.current()['total']} open"
