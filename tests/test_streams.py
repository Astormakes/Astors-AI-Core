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


async def test_node_view_command(hass, hass_ws_client, mock_entry, configured):
    """aac/nodes: the domain's nodes with kind, module name and flow counts, and the wires."""
    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    result = (await ws.receive_json())["result"]
    nodes = {n["id"]: n for n in result["nodes"]}
    assert nodes["broker"]["module_name"] == "Core" and nodes["broker"]["kind"] == "transform"
    assert nodes["stt"]["kind"] == "annotating" and nodes["stt"]["order"] == 10
    assert nodes["hearing"]["recent"] == {"audio": 0, "segments": 0, "lines": 0}
    assert {"from": "hearing.segments", "to": "stt.speech"} in result["wires"]
    assert "broker.request" in result["types"]


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


async def test_wiring_by_hand(hass, hass_ws_client, mock_entry, configured):
    """The owner's wires replace the automatic ones: a chain runs as wired (memory recall taken out of the
    request chain), a wrong-typed wire is refused, None goes back to automatic."""
    from custom_components.aac.request import ModelRequest, async_prepare

    from .conftest import memory_of, setup_aac

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    await memory_of(hass).async_remember("The drill is on basement shelf 2.", "fact")
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    auto = (await ws.receive_json())["result"]
    assert auto["by_hand"] is False
    # memory recall out of the request chain: agent's output goes straight to whatever came after memory
    wires = [w for w in auto["wires"] if "memory.requests" not in (w["from"], w["to"])]
    after = next(w["to"] for w in auto["wires"] if w["from"] == "memory.requests")
    wires.append({"from": "agent.requests", "to": after})
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["result"]["by_hand"] is True
    request = await async_prepare(hass, mock_entry, ModelRequest(domain=data.domain(), text="Where is the drill?"))
    assert request.context == [] and request.system[0].startswith("Charter")

    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": [{"from": "hearing.audio", "to": "broker.model"}]})
    assert not (await ws.receive_json())["success"]  # audio into a request input: refused

    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": None})
    assert (await ws.receive_json())["result"]["by_hand"] is False
    request = await async_prepare(hass, mock_entry, ModelRequest(domain=data.domain(), text="Where is the drill?"))
    assert any("basement" in c for c in request.context)


async def test_a_second_model_node(hass, hass_ws_client, aioclient_mock, mock_entry, configured):
    """Node types vs. instances: an added model node with its own tier and instructions answers once it is
    wired at the end of the request chain; removing it takes its wires along."""
    from .conftest import BASE_URL, setup_aac

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {
        "research": {"type": "broker", "title": "Research", "settings": {"tier": "smart", "prompt": "Dig deep.", "x": 1}}}})
    saved = (await ws.receive_json())["result"]["instances"]
    assert saved["research"]["settings"] == {"tier": "smart", "prompt": "Dig deep."}
    await ws.send_json_auto_id({"type": "aac/nodes"})
    view = (await ws.receive_json())["result"]
    nodes = {n["id"]: n for n in view["nodes"]}
    assert nodes["research"]["type"] == "broker" and not any("research" in w["to"] for w in view["wires"])  # wired by hand
    # the end of the chain into Research instead of the standard model
    tail = next(w for w in view["wires"] if w["to"] == "broker.model")
    wires = [w for w in view["wires"] if w is not tail] + [{"from": tail["from"], "to": "research.model"}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["success"]

    aioclient_mock.post(f"{BASE_URL}/chat/completions", json={"choices": [{"message": {"role": "assistant", "content": "Deep."}}]})
    await ws.send_json_auto_id({"type": "aac/chat", "text": "hi"})
    reply = (await ws.receive_json())["result"]["message"]
    assert reply["text"] == "Deep."
    run = data.runs.runs[-1]
    assert run.tier == "smart" and any("Research" in s.get("text", "") for s in run.steps)
    assert "Dig deep." in aioclient_mock.mock_calls[-1][2]["messages"][0]["content"]

    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {}})
    await ws.receive_json()
    assert not any("research" in w["to"] for w in data.store.data["node_view"]["assistant"]["wires"])


async def test_sources_and_a_second_charter(hass, hass_ws_client, mock_entry, configured):
    """Each request source has its own node and chain start; an added charter with its own text runs where it
    is wired (voice through it, the panel chat still through the standard one)."""
    from custom_components.aac.request import ModelRequest, async_prepare

    from .conftest import setup_aac

    await setup_aac(hass, mock_entry)
    data = mock_entry.runtime_data
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/nodes"})
    view = (await ws.receive_json())["result"]
    assert {"chat", "voice", "assist", "auto"} <= {n["id"] for n in view["nodes"]}
    assert {"from": "voice.requests", "to": "charter.requests"} in view["wires"]
    await ws.send_json_auto_id({"type": "aac/nodes/instances", "instances": {
        "calm": {"type": "charter", "title": "Calm voice", "settings": {"text": "Speak softly."}}}})
    assert (await ws.receive_json())["success"]
    # voice: through the added charter into the rest of the chain (the panel chat keeps the standard one)
    after = next(w["to"] for w in view["wires"] if w["from"] == "charter.requests")
    wires = [w for w in view["wires"] if w["from"] != "voice.requests"]
    wires += [{"from": "voice.requests", "to": "calm.requests"}, {"from": "calm.requests", "to": after}]
    await ws.send_json_auto_id({"type": "aac/nodes/wires", "wires": wires})
    assert (await ws.receive_json())["success"]

    voice = await async_prepare(hass, mock_entry, ModelRequest(domain=data.domain(), text="hi", source="voice"))
    chat = await async_prepare(hass, mock_entry, ModelRequest(domain=data.domain(), text="hi", source="chat"))
    assert voice.system[0] == "Speak softly." and chat.system[0].startswith("Charter")
    assert voice.tools == chat.tools  # the rest of the chain is shared
