"""How a context moves (docs/v2): from a card's output along the wires into the next cards.

A round output gives every wire a copy; on a square one the card picks the wire. While a card works on a turn it may
send snapshots of it (`Step.progress`): they pass every card's `progress()` (by default straight on), and the finished
turn follows through `handle()`. A card's tools are the cards wired
from its output and back into it (a loop): it calls their functions directly, the call and its result are written
into the context. What passed each output is kept for the monitor, and open panels hear about it (live wires).
"""

from __future__ import annotations

from collections.abc import Awaitable
import time
from typing import TYPE_CHECKING, Any

from .const import LOGGER
from .context import Context

if TYPE_CHECKING:
    from .cards import Card
    from .core import Domain

LEVEL_EVERY = 0.1  # seconds between an audio wire's loudness updates (only while a Nodes page is open)
KEEP_RECORDS, KEEP_SECONDS = 10, 600  # what passed an output lately (the monitor windows)
MAX_STEPS = 80  # a context that visits more cards than this is stopped (the wiring loops)


class Step:
    """What a card sees while it works on a context."""

    def __init__(self, flow: Flow, card: Card, port: str, context: Context) -> None:
        """Initialize."""
        self.flow, self.card, self.port, self.context = flow, card, port, context

    def targets(self, port: str = "out") -> list[tuple[Card, str]]:
        """The cards (and their input) wired to one of this card's outputs."""
        return self.flow.wired_from(self.card.id, port)

    def tool_cards(self) -> list[Card]:
        """Its tools (Flow.tools)."""
        return self.flow.tools(self.card.id)

    def attached(self) -> list[Card]:
        """Cards wired from its outputs straight back into it: they only attach a tool (nothing else goes there)."""
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

    def read(self, port: str) -> list[Context]:
        """What the dotted wires into one of this card's inputs hold now (reading never starts anything)."""
        return [m for c, p in self.flow.wired_into(self.card.id, port) if (m := c.held(p)) is not None]

    def progress(self, picks: list[Any]) -> Awaitable[None]:
        """A snapshot of this context (still being worked on), taken now, for the card's outputs (picks as handle
        returns): await what it returns to send it."""
        snap = self.context.branch()
        snap.progress = True
        return self.flow._out(self.card, snap, picks)

    def live(self, text: str) -> None:
        """Text that is still growing (a model writing): open panels show it."""
        self.flow.domain.core.notify("delta", domain=self.flow.domain.id, chat_id=self.context.chat_id,
                                     card=self.context.origin, text=text)


