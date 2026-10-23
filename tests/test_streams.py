"""The stream bus (spec/streams.md): default wiring by type, modules switched off, recent records."""

import pytest

from custom_components.aac.streams import Bus, Node


async def test_wiring_by_type_and_module_switch():
    """An output reaches every input of its type, not the node itself, only where the module is on."""
    on = {"a": True, "b": True}
    bus = Bus(lambda domain_id, module_id: on[module_id])
    got = []

    async def read(domain_id, port, item):
        got.append((domain_id, port, item["text"]))
        return "read"

    bus.add_node(Node(id="src", title="Source", outputs={"out": "text.line"}, module="a"))
    bus.add_node(Node(id="dst", title="Reader", inputs={"lines": "text.line"}, run=read, module="b"))
    bus.add_node(Node(id="other", title="Other", inputs={"x": "sound.event"}, run=read))
    assert bus.summary("d")["wires"] == [{"from": "src.out", "to": "dst.lines"}]

    assert await bus.emit("d", "src", "out", {"text": "hi"}) == ["read"]
    on["b"] = False  # the reader's module off: nothing arrives, the record is still kept
    assert await bus.emit("d", "src", "out", {"text": "again"}) == []
    assert got == [("d", "lines", "hi")]
    assert [r["text"] for r in bus.recent("d", "src", "out")] == ["hi", "again"]
    assert bus.recent("other-domain", "src", "out") == []


async def test_bad_ports_and_types():
    bus = Bus()
    with pytest.raises(ValueError):
        bus.add_node(Node(id="x", title="X", outputs={"out": "nonsense"}))
    bus.add_node(Node(id="y", title="Y", outputs={"out": "time.tick"}))
    bus.add_node(Node(id="z", title="Z", outputs={"out": "lists.item_changed"}))  # a module's own type
    with pytest.raises(ValueError):
        await bus.emit("d", "y", "nope", {})


async def test_annotating_chain_and_concurrent_readers():
    """Annotating nodes of a type form a chain in their order (writers → first → … → readers); readers of a
    record get the same object, at the same time."""
    import asyncio

    bus = Bus()
    seg = {"speech": "audio.segment"}
    bus.add_node(Node(id="mic", title="Mic", outputs={"segments": "audio.segment", "lines": "text.line"}))
    bus.add_node(Node(id="voices", title="Voices", inputs=seg, outputs=seg, adds=["speaker"], order=20))
    bus.add_node(Node(id="stt", title="STT", inputs=seg, outputs=seg, adds=["text"], order=10))
    assert [n.id for n in bus.annotators("d", "audio.segment")] == ["stt", "voices"]
    wires = bus.summary("d")["wires"]
    assert {"from": "mic.segments", "to": "stt.speech"} in wires and {"from": "stt.speech", "to": "voices.speech"} in wires
    assert {"from": "mic.segments", "to": "voices.speech"} not in wires

    started = []

    async def slow(domain_id, port, item):
        started.append("slow")
        await asyncio.sleep(0.05)
        item["addressed"] = True

    async def fast(domain_id, port, item):
        started.append("fast")

    bus.add_node(Node(id="wake", title="Wake word", inputs={"lines": "text.line"}, run=slow))
    bus.add_node(Node(id="comp", title="Companion", inputs={"lines": "text.line"}, run=fast))
    line = {"time": "2026-10-21T12:00:00+00:00", "text": "hi"}
    await bus.emit("d", "mic", "lines", line)
    assert started == ["slow", "fast"] and line["addressed"] is True
    assert [n.id for n, _ in bus.wired_from("d", "mic", "lines")] == ["wake", "comp"]


