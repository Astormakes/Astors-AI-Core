"""The cards on their own."""

from homeassistant.core import HomeAssistant

from custom_components.aac.flow import Step
from custom_components.aac.context import Context

from .conftest import setup_aac


async def sink(domain, name="Sink"):
    """A Relay that takes what arrives and sends nothing (an empty script)."""
    return await domain.add_card("relay", name, {"script": ""})


async def test_relay_runs_top_to_bottom_and_sends_only_what_it_says(hass: HomeAssistant, mock_entry):
    """A new Relay passes everything on; a script that names outputs sends only there (no fallback); a text goes out as
    the Relay's answer in the context that arrived; a card call travels and waits; an empty script sends nothing."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    new = await d.add_card("relay", "New")
    assert new.ports()[:2] == ({"in": "text"}, {"out": "text"})
    check = await d.add_card("relay", "Check", {"script": '''
context = input("in")
said = context.get("*.said")
if said and "computer" in said.text.lower():
    said.text = said.text.replace("Computer,", "").strip()
    room.add(said.text)
    output("aac", context)
    output("note", [said, "noted"])
'''})
    assert check.ports()[:2] == ({"in": "text"}, {"aac": "text", "note": "text"}) and not check.summary()["problems"]
    room = await d.add_card("buffer", "Room", {"position": "nothing"})
    await sink(d, "Yes")
    await sink(d, "Notes")
    d.set_wires([{"from": "new.out", "to": "check.in"}, {"from": "check.aac", "to": "yes.in"}, {"from": "check.note", "to": "notes.in"},
                 {"from": "check.aac", "to": "room.in"}, {"from": "room.out", "to": "check.in"}])
    context = Context()
    context.add("chat", "said", "Computer, lights on", ["said"])
    await d.flow.enter(new, "in", context)
    sent = d.flow.held["check.aac"]
    assert sent.last("said")["text"] == "lights on" and room.data["lines"] == ["lights on"]
    assert [c["name"] for c in sent.last("answer", "check.call")["data"]["calls"]] == ["add"]  # the call travelled, visible
    assert d.flow.held["check.note"].answer == "lights on\nnoted"  # a list: one answer of the Relay
    assert "check.note" not in {w["from"] for w in d.wires if w["to"] == "room.in"}
    d.flow.held.clear()
    other = Context()
    other.add("chat", "said", "hello", ["said"])
    await d.flow.enter(new, "in", other)
    assert "check.aac" not in d.flow.held and "check.out" not in d.flow.held  # no output picked: nothing leaves
    assert check.last_run["outputs"] == [] and await sink(d, "Empty")
    assert d.cards["empty"].ports()[:2] == ({"in": "text"}, {"out": "text"})


async def test_relay_context_is_a_list_of_tagged_texts(hass: HomeAssistant, mock_entry):
    """add / attach / set / remove (where, with at=; what a model reads it as, with role=), reading by tag (own tags
    without a dot, * for any), the whole text, a + b (the left one says where it belongs)."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    relay = await d.add_card("relay", "Texts", {"script": '''
context = input("in")
context.set("role", "You are AAC.", at="first", role="system")
context.add("first", "Read me first.", at="first", role="system")
context.attach("time", f"Asked: {context['chat.said'].text}")
context.set("note", "A note.")
context.set("note", "The note.")
context.attach("rule", "Be brief.", at="first")
context.remove("noise.heard", at="first")
print([e.role for e in context])
print(len(context.all("*.said")), "said;", "role" in context, "chat.said" in context, context.get("nothing"))
print(context.all("*").text.splitlines()[0], "|", context[-1].text, "|", context.text.count("hi"))
both = input("chat", read=True) + context
both.add("both", "x")
output("out", both)
'''})
    assert relay.ports()[0] == {"in": "text", "chat": "read"}
    await sink(d)
    d.set_wires([{"from": "texts.out", "to": "sink.in"}, {"from": "chat.provides", "to": "texts.chat"}])
    context = Context()
    context.add("noise", "said", "hm", ["heard"])
    context.add("chat", "said", "hi", ["said"])
    await relay.handle(context, "in", None)
    assert [(e["kind"], e["text"]) for e in context.entries] == [
        ("system", "Read me first."), ("system", "Be brief."), ("system", "You are AAC."), ("said", "hi"), ("trailer", "Asked: hi"),
        ("said", "The note.")]
    assert {"texts.role", "texts.time", "texts.note", "texts.first", "texts.both", "texts.rule"} <= {t["tag"] for t in d.tag_pool()}
    assert [p["text"] for p in relay.printed] == ["['system', 'system', 'system', 'user', 'user', 'user']", "1 said; True True None",
                                                 "Read me first. | The note. | 2"]
    sent = d.flow.held["texts.out"]
    assert sent.id == context.id and sent.entries[-1]["text"] == "x"  # b's turn, with both's entries


async def test_relay_script_problems(hass: HomeAssistant, mock_entry):
    """What is not allowed is named with its line; a mistake while running is shown and nothing leaves."""
    core = await setup_aac(hass, mock_entry)
    relay = await core.domain.add_card("relay", "Bad", {"script": "import os\ncontext = input(\"in\")\ncontext.__class__\n"})
    problems = relay.summary()["problems"]
    assert any("line 1: import" in p for p in problems) and any("line 3: .__class__" in p for p in problems)
    relay = await core.domain.update_card(relay.id, settings={"script": 'context = input("in")\nx = 1 / 0\noutput("out", context)\n'})
    context = Context()
    context.add("chat", "said", "hi")
    await relay.handle(context, "in", None)
    assert relay.last_run["problems"] == ["line 2: ZeroDivisionError: division by zero"] and relay.last_run["outputs"] == []
    check = lambda script: relay.ui_call("check", {"script": script})  # noqa: E731
    assert (await check("while True:\n    pass"))["problems"] == ["line 1: while (use for) is not allowed here"]
    assert "takes the output's name in quotes" in (await check('output("yes")'))["problems"][0]
    assert "the name is taken" in (await check("def output():\n    pass"))["problems"][0]
    assert "@ above a def" in (await check('@on("x")\ndef f(e):\n    pass'))["problems"][0]
    assert (await check("return 1"))["problems"] == ["line 1: 'return' outside function"]
    heard = await check('heard = input("in").get("x")\nif "stop" in heard:\n    pass')
    assert not heard["problems"]  # checked only when it runs (an entry has no "in": look in its text)


async def test_relay_runs_on_every_piece(hass: HomeAssistant, mock_entry):
    """A piece of a turn still being written runs the script too: output sends the piece on (streaming); a card call
    waits for the finished context; context.finished says which one it is."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    relay = await d.add_card("relay", "Clean", {"script": '''
