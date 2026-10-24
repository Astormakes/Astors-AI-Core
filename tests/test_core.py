"""The core, the default domain and the flow of messages."""

from homeassistant.core import HomeAssistant

from custom_components.aac.context import Context

from .conftest import BASE_URL, model_answers, reply, setup_aac


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
    kept = core.domain.cards["model"].requests[chat_id]  # the panel's Request view: each part's cards
    assert kept["messages"][0]["_from"] == ["charter"] and kept["messages"][-1]["_from"] == ["chat", "time"]


async def test_tools_are_the_cards_wired_out_and_back(hass: HomeAssistant, mock_entry, aioclient_mock):
    """The model calls the Lists card's function; the call and its result go into the message."""
    core = await setup_aac(hass, mock_entry)
    asked = model_answers(aioclient_mock, reply(calls=[("lists__add_items", {"list": "Shopping", "items": ["Milk"]})]),
                          reply("Added milk."))
    _, answer = await core.domain.cards["chat"].send("put milk on the shopping list")
    names = {t["function"]["name"] for t in asked[0]["tools"]}
    assert {"lists__add_items", "home__find", "home__call_service"} <= names
    assert answer == "Added milk."
    shopping = await core.domain.cards["lists"].call("read_list", {"list": "Shopping"})
    assert shopping["items"] == [{"text": "Milk", "done": False}]
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
    new_id = await core.clone("Test")
    await core.switch(new_id)
    assert core.domain.id == "test" and core.domain.cards["lists"].data["lists"][0]["name"] == "Project Portfolio"


async def test_relay_positions_and_replace(hass: HomeAssistant, mock_entry):
    """A Relay's text: start (replaced in place), after the turn (a new line every time), nothing (only passes on)."""
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    start = await domain.add_card("relay", "Role", {"position": "start", "text": "You are AAC."})
    after = await domain.add_card("relay", "Note", {"position": "after", "text": "Be short.", "replace": False})
    context = Context()
    context.add("chat", "said", "hi")
    await start.handle(context, "in", None)
    await start.handle(context, "in", None)
    await after.handle(context, "in", None)
    context.add("chat", "said", "and?")
    await after.handle(context, "in", None)
    assert [(e["kind"], e["text"]) for e in context.entries] == [
        ("system", "You are AAC."), ("said", "hi"), ("trailer", "Be short."), ("said", "and?"), ("trailer", "Be short.")]
    assert all(t.startswith(("role.", "note.", "chat.")) for e in context.entries for t in e["tags"])


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
    assert answer == "" and kept["messages"][-1]["cut"] and is_stop(kept["entries"][-1])
    _, answer = await chat.send("go on", chat_id)
    assert answer == "Fine." and any(m["content"] == "[stopped]" or "[stopped]" in str(m["content"]) for m in asked[-1]["messages"])


def _shipped_version(stem: str) -> str:
    """The VERSION of one of AAC's own card files."""
    import re
    from pathlib import Path

    import custom_components.aac.cards as cards

    return re.search(r'VERSION = "([\d.]+)"', (Path(cards.__file__).parent / f"{stem}.py").read_text()).group(1)


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
    with patch.object(core.audio, "tts", AsyncMock(side_effect=tts)), patch.object(chat, "progress", watch), \
            patch.object(d.flow, "clip", lambda card, port, clip: clips.append(clip["text"])):
        _, answer = await chat.send("what lists do I have?")
        await hass.async_block_till_done()
    assert answer == "You have no lists yet. Shall I make one?"
    assert spoken == clips == ["Let me check.", "You have no lists yet.", "Shall I make one?"]
    assert ["Let me check."] in shown  # the chat showed it while the model went on


async def test_tools_are_the_cards_in_the_loop(hass: HomeAssistant, mock_entry):
    """A model's tools are the cards with functions on a way from its output back into it (straight, or through a
    Chat and Relays); a card can opt out; the answer never goes to cards wired straight back."""
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    tools = {c.id for c in d.flow.tools("model")}
    assert {"chat", "lists", "home", "calendar"} <= tools and "charter" not in tools  # a Relay has no functions
    assert d.cards["model"].summary()["tools"] == sorted(tools)
    await d.update_card("chat", settings={"offers": False})
    assert "chat" not in {c.id for c in d.flow.tools("model")}