async def test_node_view_containers(hass, hass_ws_client, mock_entry, configured):
    """Named, resizable containers are kept per domain; a node belongs to one at most."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    box = {"x": 0, "y": 0, "w": 300, "h": 200}
    await ws.send_json_auto_id({"type": "aac/nodes/containers", "containers": [
        {"id": "c1", "name": "Ears", "nodes": ["stt", "voices"], **box},
        {"id": "c2", "name": "Wakeups", "nodes": ["voices", "heartbeat"], **box}]})
    saved = (await ws.receive_json())["result"]["containers"]
    assert [c["nodes"] for c in saved] == [["stt", "voices"], ["heartbeat"]]
    await ws.send_json_auto_id({"type": "aac/nodes"})
    assert [c["name"] for c in (await ws.receive_json())["result"]["containers"]] == ["Ears", "Wakeups"]


async def test_core_card_settings_and_positions(hass, hass_ws_client, mock_entry, configured):
    """The Agent's prompt and the standard model's settings are edited on their cards (no Master agent page);
    card positions are kept on the server."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes/settings", "node": "agent", "values": {"prompt": "Be calm."}})
    assert (await ws.receive_json())["result"]["values"] == {"prompt": "Be calm."}
    await ws.send_json_auto_id({"type": "aac/nodes/settings", "node": "model", "values": {"temperature": 5}})
    assert (await ws.receive_json())["error"]["code"] == "invalid"  # 0–2
    await ws.send_json_auto_id({"type": "aac/nodes/settings", "node": "model", "values": {"temperature": "0.3", "thinking": True}})
    assert (await ws.receive_json())["result"]["values"]["temperature"] == 0.3
    await ws.send_json_auto_id({"type": "aac/nodes/settings", "node": "reply", "values": {}})
    assert (await ws.receive_json())["error"]["code"] == "invalid"
    master = mock_entry.runtime_data.domain().settings.master
    assert master["prompt"] == "Be calm." and master["thinking"] is True
    await ws.send_json_auto_id({"type": "aac/nodes/positions", "positions": {"chat": {"x": 10.4, "y": 20, "pinned": True}}})
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": "aac/nodes"})
    result = (await ws.receive_json())["result"]
    assert result["positions"] == {"chat": {"x": 10, "y": 20, "pinned": True}}
    agent = next(n for n in result["nodes"] if n["id"] == "agent")
    assert agent["edit_values"] == {"prompt": "Be calm."} and "prompt" in agent["edit_schema"]


async def test_old_places_move_into_the_graph(hass, hass_storage, mock_entry, configured):
    """Up to 1.0.0-dev.12 a domain's graph was spread over module_settings, module_access and node_view; on load
    it is one record per domain (and the old keys are gone)."""
    from custom_components.aac.store import STORAGE_KEY

    from .conftest import setup_aac

    data = hass_storage[STORAGE_KEY]["data"]
    data["module_settings"] = {"assistant": {"charter": {"text": "Be kind."}}}
    data["module_access"] = {"assistant": {"charter": {"hidden": True}, "heartbeat": {"enabled": True}}}
    data["node_view"] = {"assistant": {"containers": [{"id": "c1", "name": "Ears", "nodes": ["stt"], "x": 0, "y": 0, "w": 300, "h": 200}],
                                       "wires": [{"from": "chat.requests", "to": "charter.requests"}],
                                       "instances": {"calm": {"type": "charter", "title": "Calm", "settings": {"text": "Softly."}}}}}
    await setup_aac(hass, mock_entry)
    store = mock_entry.runtime_data.store
    graph = store.data["graphs"]["assistant"]
    assert graph["modules"]["charter"] == {"settings": {"text": "Be kind."}, "hidden": True}
    assert graph["modules"]["heartbeat"]["enabled"] is True
    assert graph["containers"][0]["name"] == "Ears" and graph["wires"] == [{"from": "chat.requests", "to": "charter.requests"}]
    assert graph["nodes"]["calm"]["title"] == "Calm"
    assert not {"module_settings", "module_access", "node_view"} & set(store.data)
    assert mock_entry.runtime_data.bus.wired_by_hand("assistant")


