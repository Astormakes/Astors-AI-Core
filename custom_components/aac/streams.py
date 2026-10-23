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
    # v1 (owner, 2026-10-22): a request and an answer are the same thing, a message (request.py): sources write it,
    # transforms add to it, a model answers it (its answer is the message's new text), Reply brings it home
    "message": "record",
    # A card attached to a model (the dotted line): its tools, maybe context; the model's menu is its attached cards
    "tool": "link",
    "say": "record",
    "notify": "record",
    "video.frame": "continuous",
    "vision.detection": "record",
}

KEEP_RECORDS = 200  # per output and domain
KEEP_FOR = timedelta(minutes=10)

# run(domain_id, port, item) → anything (handed back to the writer by emit)
NodeRun = Callable[[str, str, dict[str, Any]], Awaitable[Any]]
# annotate(...): what an annotating node does to a record; its arguments depend on the type:
# audio.segment: annotate(source, pcm, line) → fields to add, {"drop": True}, {"split": [...]} or None
# message: annotate(message) → adds to the message in place (request.py)
Annotate = Callable[..., Awaitable[Any]]
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
    # Node types vs. instances (spec/v1.md): a type with `make` may be added to a domain more than once; each
    # instance has its own id, title and settings (`settings_schema` like a manifest's settings) and is wired
    # by hand (the automatic wiring leaves instances out)
    make: Callable[[str, str, dict[str, Any]], Node] | None = None
    settings_schema: dict[str, Any] = field(default_factory=dict)
    settings: dict[str, Any] = field(default_factory=dict)
    type: str | None = None  # an instance: the type it was made from
    # Its part in the message graph (flow.py): source · transform · model · decide · reply · sink · tool
    role: str | None = None
    # What it does, in plain words: shown on the card and in Add node, and what AAC reads when it builds a graph
    # itself; every node brings one (spec/module.md)
    description: str = ""
    # A core card's own settings, edited in the node view: (schema, get(domain_id) → values, async set(domain_id, values))
    edit: tuple[dict[str, Any], Callable[[str], dict[str, Any]], Callable[[str, dict[str, Any]], Awaitable[None]]] | None = None
    # A tool card: provide(message) adds its tool set (and maybe context) when the model it is attached to answers
    provide: Callable[[Any], Awaitable[None]] | None = None
    template: bool = False  # a type that exists only as added instances (not as a node of its own)

    @property
    def annotating(self) -> bool:
        """Adds fields to the records of a type it reads and writes."""
        return bool(self.adds) and any(k in self.outputs.values() for k in self.inputs.values())


