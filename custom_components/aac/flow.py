"""How a context moves (docs/v2 page 5): a card sends it from one of its outputs; every card wired there gets a copy.

One way to arrive: a context comes in at an input and the card runs. A card acts when the context holds something
for it, and sends only when it did something (`Card.send`); nothing to do, nothing sent: the context ends there.
Contexts move as a wave: what a card sends reaches every card wired there before anything those cards send moves on
(a copy sent earlier never arrives after what came of it). A snapshot (something streaming) goes as a wave of its own,
at once.
The one thing every card does the same: a card with functions answers the call that names it (`Context.next_call`)
with a result entry and sends the context on from its outputs. One limit: a wire that carries more than the storm
limit (AAC's settings) within a second turns red. What passes a card's wires goes into AAC's log as its Log setting
says; open panels hear about it (live wires).
"""

from __future__ import annotations

import asyncio
from collections import deque
from contextvars import ContextVar
import json
import time
from traceback import format_exc
from typing import TYPE_CHECKING, Any

from .context import Context

if TYPE_CHECKING:
    from .cards import Card
    from .core import Domain

class Wave:
    """The contexts still to arrive, in the order they were sent."""

    def __init__(self) -> None:
        self.queue: deque[tuple[Card, str, Context]] = deque()
        self.moving = True


WAVE: ContextVar[Wave | None] = ContextVar("aac_wave", default=None)  # the wave a card works in (its sends join it)
LEVEL_EVERY = 0.1  # seconds between an audio wire's loudness updates (only while a Cards page is open)
STORM_SECONDS = 1.0