def _model_answers(aioclient_mock, *replies):
    """The model's answers in turn (the last one repeats): a str is text, a tuple a tool call (name, args)."""
    import json

    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    from .conftest import BASE_URL

    calls, queue = [], list(replies or ["ok"])

    async def side_effect(method, url, data):
        calls.append(data)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        message = {"role": "assistant", "content": item} if isinstance(item, str) else {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": item[0], "arguments": json.dumps(item[1])}}]}
        return AiohttpClientMockResponse(method, url, json={"choices": [{"message": message}]})

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    return calls


async def test_node_view_command(hass, hass_ws_client, mock_entry, configured):
    """aac/nodes: the domain's cards with role, kind, module name and flow counts, the wires, what can be added."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    result = (await ws.receive_json())["result"]
    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["model"]["module_name"] == "Core" and nodes["stt"]["kind"] == "annotating" and nodes["stt"]["order"] == 10
    assert nodes["hearing"]["recent"] == {"audio": 0, "segments": 0, "lines": 0}
    assert {"from": "hearing.segments", "to": "stt.speech"} in result["wires"]
    assert {"from": "model.out", "to": "home.in"} in result["wires"] and {"from": "home.out", "to": "model.in"} in result["wires"]
    assert {"text", "audio", "video", "pool"} <= set(result["stream_types"])
    assert nodes["model"]["square"] == ["out"] and nodes["charter"]["square"] == []
    assert {"trailer", "context", "decide", "model", "charter"} <= {t["id"] for t in result["types"]}


async def test_wiring_by_hand(hass, hass_ws_client, aioclient_mock, mock_entry, configured):
    """The owner's wires replace the automatic ones: memory unwired gives neither tools nor recalls; a wrong-typed
    wire is refused; None goes back to automatic."""
    from .conftest import memory_of, setup_aac

    await setup_aac(hass, mock_entry)
    await memory_of(hass).async_remember("The drill is on basement shelf 2.", "fact")
    calls = _model_answers(aioclient_mock)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    auto = (await ws.receive_json())["result"]
    assert auto["by_hand"] is False
    wires = [w for w in auto["wires"] if "memory." not in w["from"] + w["to"]] + [{"from": "agent.out", "to": "model.in"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["result"]["by_hand"] is True
    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "Where is the drill?"})
    await ws.receive_json()
    assert "basement" not in calls[-1]["messages"][-1]["content"]
    assert not any(t["function"]["name"].startswith("memory") for t in calls[-1]["tools"])

    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": [{"from": "hearing.audio", "to": "model.in"}]})
    assert not (await ws.receive_json())["success"]  # audio into a message input: refused
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": None})
    assert (await ws.receive_json())["result"]["by_hand"] is False
    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "Where is the drill?"})
    await ws.receive_json()
    assert any(t["function"]["name"].startswith("memory") for t in calls[-1]["tools"])  # automatic: memory is a tool
    line = [w for w in auto["wires"] if w != {"from": "agent.out", "to": "model.in"}]
    line += [{"from": "agent.out", "to": "memory.in"}, {"from": "memory.out", "to": "model.in"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": line})  # wired into the line: it also recalls
    assert (await ws.receive_json())["success"]
    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "Where is the drill?"})
    await ws.receive_json()
    assert "basement" in calls[-1]["messages"][-1]["content"]


async def test_a_second_model_node(hass, hass_ws_client, aioclient_mock, mock_entry, configured):
    """An added model card with its own tier and instructions answers what is wired into it; with nothing else
    wired to its output it has no tools; removing it takes its wires along."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    calls = _model_answers(aioclient_mock, "Deep.")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {
        "time": {"type": "trailer", "title": "Time", "settings": {"text": "Local time: {{ local_time }}"}},
        "research": {"type": "model", "title": "Research", "settings": {"tier": "smart", "prompt": "Dig deep.", "x": 1}}}})
    assert (await ws.receive_json())["result"]["instances"]["research"]["settings"] == {"tier": "smart", "prompt": "Dig deep."}
    await ws.send_json_auto_id({"type": "aac/nodes"})
    view = (await ws.receive_json())["result"]
    assert not any("research" in w["to"] for w in view["wires"])  # added cards are wired by hand
    wires = [w for w in view["wires"] if w["to"] != "model.in"]
    wires += [{"from": "agent.out", "to": "research.in"}, {"from": "research.out", "to": "chat.in"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["success"]

    await ws.send_json_auto_id({"type": "aac/chat", "text": "hi"})
    assert (await ws.receive_json())["result"]["message"]["text"] == "Deep."
    run = data.runs.runs[-1]
    assert run.tier == "smart" and run.model_node == "research" and "Dig deep." in calls[-1]["messages"][0]["content"]
    assert not calls[-1].get("tools")

    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {}})
    await ws.receive_json()
    assert not any("research" in w["to"] for w in data.store.graph("assistant")["wires"])