context = input("in")
answer = context.get("model.answer")
answer.text = answer.text.upper()
print(context.finished)
output("out", context)
if not context.finished:
    lists.lists()  # refused: a mistake from here on (the piece already left)
'''})
    await sink(d)
    d.set_wires([{"from": "clean.out", "to": "sink.in"}, {"from": "clean.out", "to": "lists.in"}, {"from": "lists.out", "to": "clean.in"}])
    context = Context()
    context.add("chat", "said", "hi")
    answer = context.add("model", "answer", "Hel", ["answer"])
    answer["state"], context.progress = "open", True
    await relay.progress(context, "in")
    assert d.flow.held["clean.out"].answer == "HEL" and d.flow.held["clean.out"].progress
    assert "waits for the finished context" in relay.last_run["problems"][0]
    answer["text"], answer["state"], context.progress = "Hello", "closed", False
    await relay.handle(context, "in", None)
    assert d.flow.held["clean.out"].answer == "HELLO" and [p["text"] for p in relay.printed] == ["False", "True"]


async def test_relay_defs_are_functions(hass: HomeAssistant, mock_entry):
    """Every def is a function (off for models until ticked): a call runs only that def, with its arguments, not the
    script; another Relay calls it by name, arguments in order; a Relay with only defs is a toolbox (in, out)."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    tools = await d.add_card("relay", "Tools", {"script": '''
def greet(name, greeting="Hello"):
    """Greets someone."""
    print("greeted", name)
    return f"{greeting}, {name}!"

print("the top ran")
'''})
    assert tools.functions == {"greet": {"help": "Greets someone.", "args": {
        "name": {"type": "string", "help": "", "required": True}, "greeting": {"type": "string", "help": "", "required": False}}}}
    assert tools.offered() == {} and tools.ports()[:2] == ({"in": "text"}, {"out": "text"})
    assert await tools.call("greet", {"name": "Ann"}) == "Hello, Ann!"
    assert [p["text"] for p in tools.printed] == ["greeted Ann"]  # only the def ran
    asker = await d.add_card("relay", "Asker", {"script": 'context = input("in")\noutput("out", tools.greet("Bo", "Hi"))\n'})
    await sink(d)
    d.set_wires([{"from": "asker.out", "to": "tools.in"}, {"from": "tools.out", "to": "asker.in"}, {"from": "asker.out", "to": "sink.in"}])
    context = Context()
    context.add("chat", "said", "go")
    await asker.handle(context, "in", None)
    assert d.flow.held["asker.out"].answer == "Hi, Bo!" and "asker.call" in {t["tag"] for t in d.tag_pool()}


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
context = input("in")
heard = context.get("ears.said")
heard.text = f"{lists.total} open"
if audio.satellites[heard.data["mic"]].seconds < 10:
    output("yes", context)
'''})
    for mic, picked in (("kitchen", True), ("office", False)):
        context = Context()
        context.add("ears", "said", "and tomorrow?", ["said"], mic=mic)
        await card.handle(context, "in", Step(core.domain.flow, card, "in", context))
        assert card.last_run["outputs"] == (["yes"] if picked else []) and context.text == f"{lists.current()['total']} open"
