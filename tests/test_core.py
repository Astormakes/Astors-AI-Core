"""The core, the default domain and the flow of messages."""

from homeassistant.core import HomeAssistant

from custom_components.aac.message import Message

from .conftest import BASE_URL, model_answers, reply, setup_aac, v1_files


async def test_first_start_takes_over_v1(hass: HomeAssistant, mock_entry):
    """v1's models (master order), charter, lists and entity access land in the default domain."""
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
    assert [m["id"] for m in core.pool.models] == ["m_big", "m_small"]
    assert {"chat", "charter", "time", "models", "model", "lists", "home"} <= set(domain.cards)
    assert domain.cards["charter"].settings["text"] == "Be kind."
    assert domain.cards["models"].settings["models"] == ["m_big", "m_small"]
    assert domain.cards["lists"].data["lists"][0]["name"] == "Project Portfolio"
    assert domain.cards["home"].level("switch.lamp") == "full" and domain.cards["home"].level("sensor.outside") == "read"
    assert domain.cards["home"].level("light.other") == "hidden"
    assert {"from": "model.out", "to": "chat.answers"} in domain.wires and domain.undo == []


async def test_a_chat_round_trip(hass: HomeAssistant, mock_entry, aioclient_mock):
    """Chat → Charter (start) → Time (after the turn) → Model → back to the chat, which keeps the conversation."""
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
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
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
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

    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
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
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    core = await setup_aac(hass, mock_entry)
    domain = core.domain
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
    message = Message()
    message.add("chat", "said", "hi")
    await start.handle(message, "in", None)
    await start.handle(message, "in", None)
    await after.handle(message, "in", None)
    message.add("chat", "said", "and?")
    await after.handle(message, "in", None)
    assert [(e["kind"], e["text"]) for e in message.entries] == [
        ("system", "You are AAC."), ("said", "hi"), ("trailer", "Be short."), ("said", "and?"), ("trailer", "Be short.")]
    assert all(t.startswith(("role.", "note.", "chat.")) for e in message.entries for t in e["tags"])


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
