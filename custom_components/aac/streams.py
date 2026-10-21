"""The stream bus (spec/streams.md): the core as a collection of typed streams.

Owner design (2026-10-21): like the calendar is a collection of the modules' entries, audio, video,
timers and requests to the broker are streams the modules write and read; "everything can be wired
into anything", node-based. The core only knows the stream types, keeps the last records of every
output in RAM and delivers each record to the inputs wired to it. Default wiring is by type: an input
gets every output of its type (except the node's own), in domains where the reading node's module is on.
"""

from __future__ import annotations

import asyncio
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
# annotate(source, pcm, record) → fields to add, {"drop": True}, {"split": [...]} or None (audio.segment)
Annotate = Callable[[Any, bytes, dict[str, Any]], Awaitable[dict[str, Any] | None]]
# listen(source, pcm) → records of its own (audio.pcm in windows, e.g. sound captions)
Listen = Callable[[Any, bytes], Awaitable[list[dict[str, Any]]]]


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
    # Annotating nodes (the same type in and out, spec "Annotating"): they work on a record in turn, lowest
    # `order` first, instead of each getting a copy. On audio.segment they get the raw audio (reads_pcm).
    order: int = 50
    annotate: Annotate | None = None
    partial: Callable[[Any, bytes], Awaitable[str | None]] | None = None  # the text so far (live view)
    # Nodes reading raw media (audio.pcm) get it in windows of `window` seconds through `listen`
    listen: Listen | None = None
    window: float = 1.0

    @property
    def annotating(self) -> bool:
        """Adds fields to the records of a type it reads and writes."""
        return bool(self.adds) and any(k in self.outputs.values() for k in self.inputs.values())


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

    def annotators(self, domain_id: str, kind: str) -> list[Node]:
        """The annotating nodes of a type that are on in a domain, in their order."""
        found = [n for n in self.nodes.values() if n.annotating and kind in n.inputs.values() and self._on(n, domain_id)]
        return sorted(found, key=lambda n: n.order)

    def media_readers(self, domain_id: str, kind: str) -> list[Node]:
        """Nodes that listen to raw media of a type (audio.pcm) and are on in a domain."""
        return [n for n in self.nodes.values() if n.listen and kind in n.inputs.values() and self._on(n, domain_id)]

    def readers(self, domain_id: str, kind: str) -> list[Node]:
        """Ordinary (not annotating) nodes with an input of a type that are on in a domain."""
        return [n for n in self.nodes.values() if not n.annotating and n.run and kind in n.inputs.values()
                and self._on(n, domain_id)]

    def wires(self, domain_id: str) -> list[tuple[str, str, str, str]]:
        """(from node, from port, to node, to port) in a domain: default wiring by type. A type with
        annotating nodes runs through them as a chain: writers → first → … → last → readers."""
        found = []
        for src in self.nodes.values():
            if not self._on(src, domain_id) or src.annotating:
                continue
            for out_port, kind in src.outputs.items():
                chain = self.annotators(domain_id, kind)
                if chain:
                    first = chain[0]
                    found.append((src.id, out_port, first.id, next(p for p, k in first.inputs.items() if k == kind)))
                    for a, b in zip(chain, chain[1:]):
                        found.append((a.id, next(p for p, k in a.outputs.items() if k == kind),
                                      b.id, next(p for p, k in b.inputs.items() if k == kind)))
                tail = chain[-1] if chain else src
                tail_port = next(p for p, k in tail.outputs.items() if k == kind) if chain else out_port
                for dst in self.nodes.values():
                    if dst is src or dst.annotating or not self._on(dst, domain_id):
                        continue
                    found += [(tail.id, tail_port, dst.id, in_port) for in_port, k in dst.inputs.items() if k == kind]
        return list(dict.fromkeys(found))

    async def emit(self, domain_id: str, node_id: str, port: str, item: dict[str, Any]) -> list[Any]:
        """A node writes a record to one of its outputs: kept in RAM and handed to every input wired
        to it; returns what the readers' run gave back (e.g. the broker's run)."""
        node = self.nodes.get(node_id)
        if node is None or port not in node.outputs:
            raise ValueError(f"{node_id}.{port} is not an output")
        # A record that has its time already is handed on as it is: readers may mark it (the wake word
        # sets "addressed" on a heard line, and the transcript shows it)
        record = item if "time" in item else {"time": dt_util.utcnow().isoformat(), **item}
        recent = self._recent.setdefault((domain_id, node_id, port), deque(maxlen=KEEP_RECORDS))
        recent.append(record)
        # Its readers: wired to this output, or (a type with annotating nodes) to the chain's last one;
        # whoever writes such a type has run the chain already (the audio hub with each segment)
        kind = node.outputs[port]
        chain = self.annotators(domain_id, kind)
        tail = (chain[-1].id, next(p for p, k in chain[-1].outputs.items() if k == kind)) if chain else (node_id, port)
        readers = []
        for src, out_port, dst, in_port in self.wires(domain_id):
            reader = self.nodes.get(dst)
            if (src, out_port) == tail and reader and reader.run and not reader.annotating and (reader, in_port) not in readers:
                readers.append((reader, in_port))

        async def deliver(reader: Node, in_port: str) -> Any:
            try:
                return await reader.run(domain_id, in_port, record)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Node %s failed on %s.%s", reader.id, node_id, port)
                return None

        # Readers work at the same time (the wake word answering must not hold up the companion)
        return list(await asyncio.gather(*(deliver(r, p) for r, p in readers)))

    def recent(self, domain_id: str, node_id: str, port: str) -> list[dict[str, Any]]:
        """The output's last records (newest last, at most KEEP_RECORDS and KEEP_FOR old)."""
        cutoff = (dt_util.utcnow() - KEEP_FOR).isoformat()
        return [r for r in self._recent.get((domain_id, node_id, port), ()) if r["time"] >= cutoff]

    def summary(self, domain_id: str) -> dict[str, Any]:
        """Nodes and wires of a domain (for the panel and later the node view)."""
        return {
            "nodes": [{"id": n.id, "title": n.title, "module": n.module, "inputs": n.inputs, "outputs": n.outputs,
                       "adds": n.adds, "on": self._on(n, domain_id)} for n in self.nodes.values()],
            "wires": [{"from": f"{a}.{b}", "to": f"{c}.{d}"} for a, b, c, d in self.wires(domain_id)],
        }