class Bus:
    """Nodes, wires and the recent records of every output."""

    def __init__(self, module_enabled: Callable[[str, str], bool] | None = None) -> None:
        """Initialize (module_enabled(domain_id, module_id): is a module on in a domain)."""
        self.nodes: dict[str, Node] = {}
        self.templates: dict[str, Node] = {}  # node types that exist only when the owner adds one
        self._module_enabled = module_enabled or (lambda domain_id, module_id: True)
        self._recent: dict[tuple[str, str, str], deque[dict[str, Any]]] = {}
        # The owner's own wires per domain: fn(domain_id) → [{"from": "a.p", "to": "b.q"}] or None (automatic)
        self._stored: Callable[[str], list[dict[str, str]] | None] = lambda domain_id: None
        # Added node instances per domain: fn(domain_id) → {instance id: {"type", "title", "settings"}}
        self._instances: Callable[[str], dict[str, dict[str, Any]]] = lambda domain_id: {}

    def set_instances(self, instances: Callable[[str], dict[str, dict[str, Any]]]) -> None:
        """Where a domain's added node instances come from (the store)."""
        self._instances = instances

    def nodes_in(self, domain_id: str) -> dict[str, Node]:
        """Every node of a domain: the registered ones and the domain's added instances."""
        found = dict(self.nodes)
        for inst_id, spec in (self._instances(domain_id) or {}).items():
            kind = self.nodes.get(spec.get("type", "")) or self.templates.get(spec.get("type", ""))
            if kind and kind.make and inst_id not in found:
                node = kind.make(inst_id, spec.get("title") or kind.title, dict(spec.get("settings") or {}))
                node.type, node.module = kind.id, kind.module
                node.description = node.description or kind.description
                found[inst_id] = node
        return found

    def set_wiring(self, stored: Callable[[str], list[dict[str, str]] | None]) -> None:
        """Where the owner's wires come from (the store)."""
        self._stored = stored

    def wired_by_hand(self, domain_id: str) -> bool:
        """Has the owner wired this domain himself?"""
        return self._stored(domain_id) is not None

    def set_module_enabled(self, check: Callable[[str, str], bool]) -> None:
        """Set by the module loader."""
        self._module_enabled = check

    def add_node(self, node: Node) -> Callable[[], None]:
        """Register a node; returns the function that removes it again."""
        for port, kind in {**node.inputs, **node.outputs}.items():
            if kind not in STREAM_TYPES and "." not in kind:
                raise ValueError(f"Node {node.id}: unknown stream type {kind!r} on port {port!r}")
        (self.templates if node.template else self.nodes)[node.id] = node

        def remove() -> None:
            (self.templates if node.template else self.nodes).pop(node.id, None)

        return remove

    def _on(self, node: Node, domain_id: str) -> bool:
        return node.module is None or self._module_enabled(domain_id, node.module)

    def annotators(self, domain_id: str, kind: str) -> list[Node]:
        """The annotating nodes of a type that are on in a domain, in their order."""
        found = [n for n in self.nodes_in(domain_id).values() if n.annotating and kind in n.inputs.values() and self._on(n, domain_id)]
        return sorted(found, key=lambda n: n.order)

    def chain(self, domain_id: str, node_id: str, port: str) -> tuple[list[Node], tuple[str, str]]:
        """The annotating nodes a record from an output runs through, by following the wires (automatic:
        by their order; wired by hand: as the owner wired them), and the output at the end of the chain."""
        nodes = self.nodes_in(domain_id)
        kind = nodes[node_id].outputs[port]
        wires = self.wires(domain_id)
        chain: list[Node] = []
        at = (node_id, port)
        while True:
            nxt = next((nodes[b] for a, ap, b, bp in wires if (a, ap) == at and nodes[b].annotating
                        and nodes[b].inputs.get(bp) == kind and nodes[b] not in chain), None)
            if nxt is None:
                return chain, at
            chain.append(nxt)
            at = (nxt.id, next(p for p, k in nxt.outputs.items() if k == kind))

    def wired_from(self, domain_id: str, node_id: str, port: str, listening: bool = False) -> list[tuple[Node, str]]:
        """The ordinary nodes (and their input) wired to an output; listening: those that read raw media."""
        found, nodes = [], self.nodes_in(domain_id)
        for a, ap, b, bp in self.wires(domain_id):
            reader = nodes[b]
            if (a, ap) == (node_id, port) and not reader.annotating and (reader.listen if listening else reader.run) \
                    and (reader, bp) not in found:
                found.append((reader, bp))
        return found

    def wires(self, domain_id: str) -> list[tuple[str, str, str, str]]:
        """(from node, from port, to node, to port) in a domain: the owner's wires (of nodes that exist and are
        on, with matching types), or the automatic ones."""
        stored = self._stored(domain_id)
        if stored is None:
            return self.default_wires(domain_id)
        found, nodes = [], self.nodes_in(domain_id)
        for wire in stored:
            (a, _, ap), (b, _, bp) = wire["from"].partition("."), wire["to"].partition(".")
            src, dst = nodes.get(a), nodes.get(b)
            if src and dst and self._on(src, domain_id) and self._on(dst, domain_id) \
                    and src.outputs.get(ap) and src.outputs.get(ap) == dst.inputs.get(bp):
                found.append((a, ap, b, bp))
        return list(dict.fromkeys(found))

    def default_wires(self, domain_id: str) -> list[tuple[str, str, str, str]]:
        """The automatic wiring by type. A type with annotating nodes runs through them as a chain in their
        order: writers → first → … → last → readers. Messages by role: every source through the transforms into
        the model, the model into Reply and the sinks; every tool card onto the model."""
        found = self._message_wires(domain_id)  # added instances are wired by hand
        for src in self.nodes.values():
            if not self._on(src, domain_id) or src.annotating or src.role:
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

    def _message_wires(self, domain_id: str) -> list[tuple[str, str, str, str]]:
        on = [n for n in self.nodes.values() if n.role and self._on(n, domain_id)]

        def port(node: Node, kind: str, side: str) -> str | None:
            return next((p for p, k in (node.outputs if side == "out" else node.inputs).items() if k == kind), None)

        found = []
        models = [n for n in on if n.role == "model"]
        transforms = sorted((n for n in on if n.role == "transform"), key=lambda n: n.order)
        for model in models[:1]:  # the standard model (added ones are wired by hand)
            for src in (n for n in on if n.role == "source"):
                at = (src.id, port(src, "message", "out"))
                for t in transforms:
                    found.append((*at, t.id, port(t, "message", "in")))
                    at = (t.id, port(t, "message", "out"))
                found.append((*at, model.id, port(model, "message", "in")))
            for end in (n for n in on if n.role in ("reply", "sink")):
                found.append((model.id, port(model, "message", "out"), end.id, port(end, "message", "in")))
            for card in (n for n in on if n.role == "tool"):
                found.append((card.id, port(card, "tool", "out"), model.id, port(model, "tool", "in")))
        return list(dict.fromkeys(w for w in found if None not in w))

    async def emit(self, domain_id: str, node_id: str, port: str, item: dict[str, Any]) -> list[Any]:
        """A node writes a record to one of its outputs: kept in RAM and handed to every input wired
        to it; returns what the readers' run gave back (e.g. the broker's run)."""
        node = self.nodes_in(domain_id).get(node_id)
        if node is None or port not in node.outputs:
            raise ValueError(f"{node_id}.{port} is not an output")
        # A record that has its time already is handed on as it is: readers may mark it (the wake word
        # sets "addressed" on a heard line, and the transcript shows it)
        record = item if "time" in item else {"time": dt_util.utcnow().isoformat(), **item}
        recent = self._recent.setdefault((domain_id, node_id, port), deque(maxlen=KEEP_RECORDS))
        recent.append(record)
        # Its readers: wired to this output, or (through annotating nodes) to the end of its chain;
        # whoever writes such a type has run the chain already (the audio hub with each segment)
        readers = self.wired_from(domain_id, *self.chain(domain_id, node_id, port)[1])

        async def deliver(reader: Node, in_port: str) -> Any:
            try:
                return await reader.run(domain_id, in_port, record)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Node %s failed on %s.%s", reader.id, node_id, port)
                return None

        # Readers work at the same time (the wake word answering must not hold up the companion)
        return list(await asyncio.gather(*(deliver(r, p) for r, p in readers)))

    def record(self, domain_id: str, node_id: str, port: str, record: dict[str, Any]) -> None:
        """Keep a record of what left an output (the flow's messages; the node view shows the counts)."""
        self._recent.setdefault((domain_id, node_id, port), deque(maxlen=KEEP_RECORDS)).append(
            {"time": dt_util.utcnow().isoformat(), **record})

    def recent(self, domain_id: str, node_id: str, port: str) -> list[dict[str, Any]]:
        """The output's last records (newest last, at most KEEP_RECORDS and KEEP_FOR old)."""
        cutoff = (dt_util.utcnow() - KEEP_FOR).isoformat()
        return [r for r in self._recent.get((domain_id, node_id, port), ()) if r["time"] >= cutoff]

    def summary(self, domain_id: str) -> dict[str, Any]:
        """Nodes and wires of a domain (for the panel and later the node view)."""
        return {
            "nodes": [{"id": n.id, "title": n.title, "module": n.module, "inputs": n.inputs, "outputs": n.outputs,
                       "adds": n.adds, "order": n.order if n.annotating else None, "on": self._on(n, domain_id),
                       "role": n.role, "description": n.description,
                       **({"edit_schema": n.edit[0], "edit_values": n.edit[1](domain_id)} if n.edit else {}),
                       "kind": "annotating" if n.annotating else "source" if not n.inputs else "sink" if not n.outputs
                       else "transform",
                       # how many records each output carried in the last minutes (the view shows the flow)
                       "recent": {p: len(self.recent(domain_id, n.id, p)) for p in n.outputs},
                       "type": n.type, "settings": n.settings,
                       "settings_schema": (self.nodes.get(n.type or n.id) or self.templates.get(n.type or "") or n).settings_schema,
                       "addable": bool(n.make)}
                      for n in self.nodes_in(domain_id).values()],
            "wires": [{"from": f"{a}.{b}", "to": f"{c}.{d}"} for a, b, c, d in self.wires(domain_id)],
            # What "Add node" offers: node types that can be added (more than once), templates included
            "types": [{"id": n.id, "title": n.title, "kind": "annotating" if n.annotating else "node", "adds": n.adds,
                       "module": n.module, "settings_schema": n.settings_schema, "description": n.description, "role": n.role}
                      for n in [*self.nodes.values(), *self.templates.values()] if n.make and self._on(n, domain_id)],
        }
