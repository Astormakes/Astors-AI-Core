"""The core, the default domain and the flow of messages."""

import pytest

from homeassistant.core import HomeAssistant, callback


from .conftest import BASE_URL, model_answers, reply, setup_aac


@pytest.mark.fresh
async def test_first_start_builds_the_default_domain(hass: HomeAssistant, mock_entry):
    """A fresh install: Chat → Charter → Time → Model → back (the Chat takes HA's conversations); Lists, Home, Calendar are its tools; an
    empty list of models means every model for the job, in order."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    assert {"chat", "charter", "time", "model", "lists", "home", "calendar"} <= set(domain.cards)
    assert {"from": "model.out", "to": "chat.answers"} in domain.wires and {"from": "calendar.out", "to": "model.in"} in domain.wires
    assert domain.cards["home"].level("light.other") == "hidden"
    assert core.pool.entries(domain.cards["model"].settings["models"], "text") == [  # an empty list: every model for text
        {"model": "m_big", "queue": True}, {"model": "m_small", "queue": True}]


async def test_a_chat_round_trip(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Chat → Charter (start) → Time (after the turn) → Model → back to the chat, which keeps the conversation."""
    core = await setup_aac(hass, mock_entry)
    asked = model_answers(aioclient_mock, reply("Hello!"), reply("You said hi."))
    chat = core.domain.cards["chat"]
    chat_id, answer = await chat.send("hi")
    assert answer == "Hello!"
    first = asked[0]["messages"]
    assert first[0] == {"role": "system", "content": "Be kind."}
    assert first[-1]["role"] == "user" and first[-1]["content"].startswith("hi\n\nLocal time: ")
    _, answer = await chat.send("what did I say?", chat_id)
    second = asked[1]["messages"]
    assert answer == "You said hi." and [m["role"] for m in second] == ["system", "user", "assistant", "user"]
    assert second[1]["content"] == first[-1]["content"]  # earlier turns stay as they were: the server's cache
    assert [m["text"] for m in chat.get(chat_id)["messages"]] == ["hi", "Hello!", "what did I say?", "You said hi."]
    behind = chat.get(chat_id)["behind"]  # Behind the scenes: the newest turn as the model got it, each part's card
    assert [e["by"] for e in behind["system"]] == ["charter"] and behind["messages"][-2]["attachments"][0]["by"] == "time"


