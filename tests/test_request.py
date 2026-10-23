"""The message graph (spec/graph.md, flow.py): the automatic wiring, the document, loops, answers going home."""

import json

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.aac.broker import async_run_request
from custom_components.aac.request import Message

from .conftest import BASE_URL, memory_of, setup_aac


def _answer(aioclient_mock, *replies):
    """Answer the model calls in turn (a str: text, a tuple: a tool call (name, args)); returns the payloads."""
    calls, queue = [], list(replies)

    async def side_effect(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        calls.append(data)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        message = {"role": "assistant", "content": item} if isinstance(item, str) else {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": item[0], "arguments": json.dumps(item[1])}}]}
        return AiohttpClientMockResponse(method, url, json={"choices": [{"message": message}]})

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    return calls


async def test_automatic_wiring(hass: HomeAssistant, mock_entry, configured):
    """Sources through the standing-text cards (by order) into the model; the model into the
    endpoints and into every node with tools, and those back into the model (loops); one type, text."""
    await setup_aac(hass, mock_entry)
    wires = {(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in mock_entry.runtime_data.bus.wires("assistant")}
    assert ("chat.out", "charter.in") in wires and ("charter.out", "agent.in") in wires
    assert ("agent.out", "model.in") in wires and ("agent.out", "memory.in") not in wires  # memory hangs under the model
    assert ("voice.out", "charter.in") in wires and ("triggers.out", "charter.in") in wires
    assert not any(a.startswith("heartbeat.") for a, _ in wires)  # its module is off by default
    assert {("model.out", "chat.in"), ("model.out", "voice.in"), ("model.out", "assist.in")} <= wires
    for tool in ("home", "memory", "lists_tools"):
        assert (f"model.out", f"{tool}.in") in wires and (f"{tool}.out", "model.in") in wires


async def test_what_reaches_the_model(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Charter first, then the agent's prompt; the wired
    nodes are the tools (setup only with the owner, endpoints never); the way is recorded."""
    from custom_components.aac.modules import async_set_access

    await setup_aac(hass, mock_entry)
    await memory_of(hass).async_remember("The drill is on basement shelf 2.", "fact")
    calls = _answer(aioclient_mock, "On shelf 2.")
    run, answer = await async_run_request(hass, mock_entry, "Where is the drill?", kind="chat", trigger="chat", title="t")
    assert answer.text == "On shelf 2." and run.model_node == "model"
    system = calls[0]["messages"][0]["content"]
    assert system.startswith("Charter") and "You are a test." in system
    names = {t["function"]["name"].split("__")[0] for t in calls[0]["tools"]}
    assert {"home", "memory", "lists_tools"} <= names and not names & {"setup", "chat", "voice", "assist"}
    nodes = [s["node"] for s in run.trace]
    assert nodes[:3] == ["chat", "charter", "agent"] and nodes[-1] == "model"
    kinds = [(e["by"], e["kind"]) for e in answer.message.entries]
    assert kinds[:2] == [("charter", "system"), ("agent", "system")]  # standing text first (the cache)
    assert ("chat", "user") in kinds and kinds[-1] == ("model", "answer")
    # what the model got, each part with its source (the 🔍 view)
    sources = mock_entry.runtime_data.domain().runs.request(run.id)["sources"]
    froms = [(x["where"], x["from"]) for x in sources]
    assert froms[0] == ("system", "Charter") and ("system", "Agent") in froms
    assert ("chat", "Panel chat") in froms and froms.count(("end", "Model card (built in): the local time")) == 1
    assert any(w == "tools" for w, _ in froms) and all(x["chars"] > 0 for x in sources)
    assert all(x.get("outside") for x in sources if x["where"] == "tools") and not any(x.get("outside") for x in sources if x["from"] == "Charter")

    await async_set_access(mock_entry, "lists", "assistant", {"enabled": False})
    await async_run_request(hass, mock_entry, "hi", kind="chat", trigger="chat", title="t")
    assert "lists_tools" not in {t["function"]["name"].split("__")[0] for t in calls[-1]["tools"]}


async def test_a_tool_is_a_loop(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Calling a node sends the conversation there; what it writes comes back as the tool result, tagged with
    the call; the expert view sees Chat > … > Model > Lists > Model."""
    await setup_aac(hass, mock_entry)
    calls = _answer(aioclient_mock, ("lists_tools__create_list", {"name": "Groceries"}), "Made it.")
    run, answer = await async_run_request(hass, mock_entry, "Make a grocery list", kind="chat", trigger="chat", title="t")
    assert answer.text == "Made it."
    tool_msg = calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "Groceries" in tool_msg["content"]
    entries = answer.message.entries
    call = next(e for e in entries if e["kind"] == "call")
    result = next(e for e in entries if e["kind"] == "result")
    assert call["data"]["node"] == "lists_tools" and result["by"] == "lists_tools"
    assert result["data"]["call_id"] == call["data"]["call_id"]
    assert [s["node"] for s in answer.message.trace][-3:] == ["model", "lists_tools", "model"] or \
        "lists_tools" in [s["node"] for s in answer.message.trace]


async def test_the_chat_keeps_its_document(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """The chat stores the whole conversation (calls included); an edited answer is what the model reads next."""
    await setup_aac(hass, mock_entry)
    calls = _answer(aioclient_mock, ("lists_tools__lists", {}), "You have no lists.", "Noted.")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/chat", "text": "What lists do I have?"})
    reply = (await ws.receive_json())["result"]
    chat_id = reply["chat_id"]
    chat = mock_entry.runtime_data.chats.get(chat_id)
    assert [e["kind"] for e in chat["entries"]].count("call") == 1
    assert reply["message"]["entry"] == chat["entries"][-1]["id"]
    await ws.send_json_auto_id({"type": "aac/chat/edit", "chat_id": chat_id, "index": 1, "text": "You have three lists."})
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": "aac/chat", "chat_id": chat_id, "text": "Thanks"})
    await ws.receive_json()
    texts = [m.get("content") for m in calls[-1]["messages"]]
    assert "You have three lists." in texts and "You have no lists." not in texts
    assert any(m.get("tool_calls") for m in calls[-1]["messages"])  # the earlier call is part of the conversation


async def test_standing_text_is_replaced_not_repeated(hass: HomeAssistant, mock_entry, configured):
    """A card's standing entry stays where it is (the start of the conversation does not change)."""
    await setup_aac(hass, mock_entry)
    message = Message(domain=mock_entry.runtime_data.domain())
    message.add("chat", "user", "hi")
    message.put("charter", "system", "Be kind.")
    message.put("agent", "system", "You are AAC.")
    message.put("charter", "system", "Be kind.")
    assert [(e["by"], e["kind"]) for e in message.entries] == [("charter", "system"), ("agent", "system"), ("chat", "user")]
    message.put("agent", "system", "")
    assert [e["by"] for e in message.entries] == ["charter", "chat"]


async def test_header_and_trailer(hass: HomeAssistant, mock_entry, configured):
    """A header goes into the system prompt, a trailer after the newest user turn (even while a loop runs)."""
    from custom_components.aac.model_node import build_conversation

    await setup_aac(hass, mock_entry)
    message = Message(domain=mock_entry.runtime_data.domain())
    message.add("chat", "user", "hi")
    message.put("tr", "trailer", "Answer in German.")
    message.add("model", "answer", "Hello.")
    message.add("chat", "user", "lights?")
    system, conv = build_conversation(message, "model", {})
    assert system == []
    assert [c["content"] for c in conv] == ["hi", "Hello.", "lights?\n\nAnswer in German."]


async def test_named_inputs_are_tools(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """The owner names an input: a model wired to it sees that name (a charter's input "addressed" is the tool
    "addressed"); calling it sends the conversation there and on."""
    await setup_aac(hass, mock_entry)
    calls = _answer(aioclient_mock, ("addressed", {}), "Yes, to me.")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    view = (await ws.receive_json())["result"]
    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {
        "said": {"type": "instructions", "title": "Was addressed", "settings": {"text": "The user talks to AAC."}}}})
    assert (await ws.receive_json())["success"]
    wires = view["wires"] + [{"from": "model.out", "to": "said.in"}, {"from": "said.out", "to": "model.in"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": "aac/nodes/names", "names": {
        "said.in": {"name": "addressed", "description": "Call this when the user talks to you."}}})
    assert (await ws.receive_json())["result"]["names"]["said.in"]["name"] == "addressed"
    run, answer = await async_run_request(hass, mock_entry, "Computer, hi", kind="chat", trigger="chat", title="t")
    tool = next(t["function"] for t in calls[0]["tools"] if t["function"]["name"] == "addressed")
    assert tool["description"] == "Call this when the user talks to you."
    assert answer.text == "Yes, to me." and "The user talks to AAC." in calls[1]["messages"][0]["content"]


async def test_buffer_keeps_lines_and_is_a_tool(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """A Buffer in the line keeps the newest lines and writes them in as one block; a model wired to it can
    clear it (owner's universal-node idea)."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    graph = data.store.graph("assistant")
    graph["nodes"] = {"room": {"type": "buffer", "title": "Room", "settings": {"size": 2, "write": "note"}}}
    wires = [(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in data.bus.wires("assistant")]
    wires = [w for w in wires if w != ("agent.out", "model.in")]
    wires += [("agent.out", "room.in"), ("room.out", "model.in"), ("model.out", "room.in")]
    graph["wires"] = [{"from": a, "to": b} for a, b in dict.fromkeys(wires)]
    calls = _answer(aioclient_mock, "ok")
    for text in ("one", "two", "three"):
        await async_run_request(hass, mock_entry, text, kind="chat", trigger="chat", title="t")
    last = calls[-1]["messages"][-1]["content"]
    assert "Room (newest last):\n1. two\n2. three" in last and "1. one" not in last
    assert any(t["function"]["name"] == "room__clear" for t in calls[-1]["tools"])
    aioclient_mock.clear_requests()
    calls = _answer(aioclient_mock, ("room__clear", {}), "Cleared.")
    run, answer = await async_run_request(hass, mock_entry, "forget the room", kind="chat", trigger="chat", title="t")
    result = next(e for e in answer.message.entries if e["kind"] == "result")
    assert json.loads(result["text"]) == {"slots": []}


async def test_heard_lines_become_messages(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Heard lines: a spoken line from Hearing is a message of its own; the answer goes to the Voice card."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    graph = data.store.graph("assistant")
    graph["nodes"] = {"ears": {"type": "heard", "title": "Ears", "settings": {}}}
    wires = [(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in data.bus.wires("assistant")]
    wires += [("hearing.lines", "ears.lines"), ("ears.out", "agent.in")]
    graph["wires"] = [{"from": a, "to": b} for a, b in dict.fromkeys(wires)]
    calls = _answer(aioclient_mock, "Hello from AAC.")
    await data.bus.emit("assistant", "hearing", "lines", {"time": "2026-10-23T08:00:00+00:00", "source": "pc",
                                                          "name": "PC", "kind": "speech", "text": "Computer, hello"})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls and "Computer, hello" in calls[-1]["messages"][-1]["content"]
    run = data.runs.runs[-1]
    assert run.source == "ears" and run.result == "Hello from AAC."


async def test_an_output_with_one_wire(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """A round output set to one wire hands the message to one card only (no copies)."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    graph = data.store.graph("assistant")
    graph["nodes"] = {"m2": {"type": "model", "title": "Second", "settings": {}}}
    wires = [(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in data.bus.wires("assistant")]
    wires += [("agent.out", "m2.in"), ("m2.out", "chat.in")]
    graph["wires"] = [{"from": a, "to": b} for a, b in dict.fromkeys(wires)]
    calls = _answer(aioclient_mock, "ok")
    await async_run_request(hass, mock_entry, "hi", kind="chat", trigger="chat", title="t")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 2  # agent.out → memory (→ model) and → m2: two copies, two models answer
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes/single", "single": ["agent.out"]})
    assert (await ws.receive_json())["result"]["single"] == ["agent.out"]
    await async_run_request(hass, mock_entry, "hi", kind="chat", trigger="chat", title="t")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 3


async def test_the_pool_wire_picks_the_models(hass: HomeAssistant, mock_entry, configured):
    """The standard model takes its models from the pool wired to it (dotted); another pool, another tier."""
    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    wires = {(a, ap, b, bp) for a, ap, b, bp in data.bus.wires("assistant")}
    assert ("pool_master", "models", "model", "pool") in wires
    graph = data.store.graph("assistant")
    graph["wires"] = [{"from": f"{a}.{ap}", "to": f"{b}.{bp}"} for a, ap, b, bp in wires if a != "pool_master"] + \
        [{"from": "pool_fast.models", "to": "model.pool"}]
    from unittest.mock import patch

    seen = []
    with patch.object(data.pool, "endpoint_for", side_effect=lambda tier, bg=False: seen.append(tier)):
        run, _ = await async_run_request(hass, mock_entry, "hi", kind="chat", trigger="chat", title="t")
    assert seen and seen[0] == "fast" and run.tier == "fast"


async def test_chat_cards_and_markdown_files(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured, tmp_path):
    """An added Chat card is a chat of its own (its chats start there); a Markdown card appends answers to its file
    and the panel reads and edits it."""
    from unittest.mock import patch

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    graph = data.store.graph("assistant")
    graph["nodes"] = {"mem": {"type": "chat_card", "title": "Memory", "settings": {}},
                      "log": {"type": "md_file", "title": "Log", "settings": {"file": "log", "append": "answer"}}}
    wires = [(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in data.bus.wires("assistant")]
    wires += [("mem.out", "model.in"), ("model.out", "log.in"), ("log.out", "mem.in")]  # the answer passes the log
    graph["wires"] = [{"from": a, "to": b} for a, b in dict.fromkeys(wires)]
    _answer(aioclient_mock, "Noted in memory.")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/state"})
    state = (await ws.receive_json())["result"]
    assert {"id": "mem", "title": "Memory"} in state["chat_cards"] and state["file_cards"] == [{"id": "log", "title": "Log"}]
    with patch.object(hass.config, "path", side_effect=lambda *p: str(tmp_path.joinpath(*p))):
        await ws.send_json_auto_id({"type": "aac/chat", "text": "remember this", "card": "mem"})
        reply = (await ws.receive_json())["result"]
        chat = data.chats.get(reply["chat_id"])
        assert chat["card"] == "mem" and reply["message"]["text"] == "Noted in memory."
        assert data.runs.runs[-1].source == "mem"
        await hass.async_block_till_done()
        await ws.send_json_auto_id({"type": "aac/notes/get", "node": "log"})
        assert "Noted in memory." in (await ws.receive_json())["result"]["text"]
        await ws.send_json_auto_id({"type": "aac/notes/save", "node": "log", "text": "# Mine\n"})
        assert (await ws.receive_json())["success"]
        await ws.send_json_auto_id({"type": "aac/notes/get", "node": "log"})
        assert (await ws.receive_json())["result"]["text"] == "# Mine\n"


async def test_text_cards_render_ha_templates(hass: HomeAssistant, mock_entry, configured):
    """Header/Trailer/Context texts go through HA's template engine: HA states, the message, cards' values."""
    await setup_aac(hass, mock_entry)
    hass.states.async_set("sensor.outside", "12")
    message = Message(domain=mock_entry.runtime_data.domain(), language="de")
    message.add("chat", "user", "hi")
    message.set_vars("room", lines="1. hello", count=1)
    make = mock_entry.runtime_data.bus.templates["trailer"].make
    card = make("tr", "Trailer", {"text": "Outside {{ states('sensor.outside') }} °C, {{ message.language }}, "
                                          "{{ room.count }} line(s), year {{ now().year }}."})
    await card.handle(message, None)
    assert message.last("trailer")["text"] == f"Outside 12 °C, de, 1 line(s), year {dt_util.now().year}."
    broken = make("bad", "Bad", {"text": "{{ nope( }}"})
    await broken.handle(message, None)
    assert "[template error:" in message.last("trailer", "bad")["text"]
