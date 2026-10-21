"""The stream bus (spec/streams.md): the core as a collection of typed streams.

Owner design (2026-10-21): like the calendar is a collection of the modules' entries, audio, video,
timers and requests to the broker are streams the modules write and read; "everything can be wired
into anything", node-based. The core only knows the stream types, keeps the last records of every
output in RAM and delivers each record to the inputs wired to it. Default wiring is by type: an input
gets every output of its type (except the node's own), in domains where the reading node's module is on.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from .const import LOGGER

# type → class: "continuous" (raw media, RAM ring buffer, never stored) or "record" (small JSON objects)
STREAM_TYPES: dict[str, str] = {
    "audio.pcm": "continuous",
    "audio.segment": "record",
    "text.line": "record",
    "sound.event": "record",
    "ha.event": "record",
    "time.tick": "record",
    "broker.request": "record",
    "broker.answer": "record",
    "say": "record",
    "notify": "record",
    "video.frame": "continuous",
    "vision.detection": "record",
}

KEEP_RECORDS = 200  # per output and domain
KEEP_FOR = timedelta(minutes=10)

# run(domain_id, port, item) → anything (handed back to the writer by emit)
NodeRun = Callable[[str, str, dict[str, Any]], Awaitable[Any]]


@dataclass
class Node:
    """A module's worker: typed inputs and outputs (ports). A node with only outputs is a source,
    one with only inputs a sink. `run` is called for every record arriving on an input."""

    id: str
    title: str
    inputs: dict[str, str] = field(default_factory=dict)  # port → stream type
    outputs: dict[str, str] = field(default_factory=dict)
    run: NodeRun | None = None
    adds: list[str] = field(default_factory=list)  # annotating nodes: the fields they add
    module: str | None = None  # set by the module loader; None = the core


class Bus:
    """Nodes, wires and the recent records of every output."""

    def __init__(self, module_enabled: Callable[[str, str], bool] | None = None) -> None:
        """Initialize (module_enabled(domain_id, module_id): is a module on in a domain)."""
        self.nodes: dict[str, Node] = {}
        self._module_enabled = module_enabled or (lambda domain_id, module_id: True)
        self._recent: dict[tuple[str, str, str], deque[dict[str, Any]]] = {}

    def set_module_enabled(self, check: Callable[[str, str], bool]) -> None:
        """Set by the module loader."""
        self._module_enabled = check

    def add_node(self, node: Node) -> Callable[[], None]:
        """Register a node; returns the function that removes it again."""
        for port, kind in {**node.inputs, **node.outputs}.items():
            if kind not in STREAM_TYPES and "." not in kind:
                raise ValueError(f"Node {node.id}: unknown stream type {kind!r} on port {port!r}")
        self.nodes[node.id] = node

        def remove() -> None:
            self.nodes.pop(node.id, None)

        return remove

    def _on(self, node: Node, domain_id: str) -> bool:
        return node.module is None or self._module_enabled(domain_id, node.module)

    def wires(self, domain_id: str) -> list[tuple[str, str, str, str]]:
        """(from node, from port, to node, to port) in a domain: default wiring by type."""
        found = []
        for src in self.nodes.values():
            if not self._on(src, domain_id):
                continue
            for out_port, kind in src.outputs.items():
                for dst in self.nodes.values():
                    if dst is src or not self._on(dst, domain_id):
                        continue
                    found += [(src.id, out_port, dst.id, in_port) for in_port, k in dst.inputs.items() if k == kind]
        return found

    async def emit(self, domain_id: str, node_id: str, port: str, item: dict[str, Any]) -> list[Any]:
        """A node writes a record to one of its outputs: kept in RAM and handed to every input wired
        to it; returns what the readers' run gave back (e.g. the broker's run)."""
        node = self.nodes.get(node_id)
        if node is None or port not in node.outputs:
            raise ValueError(f"{node_id}.{port} is not an output")
        record = {"time": dt_util.utcnow().isoformat(), **item}
        recent = self._recent.setdefault((domain_id, node_id, port), deque(maxlen=KEEP_RECORDS))
        recent.append(record)
        results = []
        for src, out_port, dst, in_port in self.wires(domain_id):
            if (src, out_port) != (node_id, port) or (reader := self.nodes.get(dst)) is None or reader.run is None:
                continue
            try:
                results.append(await reader.run(domain_id, in_port, record))
            except Exception:  # noqa: BLE001
                LOGGER.exception("Node %s failed on %s.%s", dst, node_id, port)
        return results

    def recent(self, domain_id: str, node_id: str, port: str) -> list[dict[str, Any]]:
        """The output's last records (newest last, at most KEEP_RECORDS and KEEP_FOR old)."""
        cutoff = (dt_util.utcnow() - KEEP_FOR).isoformat()
        return [r for r in self._recent.get((domain_id, node_id, port), ()) if r["time"] >= cutoff]

    def summary(self, domain_id: str) -> dict[str, Any]:
        """Nodes and wires of a domain (for the panel and later the node view)."""
        return {
            "nodes": [{"id": n.id, "title": n.title, "module": n.module, "inputs": n.inputs, "outputs": n.outputs,
                       "on": self._on(n, domain_id)} for n in self.nodes.values()],
            "wires": [{"from": f"{a}.{b}", "to": f"{c}.{d}"} for a, b, c, d in self.wires(domain_id)],
        }
