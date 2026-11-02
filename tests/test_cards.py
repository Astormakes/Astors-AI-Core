"""The cards on their own."""

import asyncio

from homeassistant.core import HomeAssistant

from custom_components.aac.context import Context

from .conftest import allow, setup_aac


async def sink(domain, name="Sink"):
    """A Relay that takes what arrives and sends nothing (an empty script)."""
    return await domain.add_card("relay", name, {"script": ""})


async def test_relay_runs_top_to_bottom_and_sends_only_what_it_says(hass: HomeAssistant, mock_entry):
    """A new Relay passes everything on; a script that names outputs sends only there (no fallback); a text goes out as
    the Relay's said in the context that arrived; a card call travels and waits; an empty script sends nothing."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    new = await d.add_card("relay", "New")
    assert new.ports() == ({"in": "text"}, {"out": "text"})
    check = await d.add_card("relay", "Check", {"script": '''
context = input("in")
said = context.get("*.said")
if said and "computer" in said.text.lower():
    said.text = said.text.replace("Computer,", "").strip()
    room.add(said.text)
    output("aac", context)
    output("note", [said, "noted"])
'''})
    assert check.ports() == ({"in": "text"}, {"aac": "text", "note": "text"}) and not check.summary()["problems"]
    room = await d.add_card("buffer", "Room", {"adds": False})
    await sink(d, "Yes")
    await sink(d, "Notes")
    d.set_wires([{"from": "new.out", "to": "check.in"}, {"from": "check.aac", "to": "yes.in"}, {"from": "check.note", "to": "notes.in"},
                 {"from": "check.aac", "to": "room.in"}, {"from": "room.out", "to": "check.in"}])
    context = Context()
    context.add("chat.said", "Computer, lights on")
    await d.flow.enter(new, "in", context)
    sent = d.flow.held["check.aac"]
    assert sent.get("chat.said")["text"] == "lights on" and room.data["lines"] == ["lights on"]
    assert [c["name"] for c in sent.get("check.call")["data"]["calls"]] == ["add"]  # the call travelled, visible
    assert sent.get("room.result") is not None  # and came back with its result
    assert d.flow.held["check.note"].get("check.said")["text"] == "lights on\nnoted"  # a list: one said of the Relay
    d.flow.held.clear()
    other = Context()
    other.add("chat.said", "hello")
    await d.flow.enter(new, "in", other)
    assert "check.aac" not in d.flow.held and "check.out" not in d.flow.held  # no output picked: nothing leaves
    assert check.last_run["outputs"] == [] and await sink(d, "Empty")
    assert d.cards["empty"].ports() == ({"in": "text"}, {"out": "text"})


async def test_relay_context_is_a_list_of_tagged_texts(hass: HomeAssistant, mock_entry):
    """add / set / remove (where, with at=), the system prompt, reading by tag (own tags without a dot, * for any), the
    whole text, a + b."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    relay = await d.add_card("relay", "Texts", {"script": '''
