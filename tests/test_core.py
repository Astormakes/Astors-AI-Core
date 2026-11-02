"""The core, the default domain and the flow of messages."""

import pytest

from homeassistant.core import HomeAssistant, callback


from .conftest import BASE_URL, model_answers, reply, setup_aac


@pytest.mark.fresh
async def test_first_start_builds_the_default_domain(hass: HomeAssistant, mock_entry):
    """A fresh install: Chat → Charter → Time → Model → back (the Chat takes HA's conversations); Lists, Home, Calendar and the Chat are its tools (from its calls output); an
    empty list of models means every model for the job, in order."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    assert {"chat", "charter", "time", "model", "lists", "home", "calendar"} <= set(domain.cards)
    assert {"from": "model.out", "to": "chat.answers"} in domain.wires and {"from": "calendar.out", "to": "model.in"} in domain.wires
    assert {"from": "model.calls", "to": "calendar.in"} in domain.wires and {"from": "model.out", "to": "calendar.in"} not in domain.wires
    assert domain.cards["home"].level("light.other") == "hidden"
    assert core.pool.entries(domain.cards["model"].settings["models"], "text") == [  # an empty list: every model for text
        {"model": "m_big", "queue": True}, {"model": "m_small", "queue": True}]


async def test_a_chat_round_trip(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Chat → Charter (start) → Time (after the turn) → Model → back to the chat, which keeps the conversation."""
    core = await setup_aac(hass, mock_entry)
    asked = model_answers(aioclient_mock, reply("Hello!"), reply("You said hi."))
    chat = core.domain.cards["chat"]
    chat_id, answer = await chat.write("hi")
    assert answer == "Hello!"
    first = asked[0]["messages"]
    assert first[0] == {"role": "system", "content": "Be kind."}
    assert first[-1]["role"] == "user" and first[-1]["content"].startswith("hi\n\nLocal time: ")
    _, answer = await chat.write("what did I say?", chat_id)
    second = asked[1]["messages"]
    assert answer == "You said hi." and [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == first[-1]["content"]  # earlier turns stay as they were: the server's cache
    assert [m["text"] for m in chat.get(chat_id)["messages"]] == ["hi", "Hello!", "what did I say?", "You said hi."]
    behind = chat.get(chat_id)["behind"]["entries"]  # the Context view: every entry with its tag
    assert behind[0]["text"] == "Be kind." and [e["tag"] for e in behind].count("time.time") == 2


async def test_tools_are_the_cards_wired_out_and_back(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The model calls the Lists card's function; the call and its result go into the message."""
    core = await setup_aac(hass, mock_entry)
    asked = model_answers(aioclient_mock, reply(calls=[("add_items", {"list": "Shopping", "items": ["Milk"]})]),
                          reply("Added milk."))
    _, answer = await core.domain.cards["chat"].write("put milk on the shopping list")
    names = {t["function"]["name"] for t in asked[0]["tools"]}
    assert {"add_items", "find", "call_service"} <= names  # the function's own name: no card id
    assert answer == "Added milk."
    assert {"model.calls", "lists.out"} <= set(core.domain.flow.last)  # the call went along the wires and back
    shopping = await core.domain.cards["lists"].call("read_list", {"list": "Shopping"})
    assert {"text": "Milk", "done": False} in shopping["items"]
    assert asked[1]["messages"][-1]["role"] == "tool"


async def test_the_next_model_when_a_machine_is_off(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The Pool's order: the first that answers."""
    import aiohttp
    core = await setup_aac(hass, mock_entry)
    seen = []

    async def side_effect(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
        seen.append(data["model"])
        if data["model"] == "big":
            raise aiohttp.ClientConnectionError("off")
        return AiohttpClientMockResponse(method, url, json=reply("small here"))

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    _, answer = await core.domain.cards["chat"].write("hi")
    assert answer == "small here" and seen == ["big", "small"] and not core.pool.usable("m_big")


async def test_undo_slots_and_clone(hass: HomeAssistant, mock_entry):
    """Undo brings the setup back; slots keep it; a clone is a separate domain with the cards' data."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    await domain.cards["lists"].call("add_items", {"list": "Project Portfolio", "items": ["Car Projects"]})
    domain.save_slot("1")
    await domain.add_card("buffer", "Room")
    assert "room" in domain.cards
    assert await domain.undo_last() and "room" not in domain.cards
    await domain.remove_card("time")
    assert await domain.load_slot("1") and "time" in domain.cards
    wires = domain.wires
    await domain.add_card("buffer", "Room", at={"x": 580, "y": 300})
    positions = dict(domain.positions)
    await domain.remove_card("time", "room")  # several: one Undo step brings both back
    assert "time" not in domain.cards and "room" not in domain.cards and not any("time." in w["from"] + w["to"] for w in domain.wires)
    assert await domain.undo_last() and "time" in domain.cards and "room" in domain.cards and domain.wires == wires
    assert domain.positions == positions
    new_id = await core.clone("Test")
    await core.switch(new_id)
    assert core.domain.id == "test" and "Project Portfolio" in [li["name"] for li in core.domain.cards["lists"].data["lists"]]


async def test_undo_is_stored_as_changes(hass: HomeAssistant, mock_entry, hass_storage):
    """Each stored Undo step keeps only what differs from the setup after it (a big setting that stays is stored once);
    after a restart every step comes back whole, and a step stored whole (as before) still loads."""
    import json

    from custom_components.aac.core import UNDO_STEPS, unpacked

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    big = {"light.x": "x" * 5000}
    await d.update_card("charter", settings={"levels": big})
    for i in range(UNDO_STEPS + 3):
        await d.update_card("time", settings={"step": i % 2})
    await d.remove_card("lists")
    whole = json.loads(json.dumps(d.undo))
    await d.save_now()
    stored = hass_storage[f"aac2.domain.{d.id}"]["data"]["undo"]
    assert len(stored) == UNDO_STEPS and len(json.dumps(stored)) < len(json.dumps(whole)) / 4
    assert sum(json.dumps(step).count("x" * 5000) for step in stored) <= 1
    assert unpacked(stored, {"cards": d._specs, "wires": d.wires}) == whole  # noqa: SLF001
    assert unpacked(whole, {"cards": {}, "wires": []}) == whole  # whole steps stay as they are
    await d.unload()
    await d.load()
    assert d.undo == whole
    assert await d.undo_last() and "lists" in d.cards


async def test_removed_cards_data_stays_for_undo_or_goes(hass: HomeAssistant, mock_entry, hass_storage):
    """A removed card's data stays while Undo (or Redo, a slot) can bring the card back, then goes; the owner can let it
    go at once (Undo brings the card back empty)."""
    from custom_components.aac.core import UNDO_STEPS

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    key = lambda card: f"aac2.card.{d.id}.{card}"  # noqa: E731
    await d.cards["lists"].call("add_items", {"list": "Car", "items": ["Radio"]})
    assert d.cards["lists"].summary()["has_data"] and not d.cards["time"].summary()["has_data"]
    await d.remove_card("lists")
    await hass.async_block_till_done()
    assert key("lists") in hass_storage
    assert await d.undo_last() and "Car" in [li["name"] for li in d.cards["lists"].data["lists"]]

    await d.remove_card("lists", drop_data=True)
    assert key("lists") not in hass_storage
    assert await d.undo_last() and "lists" in d.cards and "Car" not in str(d.cards["lists"].data)

    await d.cards["lists"].call("add_items", {"list": "Car", "items": ["Radio"]})
    await d.remove_card("lists")
    for i in range(UNDO_STEPS - 1):
        await d.update_card("time", settings={"step": i % 2})
        await hass.async_block_till_done()
        assert key("lists") in hass_storage  # an Undo step still knows it
    await d.update_card("time", settings={"step": 2})
    await hass.async_block_till_done()
    assert key("lists") not in hass_storage  # no setup knows it any more

    added = await d.add_card("lists", "Errands")  # only Redo knows it after Undo: a new change lets it go
    await added.call("add_items", {"list": "Shop", "items": ["Milk"]})
    assert key("errands") in hass_storage and await d.undo_last()
    await hass.async_block_till_done()
    assert key("errands") in hass_storage
    await d.update_card("time", settings={"step": 3})
    await hass.async_block_till_done()
    assert key("errands") not in hass_storage


async def test_every_card_survives_remove_and_undo(hass: HomeAssistant, mock_entry, aioclient_mock):
    """One card of every type, wired, placed and with data: all removed at once, one Undo brings everything back as it
    was (settings, wires, positions, each card's own data), and the cards work again."""
    from copy import deepcopy

    from custom_components.aac.cards import CARD_TYPES

    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    model_answers(aioclient_mock, reply("Hello!"), reply("Still here."))
    for i, card_type in enumerate(sorted(set(CARD_TYPES) - {c.type for c in domain.cards.values()})):
        await domain.add_card(card_type, at={"x": 290 * (i % 6), "y": 600 + 200 * (i // 6)})
    ids = list(domain.cards)
    wires = len(domain.wires)
    domain.set_wires([*domain.wires, *({"from": f"{a}.out", "to": f"{b}.in"} for a, b in zip(ids, ids[1:]))])
    assert len(domain.wires) > wires  # only the wires of matching kinds stay
    await domain.cards["lists"].call("add_items", {"list": "Project Portfolio", "items": ["Survives Undo"]})
    chat_id, answer = await domain.cards["chat"].write("hi")
    assert answer == "Hello!"
    before = {"setup": domain.export(), "data": {i: deepcopy(c.data) for i, c in domain.cards.items()},
              "titles": {i: c.title for i, c in domain.cards.items()}}

    await domain.remove_card(*ids)
    assert not domain.cards and not domain.wires and not domain.positions
    assert await domain.undo_last()

    assert domain.export() == before["setup"]
    assert {i: c.data for i, c in domain.cards.items()} == before["data"]
    assert {i: c.title for i, c in domain.cards.items()} == before["titles"]
    _, answer = await domain.cards["chat"].write("still there?", chat_id)
    assert answer == "Still here." and len(domain.cards["chat"].get(chat_id)["messages"]) == 4
    assert "Survives Undo" in str(await domain.cards["lists"].call("read_list", {"list": "Project Portfolio"}))


async def test_setup_file_round_trip(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Save file → change → Load file brings the setup back; Undo goes back to before the load."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    saved = domain.export()
    await domain.remove_card("time")
    changed = domain.export()
    await domain.import_setup(saved)
    assert "time" in domain.cards and domain.wires == saved["wires"]
    assert await domain.undo_last() and "time" not in domain.cards and domain.wires == changed["wires"]


async def test_stop_cuts_the_answer_and_stays_visible(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Stop (a core function: the chat's Stop button) while the model writes: its answer is cut and goes out, the chat
    shows "stopped", and the next turn tells the model it was stopped."""
    import asyncio

    from .conftest import BASE_URL

    core = await setup_aac(hass, mock_entry)
    chat, started, asked = core.domain.cards["chat"], asyncio.Event(), []

    async def slow(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
        asked.append(data)
        if len(asked) == 1:
            started.set()
            await asyncio.sleep(3600)
        return AiohttpClientMockResponse(method, url, json=reply("Fine."))

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=slow)
    sending = hass.async_create_task(chat.write("tell me a long story"))
    await started.wait()
    chat_id = chat.chats()[0]["id"]
    await chat.stop_all()
    _, answer = await sending
    kept = chat.get(chat_id)
    assert answer == "" and kept["messages"][-1]["cut"] == "stopped"
    _, answer = await chat.write("go on", chat_id)
    assert answer == "Fine." and any("[cut off: stopped]" in str(m["content"]) for m in asked[-1]["messages"])


def _shipped_version(stem: str) -> str:
    """The VERSION of one of AAC's own card files."""
    import re
    from pathlib import Path

    import custom_components.aac.cards as cards

    here = Path(cards.__file__).parent
    file = here / stem / "card.py" if (here / stem).is_dir() else here / f"{stem}.py"
    return re.search(r'VERSION = "([\d.]+)"', file.read_text()).group(1)


async def test_card_files_in_your_folder(hass: HomeAssistant, mock_entry):
    """A card file in <config>/aac/cards/ is a card type like AAC's own; AAC's files are copied there, but a newer file
    of the same name stays (it replaces AAC's card); a broken file does not stop the others."""
    from pathlib import Path

    from custom_components.aac.cards import CARD_TYPES

    folder = Path(hass.config.path("aac", "cards"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "shout.py").write_text('''from custom_components.aac.api import Card, register


@register
class Shout(Card):
    """Upper case."""

    type = "shout"
    title = "Shout"
    help = "Makes the newest line upper case."

    async def handle(self, context, port):
        said = context.get("*.said")
        if said:
            said["text"] = said["text"].upper()
            await self.send(context)
''')
    (folder / "broken.py").write_text("this is not python")
    builtin_relay = CARD_TYPES["relay"]
    (folder / "relay.py").write_text('''from custom_components.aac.api import Card, register

VERSION = "9.0.0"


@register
class MyRelay(Card):
    type = "relay"
    title = "My relay"
''')
    try:
        core = await setup_aac(hass, mock_entry)
        shout = await core.domain.add_card("shout", "Shout")
        from custom_components.aac.context import Context
        context = Context()
        context.add("chat.said", "hello")
        await shout.handle(context, "in")
        assert context.get("*.said")["text"] == "HELLO" and CARD_TYPES["relay"].title == "My relay"
        assert CARD_TYPES["relay"].version == "9.0.0" and CARD_TYPES["chat"].version == _shipped_version("chat")
    finally:  # HA's test config folder is shared by every test
        CARD_TYPES["relay"] = builtin_relay
        CARD_TYPES.pop("shout", None)
        for name in ("shout", "broken", "relay"):  # AAC's relay.py comes back at the next start
            (folder / f"{name}.py").unlink(missing_ok=True)
            for cached in (folder / "__pycache__").glob(f"{name}.*"):
                cached.unlink()


async def test_a_model_talks_while_it_works(hass: HomeAssistant, mock_entry, aioclient_mock):
    """What a model says before its tool calls stays and goes out at once; each finished sentence goes out while it
    writes (snapshots): Text to speech speaks sentence by sentence, the Chat shows the turn, the others pass it on."""
    from unittest.mock import AsyncMock, patch

    from custom_components.aac.audio import pcm_to_wav

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    voice = await d.add_card("text_to_speech", "Text to speech")
    d.set_wires([*d.wires, {"from": "model.out", "to": f"{voice.id}.in"}])  # the answer goes to every card wired from out
    model_answers(aioclient_mock, reply("Let me check.", [("lists", {})]), reply("You have no lists yet. Shall I make one?"))
    spoken, clips = [], []

    async def tts(text, *args):
        spoken.append(text)
        return pcm_to_wav(b"\x00\x00" * 160)
    shown = []
    chat = d.cards["chat"]
    real = chat.handle

    async def watch(context, port):
        shown.append([e["text"] for e in context.find("model.said") if e["state"] == "ended"])
        return await real(context, port)
    def clip(card, port, clip):  # only this test's Text to speech (a voice chain may have its own)
        if card == voice.id:
            clips.append(clip["text"])
    with patch.object(core.audio, "tts", AsyncMock(side_effect=tts)), patch.object(chat, "handle", watch), \
            patch.object(d.flow, "clip", clip):
        _, answer = await chat.write("what lists do I have?")
        await hass.async_block_till_done()
    assert answer == "You have no lists yet. Shall I make one?"
    assert clips == ["Let me check.", "You have no lists yet.", "Shall I make one?"]
    assert ["Let me check."] in shown  # the chat showed it while the model went on


async def test_the_answer_goes_to_every_card_wired_from_out(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A turn the Chat started: the answer goes to the Chat and to every other card wired from out too (no rule sends
    it only back where it started); Lists gets it, finds no call for it and sends nothing."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    d.set_wires([*d.wires, {"from": "model.out", "to": "lists.in"}])  # lists.out → model.in is there (it is a tool)
    asked = model_answers(aioclient_mock, reply("Hello."), reply("Hello again."))
    _, answer = await d.cards["chat"].write("hi")
    assert answer == "Hello." and len(asked) == 1
    assert d.flow.last["model.out"]["time"] and "lists.out" not in d.flow.held  # Lists got it and sent nothing


async def test_tools_are_the_cards_its_calls_reach(hass: HomeAssistant, mock_entry):
    """A model's tools are the cards with functions it reaches from its calls output (straight, or through other
    cards) and that are wired back to it; a card wired only from its out (the answer) is none, nor one without a way
    back; a card whose functions are all unticked is none."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    tools = {c.id for c in d.flow.tools("model")}
    assert tools == {"lists", "home", "calendar", "chat"}
    assert d.cards["model"].summary()["tools"] == sorted(tools)
    d.set_wires([w for w in d.wires if w != {"from": "model.calls", "to": "home.in"}] + [{"from": "model.out", "to": "home.in"}])
    assert "home" not in {c.id for c in d.flow.tools("model")}  # it only gets the answers now
    d.set_wires([w for w in d.wires if w != {"from": "calendar.out", "to": "model.in"}])
    assert "calendar" not in {c.id for c in d.flow.tools("model")}  # reached, but its result could not come back
    await d.update_card("lists", settings={"functions": {fn: {"on": False} for fn in d.cards["lists"].functions}})
    assert "lists" not in {c.id for c in d.flow.tools("model")}


async def test_stop_is_a_core_function(hass: HomeAssistant, mock_entry):
    """Stop goes to every card at once and travels nowhere (owner 2026-11-01: "stop the vacuum" made the Stop word send
    one; the Model's out to Chat and Schedule, both back into the Charter, doubled it every round until Home Assistant
    froze)."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    sched = await d.add_card("schedule", "Schedule")
    d.set_wires([*d.wires, {"from": "model.out", "to": f"{sched.id}.answers"}, {"from": f"{sched.id}.out", "to": "charter.in"}])
    seen, stopped = [], []
    real = d.flow.enter

    async def counted(card, port, context):
        seen.append(card.id)
        await real(card, port, context)
    d.flow.enter = counted
    for card in d.cards.values():
        real_stop = card.stop

        async def stop(card=card, real_stop=real_stop):
            stopped.append(card.id)
            await real_stop()
        card.stop = stop
    await d.cards["chat"].stop_all()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert seen == [] and set(stopped) == set(d.cards) and not d.red


async def test_a_storm_turns_the_wire_red(hass: HomeAssistant, mock_entry, hass_ws_client):
    """More than STORM contexts through one wire within a second: it is blocked (red), kept over a restart, the owner
    gets a notification, a model hears it from the Card editor with every answer; unblocked by the owner or a model."""
    from unittest.mock import patch

    from custom_components.aac.context import Context
    from custom_components.aac.core import STORM

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.add_card("relay", "A")
    await d.add_card("relay", "B")
    await d.add_card("relay", "C")
    loop = [("a.out", "b.in"), ("b.out", "c.in"), ("c.out", "a.in")]
    d.set_wires([*d.wires, *({"from": a, "to": b} for a, b in loop)])  # a loop
    context = Context()
    context.add("a.said", "round and round")
    with patch("custom_components.aac.core.persistent_notification.async_create") as note:
        await d.flow.send(d.cards["a"], context, "out")
    assert len(d.red) == 1 and (d.red[0]["from"], d.red[0]["to"]) in loop and note.call_count == 1
    assert d.flow.passes("x.out", "y.in") and STORM >= 10 and core.storm == STORM
    assert d.log.read(levels=["error"], what="red")
    red = d.red[0]
    assert d._data()["red"] == d.red and d.summary()["red"] == d.red  # kept, and the panel shows it

    await d.set_permissions({"setup": "full"})
    editor = await d.add_card("builder", "Card editor", {"access": {"setup": "full"}})
    told = await editor.call("types", {})
    assert told["red_wires"] == [f"{red['from']} -> {red['to']}"] and isinstance(told["result"], list)
    assert "(red: blocked)" in str((await editor.call("cards", {}))["wires"])
    assert (await editor.call("unblock", {"from": red["from"], "to": red["to"]}))["unblocked"] is True
    assert not d.red and "red_wires" not in await editor.call("cards", {})  # fixed: nothing to tell

    d.red.append(dict(red))  # again, unblocked by the owner on the Cards page
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "aac/wire/unblock", "from": red["from"], "to": red["to"]})
    assert (await client.receive_json())["result"] == {"unblocked": True, "red": []}
    d.red.append(dict(red))  # a red wire that is removed is no longer red
    d.set_wires([w for w in d.wires if not (w["from"] == red["from"] and w["to"] == red["to"])])
    assert not d.red


async def test_a_call_left_behind_is_not_made_again(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A chat that kept a turn half-way (an answer whose call has no result yet) and a new line said on top: a new
    turn, the model is asked (the old call is not sent around again; owner 2026-11-01: it went Model ↔ Home 200 times)."""
    core = await setup_aac(hass, mock_entry)
    chat = core.domain.cards["chat"]
    from custom_components.aac.context import Context

    half = chat._new("Half")
    held = Context()
    held.add("chat.said", "is the TV on?")
    held.add("model.said", "", calls=[{"id": "c1", "name": "home", "card": "home", "function": "find", "args": {}}])
    half["context"] = held.to_dict()
    aioclient_mock.post(f"{BASE_URL}/chat/completions", json=reply("Hello."))
    _, answer = await chat.write("computer?", half["id"])
    assert answer == "Hello." and aioclient_mock.call_count == 1


async def test_a_closed_connection_is_tried_again_and_errors_are_read(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A kept-alive connection the server had closed is tried once more (the model is not taken for off); when no
    model answers, the turn gets an error entry: shown, returned, and read by a model next time like any entry."""
    import aiohttp
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    from custom_components.aac.cards.model import conversation

    core = await setup_aac(hass, mock_entry)
    tries = []

    async def flaky(method, url, data):
        tries.append(1)
        if len(tries) == 1:
            raise aiohttp.ServerDisconnectedError()
        return AiohttpClientMockResponse(method, url, json=reply("Fine."))
    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=flaky)
    _, answer = await core.domain.cards["chat"].write("hi")
    assert answer == "Fine." and len(tries) == 2 and all(core.pool.usable(m["id"]) for m in core.pool.models)
    aioclient_mock.clear_requests()

    async def off(method, url, data):
        raise aiohttp.ClientConnectionError("off")
    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=off)
    chat_id, answer = await core.domain.cards["chat"].write("still there?")
    assert answer == "Error: no model was available."
    from custom_components.aac.context import Context

    card = core.domain.cards["chat"]
    held = Context.from_dict(card.data["chats"][chat_id]["context"])
    assert held.newest["tag"] == "model.error"
    _, chat = conversation(held, "model")
    assert "Error: no model was available." in str(chat)


async def test_domains_new_rename_delete(hass: HomeAssistant, mock_entry, hass_storage):
    """The domain menu: a new empty domain, rename (its id is its name: stores and files move), delete (its stores and
    files go; the running one hands over; not the last); a domain whose id does not fit its name moves when AAC loads."""
    core = await setup_aac(hass, mock_entry)
    first = core.domain.id
    work = await core.create("Work")
    await core.switch(work)
    assert work == "work" and core.domain.id == work and core.domain.cards == {}
    await core.domain.add_card("lists", "Lists")
    await core.domain.cards["lists"].call("create_list", {"list": "Tasks"})
    await hass.async_add_executor_job(lambda: (core.domain.cards["lists"].folder.mkdir(parents=True), (core.domain.cards["lists"].folder / "kept").write_text("x")))
    new = await core.rename(work, "Office")
    assert new == "office" and core.domain.id == new and core.domain.name == "Office" and core.data["active"] == new
    assert next(d for d in core.data["domains"] if d["id"] == new)["name"] == "Office"
    assert "Tasks" in [li["name"] for li in core.domain.cards["lists"].data["lists"]]
    assert f"aac2.card.{new}.lists" in hass_storage and not [k for k in hass_storage if f".{work}" in k]
    assert await hass.async_add_executor_job((core.folder(new, "lists") / "kept").is_file)
    assert not await hass.async_add_executor_job(core.folder(work).exists)
    assert await core.rename(new, "Office") == new  # the same name: nothing moves
    await core.domain.update_card("lists", title="Jobs")  # a card's files go with its id too
    assert await hass.async_add_executor_job((core.folder(new, "jobs") / "kept").is_file)
    await core.delete(new)
    assert not await hass.async_add_executor_job(core.folder(new).exists)
    assert core.domain.id == first and all(d["id"] != new for d in core.data["domains"])
    assert not [k for k in hass_storage if k.startswith(f"aac2.card.{new}.") or k == f"aac2.domain.{new}"]
    with pytest.raises(ValueError):
        await core.delete(first)  # the last one stays

    next(d for d in core.data["domains"] if d["id"] == first)["name"] = "Home"  # as an older AAC left it
    await core.save_now()
    await core.domain.save_now()
    await core.domain.stop()
    await core.domain.unload()
    core.domain = None
    await core.load()
    assert core.domain.id == "home" and core.data["active"] == "home" and "chat" in core.domain.cards
    assert "aac2.domain.home" in hass_storage and f"aac2.domain.{first}" not in hass_storage
    perms = [k for c in core.domain.cards.values() for k, f in c.settings_schema.items() if f.get("permission")]
    assert {"functions", "access"} <= set(perms) and "default" not in perms


async def test_a_model_that_does_not_answer_is_flagged_until_it_does(hass: HomeAssistant, mock_entry):
    """The panel warns about models that do not answer: from the first failure until they answer again."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.aac.const import SIGNAL

    core = await setup_aac(hass, mock_entry)
    told = []
    unsub = async_dispatcher_connect(hass, SIGNAL, callback(lambda e: told.append(e["what"])))  # at once, not scheduled
    core.pool.mark_down("m_big")
    assert next(m for m in core.pool.summary() if m["id"] == "m_big")["failing"] and told.count("models") == 1
    core.pool.mark_down("m_big")  # still failing: told once
    core.pool.mark_up("m_big")
    assert not next(m for m in core.pool.summary() if m["id"] == "m_big")["failing"] and told.count("models") == 2
    unsub()


async def test_for_models_and_short_card_lists(hass: HomeAssistant, mock_entry, aioclient_mock):
    """For models: the owner's name and description of a function are what the model sees (the system prompt gets
    nothing); the Card editor's list of cards counts long settings instead of listing them (so every card fits), and
    gives one card in full."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.update_card("lists", settings={"functions": {"read_list": {"help": "Look here before saying you do not know."},
                                                         "add_items": {"name": "add_to_list"}, "delete_list": {"on": False}}})
    await d.update_card("home", settings={"access": {f"light.l{i}": "read" for i in range(40)}})
    asked = model_answers(aioclient_mock, reply("Fine."))
    await d.cards["chat"].write("hi")
    tools = {t["function"]["name"]: t["function"]["description"] for t in asked[0]["tools"]}
    assert tools["read_list"] == "Look here before saying you do not know." and "add_to_list" in tools
    assert "add_items" not in tools and "delete_list" not in tools and asked[0]["messages"][0]["content"] == "Be kind."
    await d.set_permissions({"setup": "read"})
    editor = await d.add_card("builder", "Card editor", {"access": {"setup": "full"}})
    listed = await editor.call("cards", {})
    home = next(c for c in listed["cards"] if c["id"] == "home")
    assert home["settings"]["access"] == "(40 entries: see card)" and "chat.said -> charter.in" in listed["wires"]
    assert listed["count"] == len(d.cards)
    assert len((await editor.call("card", {"card": "home"}))["settings"]["access"]) == 40
    assert "may not use setup at full" in (await editor.call("wire", {"from": "chat.said", "to": "model.in"}))["error"]


async def test_a_card_file_with_a_new_version_loads_while_aac_runs(hass: HomeAssistant, mock_entry):
    """A card file whose VERSION went up is loaded again while AAC runs; its running cards are made again (settings
    and data kept); saving it without a new VERSION does nothing."""
    import asyncio
    from pathlib import Path
    from unittest.mock import patch

    from custom_components.aac.cards import CARD_TYPES

    folder = Path(hass.config.path("aac", "cards"))
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / "echo_test.py"
    source = """from custom_components.aac.api import Card, register

VERSION = "{v}"


@register
class EchoTest(Card):
    type = "echo_test"
    title = "{title}"
    settings_schema = {{"word": {{"type": "text", "title": "Word", "default": ""}}}}
"""
    file.write_text(source.format(v="1.0.0", title="Echo one"))
    try:
        core = await setup_aac(hass, mock_entry)
        card = await core.domain.add_card("echo_test", "Echo", {"word": "kept"})
        with patch("custom_components.aac.core.CARD_FILES_EVERY", 0):
            task = hass.async_create_task(core.watch_cards(str(folder)))
            file.write_text(source.format(v="1.0.0", title="Echo saved"))  # no new VERSION: nothing happens
            await asyncio.sleep(0.2)
            assert CARD_TYPES["echo_test"].title == "Echo one" and core.domain.cards[card.id] is card
            file.write_text(source.format(v="1.1.0", title="Echo two"))
            for _ in range(50):
                if core.domain.cards[card.id] is not card:
                    break
                await asyncio.sleep(0.05)
            task.cancel()
        new = core.domain.cards[card.id]
        assert new is not card and type(new).title == "Echo two" and new.settings["word"] == "kept"
        from custom_components.aac.cards import reload_changed
        file.unlink()
        assert await hass.async_add_executor_job(reload_changed, str(folder)) == {"echo_test"} and "echo_test" not in CARD_TYPES
    finally:  # HA's test config folder is shared by every test
        CARD_TYPES.pop("echo_test", None)
        file.unlink(missing_ok=True)
        for cached in (folder / "__pycache__").glob("echo_test.*"):
            cached.unlink()


async def test_redo_and_buffer_provides(hass: HomeAssistant, mock_entry):
    """Redo brings back what Undo took (until the next change); a Buffer provides its lines on a dotted wire."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    buf = await d.add_card("buffer", "Room")
    assert await d.undo_last() and "room" not in d.cards
    assert await d.redo_last() and "room" in d.cards
    assert await d.undo_last() and await d.redo_last() and "room" in d.cards
    await d.add_card("relay", "Other", {"script": 'context = input("in") + input("reads", read=True)\noutput("out", context)\n'})  # a new change: nothing to redo
    assert not await d.redo_last()
    buf = d.cards["room"]
    await buf.call("add", {"text": "first"})
    await buf.call("add", {"text": "second"})
    held = buf.held("provides")
    assert [e["text"] for e in held.conversation] == ["first", "second"] and held.conversation[0]["id"] == buf.held("provides").conversation[0]["id"]
    d.set_wires([*d.wires, {"from": "room.provides", "to": "other.reads"}])
    assert {"from": "room.provides", "to": "other.reads"} in d.wires


async def test_a_card_id_is_its_name(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A card is called the same everywhere: renaming it moves its id through wires, positions, its data, the other
    cards' settings (a script's references) and stored entries, slots too; Undo and Redo move it
    back and forth; an id that does not fit its name moves when the domain loads."""
    import json
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    model_answers(aioclient_mock, reply("Hi."))
    chat = d.cards["chat"]
    await chat.write("hi")
    await d.cards["lists"].call("add_items", {"list": "Car", "items": ["Radio"]})
    script = 'context = input("in")\nseen = context.get("time.time")\nprint(seen, lists.total, seen.data.get("lists"), cards["lists"])\n'
    await d.add_card("relay", "Watcher", {"script": script})
    d.save_slot("1")
    where = d.positions.get("lists")

    await d.update_card("lists", title="Projects")
    assert "projects" in d.cards and "lists" not in d.cards and d.cards["projects"].name == "Projects"
    assert {"from": "model.calls", "to": "projects.in"} in d.wires and not any("lists." in w["from"] + w["to"] for w in d.wires)
    assert d.positions.get("projects") == where
    assert [li["name"] for li in d.cards["projects"].data["lists"]] == [li["name"] for li in (await d.load_card_data("projects"))["lists"]]
    assert "Car" in [li["name"] for li in d.cards["projects"].data["lists"]] and await d.load_card_data("lists") is None
    watcher = d.cards["watcher"].settings["script"]
    assert 'projects.total' in watcher and 'seen.data.get("lists")' in watcher and 'cards["projects"]' in watcher
    assert "projects" in d.slots["1"]["cards"] and "lists" not in d.slots["1"]["cards"]

    await d.update_card("time", title="Clock")
    stored = json.dumps(chat.data)
    assert '"clock.time"' in stored and '"time.time"' not in stored
    assert 'context.get("clock.time")' in d.cards["watcher"].settings["script"]
    assert await d.undo_last() and "time" in d.cards and "clock" not in d.cards and d.cards["time"].name == "Time"
    assert '"time.time"' in json.dumps(d.cards["chat"].data) and 'context.get("time.time")' in d.cards["watcher"].settings["script"]
    assert await d.redo_last() and "clock" in d.cards and '"clock.time"' in json.dumps(d.cards["chat"].data)  # Undo made the cards again

    d._specs["charter"]["title"] = "Rules"  # noqa: SLF001 - as an older AAC left it: a name its id does not fit
    await d.save_now()
    await d.unload()
    await d.load()
    assert "rules" in d.cards and "charter" not in d.cards and {"from": "chat.said", "to": "rules.in"} in d.wires
    await d.add_card("buffer", "Rules")  # a second card with that name
    assert "rules_2" in d.cards


async def test_aacs_own_log(hass: HomeAssistant, mock_entry, hass_ws_client):
    """AAC's log (progress 38): what passes a card's wires as its Log setting says (the monitor reads it), whole contexts
    only a few minutes and never on disk, a repeat counted up, what fails with its trace, entries following a card's
    rename, rotation, kept over a restart and a domain rename; a copy starts its own log."""
    import time
    from unittest.mock import patch

    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.add_card("relay", "A")
    await d.add_card("log", "Sink")
    d.set_wires([*d.wires, {"from": "a.out", "to": "sink.in"}])
    first = Context()
    first.add("a.said", "first line")
    await d.flow.send(d.cards["a"], first, "out")
    out = d.log.read(card="a", what="out")
    assert out[-1]["text"] == "first line" and out[-1]["port"] == "out" and out[-1]["to"] == ["sink.in"] and "context" not in out[-1]
    assert not d.log.read(card="a", what="in")  # by default only what goes out

    a = await d.update_card("a", settings={"log": "context", "log_at": "both"})
    second = Context()
    second.add("x.said", "second")
    await d.flow.enter(a, "in", second)
    got = d.log.read(card="a", levels=["flow"], last=2)
    assert [e["what"] for e in got] == ["in", "out"] and all(e["context"]["entries"] for e in got)
    with patch("custom_components.aac.log.time.time", return_value=time.time() + 601):  # whole contexts go
        assert all("context" not in e for e in d.log.read(card="a"))

    await d.update_card("a", settings={"log": "nothing"})
    before = len(d.log.read(card="a", levels=["flow"]))
    await d.flow.send(d.cards["a"], first, "out")
    assert len(d.log.read(card="a", levels=["flow"])) == before
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "aac/monitor", "wire": "a.out"})
    mon = (await client.receive_json())["result"]
    assert mon["off"] and mon["records"][-1]["text"] == "second"

    for _ in range(3):
        d.log.write("warning", "same thing", part="audio")
    assert d.log.entries[-1]["n"] == 3 and d.log.read(part="*", levels=["warning"])[-1]["text"] == "same thing"

    sink = d.cards["sink"]
    with patch.object(type(sink), "handle", side_effect=RuntimeError("boom")):
        await d.flow.send(d.cards["a"], first, "out")
    err = d.log.read(levels=["error"])[-1]
    assert err["card"] == "sink" and "boom" in err["text"] and "RuntimeError" in err["trace"]
    await d.update_card("sink", title="Drain")  # its entries follow its id
    assert d.log.read(card="drain", levels=["error"]) and not d.log.read(card="sink", levels=["error"])

    with patch("custom_components.aac.log.KEEP", 5):  # two lanes: a busy card's wires never push the rest out
        for i in range(8):
            d.log.write("info", f"n{i}", part="core")
        assert [e["text"] for e in d.log.entries if e["level"] != "flow"] == ["n3", "n4", "n5", "n6", "n7"]
        for i in range(8):
            d.log.write("flow", f"f{i}", card="a", what="out", port="out")
        assert [e["text"] for e in d.log.entries] == ["n3", "n4", "n5", "n6", "n7", "f3", "f4", "f5", "f6", "f7"]
    a = await d.update_card("a", settings={"log": "context"})
    await d.flow.send(d.cards["a"], first, "out")
    await d.log.flush()
    on_disk = await hass.async_add_executor_job(d.log.path.read_text)
    assert '"entries"' not in on_disk and "n7" in on_disk  # a whole context never reaches the disk

    await client.send_json({"id": 2, "type": "aac/log", "search": "n7"})
    assert [e["text"] for e in (await client.receive_json())["result"]["entries"]] == ["n7"]
    new_id = await core.rename(d.id, "Home Base")  # the file moves with the domain and is read again
    assert core.domain.id == new_id and core.domain.log.read(search="n7") and core.domain.log.read(what="renamed")
    copy = await core.clone("Copy")
    assert not await hass.async_add_executor_job((core.folder(copy) / "log.jsonl").exists)
    await client.send_json({"id": 3, "type": "aac/log/clear"})
    await client.receive_json()
    assert not core.domain.log.entries and not await hass.async_add_executor_job(core.domain.log.path.read_text)
