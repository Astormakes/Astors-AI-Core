"""How a message moves (docs/v2): from a card's output along the wires into the next cards.

A round output gives every wire a copy; on a square one the card picks the wire. A card's tools are the cards wired
from its output and back into it (a loop): it calls their functions directly, the call and its result are written
into the message. What passed each output is kept for the monitor, and open panels hear about it (live wires).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .const import LOGGER
from .message import Message

if TYPE_CHECKING:
    from .cards import Card
    from .core import Domain

LEVEL_EVERY = 0.25  # seconds between an audio wire's loudness updates
KEEP_RECORDS, KEEP_SECONDS = 10, 600  # what passed an output lately (the monitor windows)
MAX_STEPS = 80  # a message that visits more cards than this is stopped (the wiring loops)


class Step:
    """What a card sees while it works on a message."""

    def __init__(self, flow: Flow, card: Card, port: str, message: Message) -> None:
        """Initialize."""
        self.flow, self.card, self.port, self.message = flow, card, port, message

    def targets(self, port: str = "out") -> list[tuple[Card, str]]:
        """The cards (and their input) wired to one of this card's outputs."""
        return self.flow.wired_from(self.card.id, port)

    def tool_cards(self) -> list[Card]:
        """Cards wired from this card's outputs and back into it: its tools."""
        mine = self.card.id
        back = {w["from"].split(".")[0] for w in self.flow.domain.wires if w["to"].split(".")[0] == mine}
        found: dict[str, Card] = {}
        for port in self.card.ports()[1]:
            for target, _ in self.targets(port):
                if target.id in back and target.id != mine:
                    found[target.id] = target
        return list(found.values())

    def wired_into(self, port: str) -> list[Card]:
        """The cards wired into one of this card's inputs (e.g. the Pool on a Model's pool input)."""
        return [c for c, _ in self.flow.wired_into(self.card.id, port)]

    def live(self, text: str) -> None:
        """Text that is still growing (a model writing): open panels show it."""
        self.flow.domain.core.notify("delta", domain=self.flow.domain.id, chat_id=self.message.chat_id,
                                     card=self.message.origin, text=text)


class Flow:
    """The message flow of one domain."""

    def __init__(self, domain: Domain) -> None:
        """Initialize."""
        self.domain = domain
        self.last: dict[str, dict[str, Any]] = {}  # "card.port" → the newest message that left there
        self.records: dict[str, list[dict[str, Any]]] = {}  # "card.port" → what passed lately: {time, text}
        self._level_at: dict[str, float] = {}

    def wired_from(self, card_id: str, port: str) -> list[tuple[Card, str]]:
        """Targets of an output."""
        found = []
        for w in self.domain.wires:
            a, ap = w["from"].split(".", 1)
            b, bp = w["to"].split(".", 1)
            if a == card_id and ap == port and (card := self.domain.cards.get(b)):
                found.append((card, bp))
        return found

    def wired_into(self, card_id: str, port: str) -> list[tuple[Card, str]]:
        """Sources of an input."""
        found = []
        for w in self.domain.wires:
            a, ap = w["from"].split(".", 1)
            b, bp = w["to"].split(".", 1)
            if b == card_id and bp == port and (card := self.domain.cards.get(a)):
                found.append((card, ap))
        return found

    def record(self, card_id: str, port: str, message: Message) -> None:
        """What left an output: kept for the monitor; open panels pulse the wire."""
        key = f"{card_id}.{port}"
        self.last[key] = {**message.summary(), "time": time.time()}
        newest = next((e for e in reversed(message.entries) if e["kind"] not in ("system", "signal") and e["text"].strip()), None)
        self._keep(key, newest["text"][:400] if newest else "")
        self.domain.core.notify("flow", domain=self.domain.id, wire=f"{card_id}.{port}")

    def _keep(self, key: str, text: str) -> None:
        now = time.time()
        kept = [r for r in self.records.get(key, []) if now - r["time"] < KEEP_SECONDS]
        self.records[key] = [*kept, {"time": now, "text": text}][-KEEP_RECORDS:]

    def stream(self, card_id: str, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """Live audio on an output: handed at once to every wired card that takes a stream (no records). Open panels
        get its loudness a few times a second (the wire's thickness)."""
        now = time.monotonic()
        key = f"{card_id}.{port}"
        if now - self._level_at.get(key, 0) >= LEVEL_EVERY:
            self._level_at[key] = now
            from .audio import level  # noqa: PLC0415
            self.domain.core.notify("level", domain=self.domain.id, wire=key, level=level(chunk))
        for target, in_port in self.wired_from(card_id, port):
            if hasattr(target, "stream"):
                try:
                    target.stream(in_port, chunk, meta)
                except Exception:  # noqa: BLE001 - one card must not stop the audio
                    LOGGER.exception("AAC: %s failed on audio", target.id)

    def piece(self, card_id: str, port: str, piece: dict[str, Any]) -> None:
        """A piece of speech (a Pause cut's): to every wired card that takes pieces, in the background."""
        self.last[f"{card_id}.{port}"] = {"piece": f"{piece.get('seconds')} s of speech", "source": piece.get("name"), "time": time.time()}
        self._keep(f"{card_id}.{port}", f"{piece.get('seconds')} s of speech from {piece.get('name') or piece.get('source')}")
        self.domain.core.notify("flow", domain=self.domain.id, wire=f"{card_id}.{port}")
        for target, in_port in self.wired_from(card_id, port):
            if hasattr(target, "piece"):
                self.domain.hass.async_create_background_task(target.piece(in_port, piece), f"aac {target.id} piece")

    async def send(self, message: Message, card_id: str, port: str = "out") -> None:
        """A card sends a message from one of its outputs."""
        await self._out(self.domain.cards[card_id], message, [port])

    async def enter(self, card: Card, port: str, message: Message) -> None:
        """A message arrives at a card's input."""
        message.way.append(card.id)
        if len(message.way) > MAX_STEPS:
            LOGGER.warning("AAC: a message visited %s cards; the wiring may loop (stopped at %s)", MAX_STEPS, card.id)
            return
        if message.stopped:
            await card.stop()
        try:
            result = await card.handle(message, port, Step(self, card, port, message))
        except Exception:  # noqa: BLE001 - one broken card must not stop the domain
            LOGGER.exception("AAC: card %s failed", card.id)
            return
        await self._out(card, message, result)

    async def _out(self, card: Card, message: Message, result: list[Any] | None) -> None:
        _, outs, square = card.ports()
        if result is None:
            picks = [p for p, kind in outs.items() if kind == "text" and p not in square]
        else:
            picks = result
        sends: list[tuple[Card, str]] = []
        for pick in picks:
            port, only = pick if isinstance(pick, tuple) else (pick, None)
            targets = [(c, p) for c, p in self.wired_from(card.id, port) if only is None or c.id == only]
            if targets:
                self.record(card.id, port, message)
            sends += targets
        for i, (target, in_port) in enumerate(sends):
            await self.enter(target, in_port, message if i == len(sends) - 1 else message.branch())