context = input("in")
context.set("context.system", "You are AAC.")
context.add("first", "Read me first.", at="first")
context.add("time", f"Asked: {context['chat.said'].text}")
context.set("note", "A note.")
context.set("note", "The note.")
context.remove("noise.said", at="first")
print([e.tag for e in context][:2], context.system.text)
print(len(context.all("*.said")), "said;", "note" in context, "chat.said" in context, context.get("nothing"))
print(context.all("*").text.splitlines()[0], "|", context[-1].text, "|", context.text.count("hi"))
both = input("chat", read=True) + context
both.add("both", "x")
output("out", both)
'''})
    assert relay.ports()[0] == {"in": "text", "chat": "read"}
    await sink(d)
    d.set_wires([{"from": "texts.out", "to": "sink.in"}, {"from": "chat.provides", "to": "texts.chat"}])
    context = Context()
    context.add("noise.said", "hm")
    context.add("chat.said", "hi")
    await relay.handle(context, "in")
    assert [(e["tag"], e["text"]) for e in context.conversation] == [
        ("texts.first", "Read me first."), ("chat.said", "hi"), ("texts.time", "Asked: hi"), ("texts.note", "The note.")]
    assert context.system["text"] == "You are AAC."
    assert {"texts.time", "texts.note", "texts.first", "texts.both"} <= {t["tag"] for t in d.tag_pool()}
    assert [p["text"] for p in relay.printed] == ["['context.system', 'context.tools'] You are AAC.", "1 said; True True None",
                                                 "You are AAC. | The note. | 2"]
    assert d.flow.held["texts.out"].entries[-1]["text"] == "x"


async def test_relay_script_problems(hass: HomeAssistant, mock_entry):
    """What is not allowed is named with its line; a mistake while running is shown and nothing leaves."""
    core = await setup_aac(hass, mock_entry)
    relay = await core.domain.add_card("relay", "Bad", {"script": "import os\ncontext = input(\"in\")\ncontext.__class__\n"})
    problems = relay.summary()["problems"]
    assert any("line 1: import" in p for p in problems) and any("line 3: .__class__" in p for p in problems)
    relay = await core.domain.update_card(relay.id, settings={"script": 'context = input("in")\nx = 1 / 0\noutput("out", context)\n'})
    context = Context()
    context.add("chat.said", "hi")
    await relay.handle(context, "in")
    assert relay.last_run["problems"] == ["line 2: ZeroDivisionError: division by zero"] and relay.last_run["outputs"] == []
    check = lambda script: relay.ui_call("check", {"script": script})  # noqa: E731
    assert (await check("while True:\n    pass"))["problems"] == ["line 1: while (use for) is not allowed here"]
    assert "takes the output's name in quotes" in (await check('output("yes")'))["problems"][0]
    assert "the name is taken" in (await check("def output():\n    pass"))["problems"][0]
    assert "@ above a def" in (await check('@on("x")\ndef f(e):\n    pass'))["problems"][0]
    assert (await check("return 1"))["problems"] == ["line 1: 'return' outside function"]
    heard = await check('heard = input("in").get("x")\nif "stop" in heard:\n    pass')
    assert not heard["problems"]  # checked only when it runs (an entry has no "in": look in its text)


async def test_relay_script_stops_itself_when_its_time_is_up(hass: HomeAssistant, mock_entry, monkeypatch):
    """A thread cannot be stopped from outside: a script that runs too long stops itself on its next line (it does not
    run on in the background, filling Home Assistant's worker threads); its defs too."""
    import re
    import sys

    import pytest

    core = await setup_aac(hass, mock_entry)
    monkeypatch.setattr(sys.modules["aac_cards.relay.script"], "WAIT", 0.3)
    endless = "for a in range(10000):\n    for b in range(10000):\n        x = a * b\n"
    relay = await core.domain.add_card("relay", "Endless", {"script": 'context = input("in")\n' + endless +
                                                             'def spin():\n    "Spins."\n' + endless.replace("\n", "\n    ").replace("for a", "    for a", 1)})
    context = Context()
    context.add("chat.said", "hi")
    await relay.handle(context, "in")
    assert re.fullmatch(r"line [2-4]: it ran longer than 0.3 s", relay.last_run["problems"][0])
    with pytest.raises(Exception, match=r"line [7-9]: it ran longer than 0.3 s"):
        await relay.call("spin", {})
    assert sys.gettrace() is None  # the check ends with the script

    slow = await core.domain.add_card("relay", "Slow", {"script": 'def wait():\n    "Waits."\n    for i in range(10000):\n        x = i\n    return "done"\n'})
    monkeypatch.setattr(sys.modules["aac_cards.relay.script"], "WAIT", 0.05)
    calls = await core.domain.add_card("relay", "Caller", {"script": 'context = input("in")\nresult = slow.wait()\noutput("out", context)\n'})
    core.domain.set_wires([*core.domain.wires, {"from": "caller.out", "to": "slow.in"}, {"from": "slow.out", "to": "caller.in"}])
    real = calls.travel

    async def slowly(*args):  # the card it calls takes its time: not the script's own
        await asyncio.sleep(0.3)
        return await real(*args)
    calls.travel = slowly
    await calls.handle(context, "in")
    assert calls.last_run["problems"] == []


async def test_relay_runs_on_every_snapshot(hass: HomeAssistant, mock_entry):
    """A context still being written runs the script too: output sends it on (streaming); a card call waits for the
    finished context; context.streaming says which one it is."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    relay = await d.add_card("relay", "Clean", {"script": '''
context = input("in")
answer = context.get("model.said")
answer.text = answer.text.upper()
print(context.streaming)
output("out", context)
if context.streaming:
    lists.lists()  # refused: a mistake from here on (the snapshot already left)