class Flow:
    """The context flow of one domain."""

    def __init__(self, domain: Domain) -> None:
        """Initialize."""
        self.domain = domain
        self.last: dict[str, dict[str, Any]] = {}  # "card.port" → the newest context that left there (the monitor)
        self.held: dict[str, Context] = {}  # "card.port" → a copy of the newest context that left there (dotted wires)
        self._level_at: dict[str, float] = {}
        self._through: dict[tuple[str, str], deque[float]] = {}  # wire → when contexts went through it lately

    # ------------------------------------------------------------------ the wires
    def passes(self, a: str, b: str) -> bool:
        """A context may go through the wire a → b (`card.port` each): not when it is red; it turns red when more than
        the storm limit went through it within a second (the wiring loops)."""
        if self.domain.is_red(a, b):
            return False
        storm = self.domain.core.storm
        now, seen = time.monotonic(), self._through.setdefault((a, b), deque())
        seen.append(now)
        while seen and now - seen[0] > STORM_SECONDS:
            seen.popleft()
        if len(seen) > storm:
            seen.clear()
            self.domain.redden(a, b, storm, STORM_SECONDS)
            return False
        return True

    def wired_from(self, card_id: str, port: str) -> list[tuple[Card, str]]:
        """The cards (and their input) wired to an output."""
        found = []
        for w in self.domain.wires:
            a, ap = w["from"].split(".", 1)
            b, bp = w["to"].split(".", 1)
            if a == card_id and ap == port and (card := self.domain.cards.get(b)):
                found.append((card, bp))
        return found

    def wired_into(self, card_id: str, port: str) -> list[tuple[Card, str]]:
        """The cards (and their output) wired into an input."""
        found = []
        for w in self.domain.wires:
            a, ap = w["from"].split(".", 1)
            b, bp = w["to"].split(".", 1)
            if b == card_id and bp == port and (card := self.domain.cards.get(a)):
                found.append((card, ap))
        return found

    def read(self, card_id: str, port: str) -> list[Context]:
        """What the dotted wires into an input hold now (reading never starts anything)."""
        return [m for c, p in self.wired_into(card_id, port) if (m := c.held(p)) is not None]

    def _text_wires(self) -> list[tuple[str, str, str]]:
        """The wires that carry contexts, card to card (dotted and audio wires do not)."""
        found = []
        for w in self.domain.wires:
            a, ap = w["from"].split(".", 1)
            b = w["to"].split(".", 1)[0]
            if (card := self.domain.cards.get(a)) and card.ports()[1].get(ap) == "text" and b in self.domain.cards:
                found.append((a, ap, b))
        return found

    def reach(self, card_id: str, ports: list[str]) -> set[str]:
        """The cards a context can reach from some of a card's outputs, straight or through other cards (not on through
        the card itself: what it sends from its other outputs is not this)."""
        wires = self._text_wires()
        seen = {b for a, ap, b in wires if a == card_id and ap in ports}
        todo = [b for b in seen if b != card_id]
        while todo:
            at = todo.pop()
            for a, _, b in wires:
                if a == at and b not in seen:
                    seen.add(b)
                    if b != card_id:
                        todo.append(b)
        return seen

    def tools(self, card_id: str) -> list[Card]:
        """A card's tools: the cards with functions it reaches from the outputs its calls leave by (Card.calls_from;
        empty: all of them) that reach it again (for the result)."""
        card = self.domain.cards[card_id]
        ports = list(card.calls_from) or [p for p, kind in card.ports()[1].items() if kind == "text"]
        found = []
        for c in sorted(self.reach(card_id, ports) - {card_id}):
            other = self.domain.cards[c]
            outs = [p for p, kind in other.ports()[1].items() if kind == "text"]
            if other.offered() and card_id in self.reach(c, outs):
                found.append(other)
        return found

    # ------------------------------------------------------------------ moving
    async def send(self, card: Card, context: Context, port: str) -> None:
        """A card sends a context from one of its outputs: every card wired there gets a copy (a red wire carries
        nothing). Within a wave it joins the wave; otherwise (or a snapshot) it starts one and returns when it ended."""
        targets = [(c, p) for c, p in self.wired_from(card.id, port) if self.passes(f"{card.id}.{port}", f"{c.id}.{p}")]
        if not targets:
            return
        self.record(card.id, port, context, [f"{c.id}.{p}" for c, p in targets])
        arriving = [(c, p, context.copy()) for c, p in targets]
        wave = WAVE.get()
        if wave is not None and wave.moving and not context.streaming:
            wave.queue.extend(arriving)
            return
        wave = Wave()
        wave.queue.extend(arriving)
        token = WAVE.set(wave)
        try:
            while wave.queue:
                await self.enter(*wave.queue.popleft())
        finally:
            wave.moving = False
            WAVE.reset(token)

    async def enter(self, card: Card, port: str, context: Context) -> None:
        """A context arrives at a card's input."""
        await asyncio.sleep(0)  # Home Assistant gets its turn between two cards (a storm cannot freeze it)
        if not context.streaming:
            self._log(card.id, "in", port, self.newest(context), context)
        call = context.next_call()
        if call is not None and call[1]["card"] == card.id:  # a call that names it: answered, sent on
            await self._answer(card, context, call[1])
            return
        try:
            await card.handle(context, port)
        except Exception as err:  # noqa: BLE001 - one broken card must not stop the domain
            self.failed(card.id, "", err)

    async def _answer(self, card: Card, context: Context, call: dict[str, Any]) -> None:
        """Run the called function, add its result, send the context on from the card's outputs."""
        try:
            result = await card.call(call["function"], call.get("args") or {}, context)
        except Exception as err:  # noqa: BLE001 - a failing tool is told to the caller
            result = {"error": str(err)}
            self.domain.log.write("warning", f"Its function {call['function']} failed: {err!r}", card=card.id, what="function")
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        context.add(card.tag("result"), text[:8000], call_id=call["id"], name=call["name"])
        for port, kind in card.ports()[1].items():
            if kind == "text":
                await self.send(card, context, port)

    # ------------------------------------------------------------------ what it writes down
    def record(self, card_id: str, port: str, context: Context, to: list[str] | None = None) -> None:
        """What left an output: kept for the monitor and dotted wires; open panels pulse the wire."""
        key = f"{card_id}.{port}"
        self.last[key] = {**context.summary(), "time": time.time()}
        self.held[key] = context.copy()
        if not context.streaming:  # snapshots light the wire, the finished context is written down
            self._log(card_id, "out", port, self.newest(context), context, to=to)
        self.domain.core.notify("flow", domain=self.domain.id, wire=key, to=to)

    @staticmethod
    def newest(context: Context) -> str:
        """What is new in a context: its newest entry with text (else its calls, else its tag)."""
        for e in reversed(context.conversation):
            if e["text"].strip():
                return e["text"]
            if calls := e["data"].get("calls"):
                return ", ".join(f"{c['name']}(…)" for c in calls)
        return f"#{context.newest['tag']}" if context.newest else ""

    def _log(self, card_id: str, at: str, port: str, text: str, context: Context | None = None, **extra: Any) -> None:
        """What passed one of a card's ports, as its Log setting says: nothing, the changed text, or with the whole context
        (kept a few minutes); what comes in, goes out, or both."""
        card = self.domain.cards.get(card_id)
        what, when = (card.settings.get("log"), card.settings.get("log_at")) if card else ("text", "out")
        if what == "nothing" or when not in (at, "both"):
            return
        whole = {**context.summary(), "time": time.time()} if context is not None and what == "context" else None
        self.domain.log.write("flow", text or "(no text)", card=card_id, what=at, port=port, context=whole, **extra)

    def failed(self, card_id: str, doing: str, err: Exception) -> None:
        """A card failed: in the log with where it happened."""
        self.domain.log.write("error", f"Failed{f' {doing}' if doing else ''}: {err!r}", card=card_id, what="failed", trace=format_exc()[-3000:])

    # ------------------------------------------------------------------ audio
    def stream(self, card_id: str, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """Live audio on an output: handed at once to every wired card that takes a stream (no records). Open panels
        get its loudness a few times a second (the wire's thickness)."""
        now = time.monotonic()
        key = f"{card_id}.{port}"
        if self.domain.core.cards_page_open and now - self._level_at.get(key, 0) >= LEVEL_EVERY:
            self._level_at[key] = now
            from .audio import level  # noqa: PLC0415
            self.domain.core.notify("level", domain=self.domain.id, wire=key, level=level(chunk))
        for target, in_port in self.wired_from(card_id, port):
            if hasattr(target, "stream"):
                try:
                    target.stream(in_port, chunk, meta)
                except Exception as err:  # noqa: BLE001 - one card must not stop the audio
                    self.failed(target.id, "on audio", err)

    def clip(self, card_id: str, port: str, clip: dict[str, Any]) -> None:
        """An audio clip (e.g. an Audio clipper's stretch of speech): to every wired card that takes clips, in the background."""
        what = "stop" if clip.get("stop") else f"{clip.get('seconds')} s of audio"
        self.last[f"{card_id}.{port}"] = {"clip": what, "source": clip.get("name"), "time": time.time()}
        self._log(card_id, "out", port, f"{what}{f' · {t}' if (t := clip.get('text')) else ''} ({clip.get('name') or clip.get('source') or 'any'})")
        self.domain.core.notify("flow", domain=self.domain.id, wire=f"{card_id}.{port}")
        for target, in_port in self.wired_from(card_id, port):
            if hasattr(target, "clip"):
                self.domain.hass.async_create_background_task(target.clip(in_port, clip), f"aac {target.id} clip")