class Flow:
    """The context flow of one domain."""

    def __init__(self, domain: Domain) -> None:
        """Initialize."""
        self.domain = domain
        self.last: dict[str, dict[str, Any]] = {}  # "card.port" → the newest context that left there
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.held: dict[str, Context] = {}  # "card.port" → a copy of the newest context that left there (dotted wires read it)  # "card.port" → what passed lately: {time, text}
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

    def tools(self, card_id: str) -> list[Card]:
        """A card's tools: the cards in its loop (on a way from its outputs back into its inputs, straight or through
        other cards) that offer functions, unless their "offers its functions" tick is off."""
        wires = self.domain.wires

        def reach(forward: bool) -> set[str]:
            seen, todo = set(), [card_id]
            while todo:
                at = todo.pop()
                for w in wires:
                    a, b = w["from"].split(".")[0], w["to"].split(".")[0]
                    nxt = b if forward and a == at else a if not forward and b == at else None
                    if nxt and nxt != card_id and nxt not in seen:
                        seen.add(nxt)
                        todo.append(nxt)
            return seen
        cards = self.domain.cards
        return [cards[i] for i in sorted(reach(True) & reach(False))
                if i in cards and cards[i].functions and cards[i].settings.get("offers", cards[i].offers)]

    def wired_into(self, card_id: str, port: str) -> list[tuple[Card, str]]:
        """Sources of an input."""
        found = []
        for w in self.domain.wires:
            a, ap = w["from"].split(".", 1)
            b, bp = w["to"].split(".", 1)
            if b == card_id and bp == port and (card := self.domain.cards.get(a)):
                found.append((card, ap))
        return found

    def record(self, card_id: str, port: str, context: Context, to: list[str] | None = None) -> None:
        """What left an output: kept for the monitor; open panels pulse the wire."""
        key = f"{card_id}.{port}"
        self.last[key] = {**context.summary(), "time": time.time()}
        self.held[key] = context.branch()
        newest = next((e for e in reversed(context.entries) if e["kind"] not in ("system", "signal") and e["text"].strip()), None)
        self._keep(key, newest["text"][:400] if newest else "")
        self.domain.core.notify("flow", domain=self.domain.id, wire=key, to=to)  # to: the inputs it went to (a square output picks)

    def _keep(self, key: str, text: str) -> None:
        now = time.time()
        kept = [r for r in self.records.get(key, []) if now - r["time"] < KEEP_SECONDS]
        self.records[key] = [*kept, {"time": now, "text": text}][-KEEP_RECORDS:]

    def stream(self, card_id: str, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """Live audio on an output: handed at once to every wired card that takes a stream (no records). Open panels
        get its loudness a few times a second (the wire's thickness)."""
        now = time.monotonic()
        key = f"{card_id}.{port}"
        if self.domain.core.nodes_open and now - self._level_at.get(key, 0) >= LEVEL_EVERY:
            self._level_at[key] = now
            from .audio import level  # noqa: PLC0415
            self.domain.core.notify("level", domain=self.domain.id, wire=key, level=level(chunk))
        for target, in_port in self.wired_from(card_id, port):
            if hasattr(target, "stream"):
                try:
                    target.stream(in_port, chunk, meta)
                except Exception:  # noqa: BLE001 - one card must not stop the audio
                    LOGGER.exception("AAC: %s failed on audio", target.id)

    def clip(self, card_id: str, port: str, clip: dict[str, Any]) -> None:
        """An audio clip (e.g. an Audio clipper's stretch of speech): to every wired card that takes clips, in the background."""
        what = "stop" if clip.get("stop") else f"{clip.get('seconds')} s of audio"
        self.last[f"{card_id}.{port}"] = {"clip": what, "source": clip.get("name"), "time": time.time()}
        self._keep(f"{card_id}.{port}", f"{what}{f' · {t}' if (t := clip.get('text')) else ''} ({clip.get('name') or clip.get('source') or 'any'})")
        self.domain.core.notify("flow", domain=self.domain.id, wire=f"{card_id}.{port}")
        for target, in_port in self.wired_from(card_id, port):
            if hasattr(target, "clip"):
                self.domain.hass.async_create_background_task(target.clip(in_port, clip), f"aac {target.id} clip")

    async def send(self, context: Context, card_id: str, port: str = "out") -> None:
        """A card sends a context from one of its outputs."""
        await self._out(self.domain.cards[card_id], context, [port])

    async def enter(self, card: Card, port: str, context: Context) -> None:
        """A context arrives at a card's input."""
        context.way.append(card.id)
        if len(context.way) > MAX_STEPS:
            LOGGER.warning("AAC: a context visited %s cards; the wiring may loop (stopped at %s)", MAX_STEPS, card.id)
            return
        if context.progress:  # a snapshot: cards that show or speak as things come answer it, the others pass it on
            try:
                result = await card.progress(context, port)
            except Exception:  # noqa: BLE001
                LOGGER.exception("AAC: card %s failed on a snapshot", card.id)
                return
            await self._out(card, context, result)
            return
        if context.stopped:  # a stop runs forward: a busy card cuts and sends its own context on, the stop ends there
            if await card.stop(context.entries[-1]):
                return
            await self._out(card, context, None)
            return
        try:
            result = await card.handle(context, port, Step(self, card, port, context))
        except Exception:  # noqa: BLE001 - one broken card must not stop the domain
            LOGGER.exception("AAC: card %s failed", card.id)
            return
        await self._out(card, context, result)

    async def _out(self, card: Card, context: Context, result: list[Any] | None) -> None:
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
                self.record(card.id, port, context, [f"{c.id}.{p}" for c, p in targets])
            sends += targets
        for i, (target, in_port) in enumerate(sends):
            await self.enter(target, in_port, context if i == len(sends) - 1 else context.branch())