'''})
    await sink(d)
    d.set_wires([{"from": "clean.out", "to": "sink.in"}, {"from": "clean.out", "to": "lists.in"}, {"from": "lists.out", "to": "clean.in"}])
    context = Context()
    context.add("chat.said", "hi")
    answer = context.add("model.said", "Hel")
    answer["state"] = "streaming"
    await relay.handle(context, "in")
    assert d.flow.held["clean.out"].get("model.said")["text"] == "HEL" and d.flow.held["clean.out"].streaming
    assert "waits for the finished context" in relay.last_run["problems"][0]
    answer["text"], answer["state"] = "Hello", "ended"
    await relay.handle(context, "in")
    assert d.flow.held["clean.out"].get("model.said")["text"] == "HELLO" and [p["text"] for p in relay.printed] == ["True", "False"]


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
    assert tools.offered() == {} and tools.ports() == ({"in": "text"}, {"out": "text"})
    assert await tools.call("greet", {"name": "Ann"}) == "Hello, Ann!"
    assert [p["text"] for p in tools.printed] == ["greeted Ann"]  # only the def ran
    asker = await d.add_card("relay", "Asker", {"script": 'context = input("in")\noutput("out", tools.greet("Bo", "Hi"))\n'})
    await sink(d)
    d.set_wires([{"from": "asker.out", "to": "tools.in"}, {"from": "tools.out", "to": "asker.in"}, {"from": "asker.out", "to": "sink.in"}])
    context = Context()
    context.add("chat.said", "go")
    await asker.handle(context, "in")
    assert d.flow.held["asker.out"].get("asker.said")["text"] == "Hi, Bo!" and "asker.call" in {t["tag"] for t in d.tag_pool()}


async def test_buffer_keeps_and_writes(hass: HomeAssistant, mock_entry):
    """The last N lines, added to each context as one entry at the end (the one before goes), sent on; its functions
    change it."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    buffer = await d.add_card("buffer", "Room", {"size": 2})
    await sink(d)
    d.set_wires([*d.wires, {"from": "room.out", "to": "sink.in"}])
    context = Context()
    for text in ("one", "two", "three"):
        context.add("stt.said", text)
        await buffer.handle(context, "in")
        context = d.flow.held["room.out"]
    assert buffer.data["lines"] == ["two", "three"]
    assert [e["text"].splitlines()[-1] for e in context.find("room.lines")] == ["2. three"]  # only the newest stays
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
    """Two layers (docs/v2 page 6): the domain says what exists, the card's May use how far within it; the lower wins.
    Hidden does not exist, list is known without its state, read is seen, only full may be switched; a service must
    be the entity's own, and its data cannot name another target."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    for entity_id, state in (("switch.lamp", "off"), ("sensor.t", "21"), ("lock.door", "locked"), ("light.desk", "on")):
        hass.states.async_set(entity_id, state)
    await d.set_permissions({"switch.lamp": "full", "sensor.t": "full", "light.desk": "list", "lock.door": "hidden"})
    home = await d.add_card("homeassistant", "Home", {"access": {"switch.lamp": "full", "sensor.t": "read",
                                                                 "light.desk": "full", "lock.door": "full"}})
    found = {e["entity_id"]: e for e in (await home.call("find", {}))["entities"]}
    assert set(found) == {"switch.lamp", "sensor.t", "light.desk"}  # the door is hidden by the domain
    assert found["light.desk"]["access"] == "list" and "state" not in found["light.desk"] and found["sensor.t"]["state"] == "21"
    got = await home.call("get_state", {"entity_ids": ["lock.door", "light.desk", "sensor.t"]})
    assert got["lock.door"] == got["light.desk"] == "unknown or not allowed" and got["sensor.t"]["state"] == "21"
    refused = await home.call("call_service", {"domain": "switch", "service": "turn_on", "entity_id": "sensor.t"})
    assert "may not be switched" in refused["error"]

    called = []
    hass.services.async_register("switch", "turn_on", lambda call: called.append(dict(call.data)))
    hass.services.async_register("shell_command", "anything", lambda call: called.append("shell"))
    await home.call("call_service", {"domain": "switch", "service": "turn_on", "entity_id": "switch.lamp",
                                     "data": {"entity_id": "lock.door", "area_id": "kitchen"}})
    assert called == [{"entity_id": "switch.lamp"}]  # its data names no other target
    try:
        await home.call("call_service", {"domain": "shell_command", "service": "anything", "entity_id": "switch.lamp"})
    except PermissionError as err:
        assert "is not a service of switch.lamp" in str(err)
    assert called == [{"entity_id": "switch.lamp"}]
    await d.set_permissions({"switch.lamp": "read"})  # the domain lowers it: the card's full is read now
    assert "may not be switched" in (await d.cards["home"].call("call_service", {"domain": "switch", "service": "turn_on",
                                                                                "entity_id": "switch.lamp"}))["error"]


async def test_ask_waits_for_the_owners_yes(hass: HomeAssistant, mock_entry, hass_ws_client):
    """At ask an action waits for the owner (docs/v2 page 6): the panel and the phone (its two buttons) ask, the first
    answer counts; a no is told to the model, reading needs no yes."""
    from pytest_homeassistant_custom_component.common import async_mock_service

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    hass.states.async_set("switch.lamp", "off")
    switched = async_mock_service(hass, "switch", "turn_on")
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    core.data.setdefault("settings", {})["ask_notify"] = "notify.mobile_app_phone"
    home = await allow(d, "home", {"switch.lamp": "ask"})
    assert home.ha.state("switch.lamp").state == "off"  # reading: no yes needed
    turn_on = {"domain": "switch", "service": "turn_on", "entity_id": "switch.lamp"}

    task = hass.async_create_task(home.call("call_service", turn_on))
    for _ in range(50):
        if core.asks.waiting:
            break
        await asyncio.sleep(0.01)
    (asked,) = core.asks.waiting.values()
    assert "switch.turn_on" in asked["action"] and asked["name"] == "Home"
    for _ in range(50):
        if phone:
            break
        await asyncio.sleep(0.01)
    actions = phone[-1].data["data"]["actions"]
    assert [a["title"] for a in actions] == ["Allow", "Deny"] and actions[0]["action"] == f"AAC_YES_{asked['id']}"
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"AAC_YES_{asked['id']}"})  # the phone's Allow
    assert (await task)["done"] and len(switched) == 1 and not core.asks.waiting

    task = hass.async_create_task(home.call("call_service", turn_on))
    for _ in range(50):
        if core.asks.waiting:
            break
        await asyncio.sleep(0.01)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "aac/asks"})
    (waiting,) = (await client.receive_json())["result"]["asks"]
    await client.send_json_auto_id({"type": "aac/ask/answer", "ask": waiting["id"], "yes": False})  # the panel's Deny
    assert (await client.receive_json())["result"] == {"answered": True}
    try:
        await task
    except PermissionError as err:
        assert "did not allow" in str(err)
    else:
        raise AssertionError("it switched without a yes")
    assert len(switched) == 1


async def test_a_card_asks_for_more(hass: HomeAssistant, mock_entry, hass_ws_client):
    """request_access (every card with needs; the core answers it, so a card that may do nothing can still ask): only
    what the domain lets it know exists; the owner allows some; then the card has it (the domain raised to it)."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    for entity_id in ("light.desk", "light.hall", "lock.door"):
        hass.states.async_set(entity_id, "on")
    await d.set_permissions({"light.desk": "list", "light.hall": "list"})
    home = d.cards["home"]
    got = await home.run("request_access", {"things": ["light.desk", "light.hall", "lock.door"], "level": "full",
                                            "reason": "To switch the lights when asked."})
    assert got["asked"] == {"light.desk": "full", "light.hall": "full"} and got["unknown"] == ["lock.door"]
    (req,) = d.requests
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "aac/request/decide", "request": req["id"], "allowed": ["light.desk"]})
    assert (await client.receive_json())["result"] == {"granted": {"light.desk": "full"}}
    assert not d.requests and d.cards["home"].level("light.desk") == "full" and d.cards["home"].level("light.hall") == "list"
    hall = await allow(d, "home", {"light.hall": "hidden"})  # on a card's own sheet, hidden is said out loud
    assert "light.hall" not in {e["entity_id"] for e in hall.ha.entities()}
    assert d.allows("light.desk") == "full"
    assert "request_access" in d.cards["home"].offered()


async def test_home_assistant_card_reports_the_new_state(hass: HomeAssistant, mock_entry):
    """A device reports its new state after the service returned: call_service waits for it (not the old one)."""
    core = await setup_aac(hass, mock_entry)
    await core.domain.set_permissions({"switch.lamp": "full"})
    home = await core.domain.add_card("homeassistant", "Home", {"access": {"switch.lamp": "full"}})
    hass.states.async_set("switch.lamp", "off")

    async def report_later():
        await asyncio.sleep(0.2)
        hass.states.async_set("switch.lamp", "on")

    async def turn_on(call):  # the service returns at once; the device reports a moment later
        hass.async_create_task(report_later())
    hass.services.async_register("switch", "turn_on", turn_on)
    assert await home.call("call_service", {"domain": "switch", "service": "turn_on", "entity_id": "switch.lamp"}) == \
        {"done": True, "state": "on"}


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
        context.add("ears.said", "and tomorrow?", mic=mic)
        await card.handle(context, "in")
        assert card.last_run["outputs"] == (["yes"] if picked else []) and context.get("ears.said")["text"] == f"{lists.current()['total']} open"
