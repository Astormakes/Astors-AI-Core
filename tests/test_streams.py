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