async def test_try_it_shows_the_way(hass, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Try it: the answer plus the way through the graph, each node with what it wrote."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    _model_answers(aioclient_mock, "Hi there.")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "hello", "source": "voice"})
    result = (await ws.receive_json())["result"]
    assert result["answer"] == "Hi there." and result["source"] == "voice" and result["model_node"] == "model"
    steps = {s["node"]: s for s in result["trace"]}
    assert steps["charter"]["added"][0]["kind"] == "system" and steps["charter"]["added"][0]["text"].startswith("Charter")
    assert steps["model"]["role"] == "model"
    assert [s["node"] for s in result["trace"]][:3] == ["voice", "charter", "agent"]


async def test_build_a_voice_way(hass, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Start to finish: a voice model with its own charter, instructions and context in the line and only the
    home wired to it: exactly these reach the model; the home answers only the model that called it; the panel
    chat keeps the standard way."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    calls = _model_answers(aioclient_mock, ("home__home_overview", {}), "Lights are off.")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    view = (await ws.receive_json())["result"]
    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {
        "time": {"type": "trailer", "title": "Time", "settings": {"text": "Local time: {{ local_time }}"}},
        "calm": {"type": "charter", "title": "Calm", "settings": {"text": "Be calm."}},
        "short": {"type": "instructions", "title": "Short", "settings": {"text": "Answer in one sentence."}},
        "room": {"type": "context", "title": "Room", "settings": {"text": "The user is in the bedroom."}},
        "vmodel": {"type": "model", "title": "Voice model"}}})
    assert (await ws.receive_json())["success"]
    line = ["voice", "calm", "short", "room", "vmodel"]
    wires = [w for w in view["wires"] if not w["from"].startswith("voice.")]
    wires += [{"from": f"{a}.out", "to": f"{b}.in"} for a, b in zip(line, line[1:])]
    wires += [{"from": "vmodel.out", "to": "voice.in"}, {"from": "vmodel.out", "to": "home.in"}, {"from": "home.out", "to": "vmodel.in"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["success"]

    runs_before = len(mock_entry.runtime_data.runs.runs)
    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "Are the lights on?", "source": "voice"})
    result = (await ws.receive_json())["result"]
    assert result["answer"] == "Lights are off."
    assert len(mock_entry.runtime_data.runs.runs) == runs_before + 1  # the standard model was not woken by the home
    assert [t["node"] for t in result["trace"]][:5] == ["voice", "calm", "short", "room", "vmodel"]
    system, user = calls[-1]["messages"][0]["content"], calls[-1]["messages"][1]["content"]
    assert system.startswith("Be calm.") and "Answer in one sentence." in system and "Charter (the owner" not in system
    assert "The user is in the bedroom." in user
    names = {t["function"]["name"] for t in calls[0]["tools"]}
    assert "home__control" in names and not any(n.startswith(("lists", "memory")) for n in names)

    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "hi", "source": "chat"})
    chat = (await ws.receive_json())["result"]
    assert chat["trace"][1]["node"] == "charter" and chat["model_node"] == "model"


async def test_decide_routes_by_named_outputs(hass, hass_ws_client, aioclient_mock, mock_entry, configured):
    """The owner's 69 example: model 1 answers, Decide picks a named output (the server's grammar limits the choice),
    the chosen way leads on, and the answer still comes home to the panel chat."""
    import json

    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

    from .conftest import BASE_URL, setup_aac

    await setup_aac(hass, mock_entry)
    calls = []

    async def side_effect(method, url, data):
        calls.append(data)
        if data.get("response_format", {}).get("json_schema", {}).get("name") == "decision":
            enum = data["response_format"]["json_schema"]["schema"]["properties"]["output"]["enum"]
            assert enum == ["has_69", "other"]
            return AiohttpClientMockResponse(method, url, json={"choices": [{"message": {"role": "assistant", "content": json.dumps({"output": "has_69"})}}]})
        text = "It contains 69." if "69" in data["messages"][-1]["content"] else "Room 69 is free."
        return AiohttpClientMockResponse(method, url, json={"choices": [{"message": {"role": "assistant", "content": text}}]})

    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=side_effect)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    view = (await ws.receive_json())["result"]
    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {
        "time": {"type": "trailer", "title": "Time", "settings": {"text": "Local time: {{ local_time }}"}},
        "check": {"type": "decide", "title": "Has 69?", "settings": {
            "outputs": [{"name": "has_69", "description": "the text mentions 69"}, {"name": "other", "description": "anything else"}],
            "default": "other"}},
        "second": {"type": "model", "title": "Second"}}})
    assert (await ws.receive_json())["success"]
    wires = [w for w in view["wires"] if not (w["from"] == "model.out" and w["to"] == "chat.in")]
    wires += [{"from": "model.out", "to": "check.in"}, {"from": "check.has_69", "to": "second.in"},
              {"from": "check.other", "to": "chat.in"}, {"from": "second.out", "to": "chat.in"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["success"]

    await ws.send_json_auto_id({"type": "aac/chat", "text": "Which room is free?"})
    reply = (await ws.receive_json())["result"]["message"]
    assert reply["text"] == "It contains 69."  # model 1 → Decide (has_69) → model 2 → the panel chat
    await ws.send_json_auto_id({"type": "aac/nodes/try", "text": "Which room is free?"})
    trace = (await ws.receive_json())["result"]["trace"]
    decided = next(s for s in trace if s["node"] == "check")
    assert decided["output"] == "has_69" and [s["node"] for s in trace if s.get("role") == "model"] == ["model", "second"]



async def test_every_card_says_what_it_does(hass, mock_entry, configured):
    """Descriptions are part of the node contract (the owner reads them on the card; AAC will read them when it
    builds graphs itself): every node and every addable type has one."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    summary = mock_entry.runtime_data.bus.summary("assistant")
    missing = [n["id"] for n in summary["nodes"] if len(n["description"]) < 20]
    missing += [t["id"] for t in summary["types"] if len(t["description"]) < 20]
    assert not missing, missing
