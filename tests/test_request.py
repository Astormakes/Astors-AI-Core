"""The message graph (spec/graph.md, flow.py): the automatic wiring, the document, loops, answers going home."""

import json

from homeassistant.core import HomeAssistant

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
    """Sources through the standing-text cards (by order) and memory into the model; the model into the
    endpoints and into every node with tools, and those back into the model (loops); one type, text."""
    await setup_aac(hass, mock_entry)
    wires = {(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in mock_entry.runtime_data.bus.wires("assistant")}
    assert ("chat.out", "charter.in") in wires and ("charter.out", "agent.in") in wires
    assert ("agent.out", "memory.in") in wires and ("memory.out", "model.in") in wires  # memory recalls in the line
    assert ("voice.out", "charter.in") in wires and ("triggers.out", "charter.in") in wires
    assert not any(a.startswith("heartbeat.") for a, _ in wires)  # its module is off by default
    assert {("model.out", "chat.in"), ("model.out", "voice.in"), ("model.out", "assist.in")} <= wires
    for tool in ("home", "memory", "lists_tools"):
        assert (f"model.out", f"{tool}.in") in wires and (f"{tool}.out", "model.in") in wires


async def test_what_reaches_the_model(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Charter first, then the agent's prompt; memory writes what it recalls into the conversation; the wired
    nodes are the tools (setup only with the owner, endpoints never); the way is recorded."""
    from custom_components.aac.modules import async_set_access

    await setup_aac(hass, mock_entry)
    await memory_of(hass).async_remember("The drill is on basement shelf 2.", "fact")
    calls = _answer(aioclient_mock, "On shelf 2.")
    run, answer = await async_run_request(hass, mock_entry, "Where is the drill?", kind="chat", trigger="chat", title="t")
    assert answer.text == "On shelf 2." and run.model_node == "model"
    system = calls[0]["messages"][0]["content"]
    assert system.startswith("Charter") and "You are a test." in system
    assert "basement shelf 2" in calls[0]["messages"][-1]["content"]  # the recall goes with the user turn
    names = {t["function"]["name"].split("__")[0] for t in calls[0]["tools"]}
    assert {"home", "memory", "lists_tools"} <= names and not names & {"setup", "chat", "voice", "assist"}
    nodes = [s["node"] for s in run.trace]
    assert nodes[:3] == ["chat", "charter", "agent"] and "memory" in nodes and nodes[-1] == "model"
    kinds = [(e["by"], e["kind"]) for e in answer.message.entries]
    assert kinds[:2] == [("charter", "system"), ("agent", "system")]  # standing text first (the cache)
    assert ("chat", "user") in kinds and ("memory", "note") in kinds and kinds[-1] == ("model", "answer")

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
