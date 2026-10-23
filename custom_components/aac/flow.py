"""How a message moves (docs/v2): from a card's output along the wires into the next cards.

A round output gives every wire a copy; on a square one the card picks the wire. A card's tools are the cards wired
from its output and back into it (a loop): it calls their functions directly, the call and its result are written
into the message. What passed each output is kept for the monitor, and open panels hear about it (live wires).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import LOGGER
from .message import Message

if TYPE_CHECKING:
    from .cards import Card
    from .core import Domain

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
        self.last[f"{card_id}.{port}"] = message.summary()
        self.domain.core.notify("flow", domain=self.domain.id, wire=f"{card_id}.{port}")

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