async def test_tools_are_the_cards_wired_out_and_back(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The model calls the Lists card's function; the call and its result go into the message."""
    core = await setup_aac(hass, mock_entry)
    asked = model_answers(aioclient_mock, reply(calls=[("add_items", {"list": "Shopping", "items": ["Milk"]})]),
                          reply("Added milk."))
    _, answer = await core.domain.cards["chat"].send("put milk on the shopping list")
    names = {t["function"]["name"] for t in asked[0]["tools"]}
    assert {"add_items", "find", "call_service"} <= names  # the function's own name: no card id
    assert answer == "Added milk."
    assert {"model.out", "lists.out"} <= set(core.domain.flow.last)  # the call went along the wires and back
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
    _, answer = await core.domain.cards["chat"].send("hi")
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
    chat_id, answer = await domain.cards["chat"].send("hi")
    assert answer == "Hello!"
    before = {"setup": domain.export(), "data": {i: deepcopy(c.data) for i, c in domain.cards.items()},
              "titles": {i: c.title for i, c in domain.cards.items()}}

    await domain.remove_card(*ids)
    assert not domain.cards and not domain.wires and not domain.positions
    assert await domain.undo_last()

    assert domain.export() == before["setup"]
    assert {i: c.data for i, c in domain.cards.items()} == before["data"]
    assert {i: c.title for i, c in domain.cards.items()} == before["titles"]
    _, answer = await domain.cards["chat"].send("still there?", chat_id)
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
    """The chat's Stop while the model writes: the answer is cut, the stop is the context's last entry, and the next
    turn tells the model it was stopped (an old stop does not stop the next turn)."""
    import asyncio

    from custom_components.aac.context import is_stop

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
    sending = hass.async_create_task(chat.send("tell me a long story"))
    await started.wait()
    chat_id = chat.chats()[0]["id"]
    await chat.stop_chat(chat_id)
    _, answer = await sending
    kept = chat.get(chat_id)
    assert answer == "" and kept["messages"][-1]["cut"] and is_stop(kept["behind"]["events"][-1])
    _, answer = await chat.send("go on", chat_id)
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

    async def handle(self, context, port, step):
        said = context.last("said")
        if said:
            said["text"] = said["text"].upper()
        return None
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
        context.add("chat", "said", "hello")
        await shout.handle(context, "in", None)
        assert context.last("said")["text"] == "HELLO" and CARD_TYPES["relay"].title == "My relay"
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
    relay = await d.add_card("relay", "Answer")  # as in the voice chain: model → Answer → Chat and Text to speech
    d.set_wires([*[w for w in d.wires if w != {"from": "model.out", "to": "chat.answers"}], {"from": "model.out", "to": f"{relay.id}.in"},
                 {"from": f"{relay.id}.out", "to": "chat.answers"}, {"from": f"{relay.id}.out", "to": f"{voice.id}.in"}])
    model_answers(aioclient_mock, reply("Let me check.", [("lists__lists", {})]), reply("You have no lists yet. Shall I make one?"))
    spoken, clips = [], []

    async def tts(text, *args):
        spoken.append(text)
        return pcm_to_wav(b"\x00\x00" * 160)
    shown = []
    chat = d.cards["chat"]
    real = chat.progress

    async def watch(context, port):
        shown.append([e["text"] for e in context.entries if e["kind"] == "answer" and e.get("state") == "closed"])
        return await real(context, port)
    def clip(card, port, clip):  # only this test's Text to speech (a voice chain may have its own)
        if card == voice.id:
            clips.append(clip["text"])
    with patch.object(core.audio, "tts", AsyncMock(side_effect=tts)), patch.object(chat, "progress", watch), \
            patch.object(d.flow, "clip", clip):
        _, answer = await chat.send("what lists do I have?")
        await hass.async_block_till_done()
    assert answer == "You have no lists yet. Shall I make one?"
    assert clips == ["Let me check.", "You have no lists yet.", "Shall I make one?"]
    assert ["Let me check."] in shown  # the chat showed it while the model went on


async def test_tools_are_the_cards_in_its_loop(hass: HomeAssistant, mock_entry):
    """A model's tools are the cards with functions in its loop (straight back, or round through other cards: the
    Chat); a card whose functions are all unticked is none; a Relay in the loop has the same tools."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    tools = {c.id for c in d.flow.tools("model")}
    assert tools == {"lists", "home", "calendar", "chat"}
    assert d.cards["model"].summary()["tools"] == sorted(tools)
    await d.update_card("lists", settings={"functions": {fn: {"on": False} for fn in d.cards["lists"].functions}})
    assert "lists" not in {c.id for c in d.flow.tools("model")}


async def test_a_closed_connection_is_tried_again_and_errors_are_not_read(hass: HomeAssistant, mock_entry, aioclient_mock):
    """A kept-alive connection the server had closed is tried once more (the model is not taken for off); when no
    model answers, the turn gets an error entry: shown and returned, but never read by a model (it would copy it)."""
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
    _, answer = await core.domain.cards["chat"].send("hi")
    assert answer == "Fine." and len(tries) == 2 and all(core.pool.usable(m["id"]) for m in core.pool.models)
    aioclient_mock.clear_requests()

    async def off(method, url, data):
        raise aiohttp.ClientConnectionError("off")
    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=off)
    chat_id, answer = await core.domain.cards["chat"].send("still there?")
    assert answer == "Error: no model was available."
    card = core.domain.cards["chat"]
    held = card._context(card.data["chats"][chat_id])
    assert held.entries[-1]["kind"] == "error" and held.events[-1]["kind"] == "error"  # an event
    _, chat = conversation(held, "model", {})
    assert "Error" not in str(chat)


async def test_domains_new_rename_delete(hass: HomeAssistant, mock_entry, hass_storage):
    """The domain menu: a new empty domain, rename, delete (its stores go; the running one hands over; not the last)."""
    core = await setup_aac(hass, mock_entry)
    first = core.domain.id
    new = await core.create("Work")
    await core.switch(new)
    assert core.domain.id == new and core.domain.cards == {}
    await core.domain.add_card("lists", "Lists")
    await core.domain.cards["lists"].call("create_list", {"list": "Tasks"})
    await core.rename(new, "Office")
    assert core.domain.name == "Office" and next(d for d in core.data["domains"] if d["id"] == new)["name"] == "Office"
    await core.delete(new)
    assert core.domain.id == first and all(d["id"] != new for d in core.data["domains"])
    assert not [k for k in hass_storage if k.startswith(f"aac2.card.{new}.") or k == f"aac2.domain.{new}"]
    with pytest.raises(ValueError):
        await core.delete(first)  # the last one stays
    perms = [k for c in core.domain.cards.values() for k, f in c.settings_schema.items() if f.get("permission")]
    assert {"functions", "levels"} <= set(perms) and "default" not in perms


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
    nothing); the Node editor's list of cards counts long settings instead of listing them (so every card fits), and
    gives one card in full."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    await d.update_card("lists", settings={"functions": {"read_list": {"help": "Look here before saying you do not know."},
                                                         "add_items": {"name": "add_to_list"}, "delete_list": {"on": False}}})
    await d.update_card("home", settings={"levels": {f"light.l{i}": "read" for i in range(40)}})
    asked = model_answers(aioclient_mock, reply("Fine."))
    await d.cards["chat"].send("hi")
    tools = {t["function"]["name"]: t["function"]["description"] for t in asked[0]["tools"]}
    assert tools["read_list"] == "Look here before saying you do not know." and "add_to_list" in tools
    assert "add_items" not in tools and "delete_list" not in tools and asked[0]["messages"][0]["content"] == "Be kind."
    editor = await d.add_card("builder", "Node editor")
    listed = await editor.call("cards", {})
    home = next(c for c in listed["cards"] if c["id"] == "home")
    assert home["settings"]["levels"] == "(40 entries: see card)" and "chat.said -> charter.in" in listed["wires"]
    assert listed["count"] == len(d.cards)
    assert len((await editor.call("card", {"card": "home"}))["settings"]["levels"]) == 40


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
    await d.add_card("relay", "Other")  # a new change: nothing to redo
    assert not await d.redo_last()
    buf = d.cards["room"]
    await buf.call("add", {"text": "first"})
    await buf.call("add", {"text": "second"})
    held = buf.held("provides")
    assert [e["text"] for e in held.entries] == ["first", "second"] and held.entries[0]["id"] == buf.held("provides").entries[0]["id"]
    d.set_wires([*d.wires, {"from": "room.provides", "to": "other.reads"}])
    assert {"from": "room.provides", "to": "other.reads"} in d.wires
