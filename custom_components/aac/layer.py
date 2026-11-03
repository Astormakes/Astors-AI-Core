"""The card layer's core (docs/v2 page 6): what the running Domain and its cards call `self.core`. It runs in the card
worker (walled in, its own loop) or, for tests, inside Home Assistant; either way it reaches the world only through
the core (`core.Core`) by one link: `link.ask(do, args)` (awaited, the core answers with plain data) and
`link.tell(message)` (no answer). What only concerns the cards is its own: the running Domain, the calendar the cards
offer, the channels cards subscribe to, time and tasks (its clock). No Home Assistant here.

The core asks it with `handle(do, args)` (the panel's commands, a conversation, the calendar entity, which domain
runs) and tells it with `told(message)` (a model's text as it is written, a state that changed, audio, the values it
keeps a copy of).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import itertools
from typing import Any

from .calendars import Calendars
from .const import plain_name


class Clock:
    """Time and tasks on the card layer's own loop (the worker's)."""

    def __init__(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._tasks: set[asyncio.Task] = set()

    def task(self, coro: Any, name: str = "aac", background: bool = False, eager: bool = True) -> asyncio.Task:
        task = self.loop.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def later(self, seconds: float, job: Callable[[], Any]) -> Callable[[], None]:
        def run() -> None:
            if asyncio.iscoroutine(result := job()):
                self.task(result)
        handle = self.loop.call_later(seconds, run)
        return handle.cancel

    def every_minute(self, job: Callable[[Any], Any]) -> Callable[[], None]:
        """job(the minute, as a UTC time at second 0) at the start of every minute; a timer that comes a hair early or
        late still counts for its own minute, once."""
        from datetime import timedelta  # noqa: PLC0415

        from homeassistant.util import dt as dt_util  # noqa: PLC0415 - pure, imported before the wall by the cards

        handle: list[asyncio.TimerHandle] = []

        def plan(at: Any) -> None:
            handle[:] = [self.loop.call_later(max(0.0, (at - dt_util.utcnow()).total_seconds()), tick, at)]

        def tick(at: Any) -> None:
            job(at)
            plan(at + timedelta(minutes=1))
        plan((dt_util.utcnow() + timedelta(minutes=1)).replace(second=0, microsecond=0))
        return lambda: handle and handle[0].cancel()

    async def blocking(self, job: Callable[..., Any], *args: Any) -> Any:
        return await self.loop.run_in_executor(None, job, *args)


class Layer:
    """The card layer's core."""

    def __init__(self, link: Any, clock: Any) -> None:
        self.link, self.clock = link, clock
        self.domain: Any = None  # the running Domain
        self.data: dict[str, Any] = {}  # what the core lets it know: settings (storm), languages
        self._channels: dict[str, list[tuple[Any, Callable[..., Any]]]] = {}
        self._watching: dict[str, float] = {}
        self._page_until = 0.0
        self._ids = itertools.count(1)
        self._calls: dict[int, Callable[..., Any]] = {}  # a callback the core calls by its id (a model's text, a change)
        self.calendar = Calendars(self)
        self.home, self.services, self.models = HomeLink(self), ServicesLink(self), ModelsLink(self)
        self.audio, self.asks = AudioLink(self), AsksLink(self)

    # ------------------------------------------------------------------ time and tasks
    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self.clock.loop

    def task(self, coro: Any, name: str = "aac", background: bool = False, eager: bool = True) -> asyncio.Task:
        return self.clock.task(coro, name, background, eager)

    def later(self, seconds: float, job: Callable[[], Any]) -> Callable[[], None]:
        return self.clock.later(seconds, job)

    def every_minute(self, job: Callable[[Any], Any]) -> Callable[[], None]:
        return self.clock.every_minute(job)

    async def blocking(self, job: Callable[..., Any], *args: Any) -> Any:
        return await self.clock.blocking(job, *args)

    def doing(self, what: str) -> Any:
        """Names what runs while a block runs (in the worker its watchdog says it when it holds the worker too long)."""
        import contextlib  # noqa: PLC0415

        return self.link.doing(what) if hasattr(self.link, "doing") else contextlib.nullcontext()

    # ------------------------------------------------------------------ what it tells the core (no answer)
    def notify(self, what: str, **data: Any) -> None:
        """Open panels hear about it."""
        self.link.tell({"notify": what, **data})

    def log(self, part: str, level: str, text: str, **extra: Any) -> None:
        if self.domain is not None:
            self.domain.log.write(level, text, part=part, **extra)

    def note(self, note_id: str, title: str, text: str) -> None:
        self.link.tell({"note": note_id, "title": title, "text": text})

    def unnote(self, note_id: str) -> None:
        self.link.tell({"unnote": note_id})

    def card_renamed(self, old: str, new: str) -> None:
        self.link.tell({"renamed": old, "new": new})

    # ------------------------------------------------------------------ what it keeps (stored by the core)
    def store(self, key: str) -> StoreLink:
        return StoreLink(self, key)

    # ------------------------------------------------------------------ the core's values it has a copy of
    @property
    def storm(self) -> int:
        from .domain import STORM  # noqa: PLC0415

        return int((self.data.get("settings") or {}).get("storm") or STORM)

    def languages(self) -> list[str]:
        return list(self.data.get("languages") or [])

    def watch(self, cards: list[str], until: float) -> None:
        """An open Cards page shows these cards' views (until then: the core says when)."""
        self._page_until = until
        self._watching = {**{k: v for k, v in self._watching.items() if v > self.loop.time()}, **dict.fromkeys(cards, until)}

    def watched(self, card_id: str) -> bool:
        return self._watching.get(card_id, 0) > self.loop.time()

    @property
    def cards_page_open(self) -> bool:
        return self._page_until > self.loop.time()

    # ------------------------------------------------------------------ channels cards subscribe to (audio, a conversation)
    def subscribe(self, channel: str, card: Any, handler: Callable[..., Any]) -> None:
        self._channels.setdefault(channel, []).append((card, handler))
        if channel == "audio":
            self.link.tell({"wants_audio": True})

    def unsubscribe(self, card: Any) -> None:
        for channel, found in self._channels.items():
            self._channels[channel] = [(c, h) for c, h in found if c is not card]
        if not self._channels.get("audio"):
            self.link.tell({"wants_audio": False})

    def subscribers(self, channel: str) -> list[tuple[Any, Callable[..., Any]]]:
        return [(c, h) for c, h in self._channels.get(channel, []) if self.domain is not None and c.domain is self.domain]

    # ------------------------------------------------------------------ callbacks the core calls by id
    def callback(self, handler: Callable[..., Any]) -> int:
        call_id = next(self._ids)
        self._calls[call_id] = handler
        return call_id

    def forget(self, call_id: int) -> None:
        self._calls.pop(call_id, None)

    def told(self, message: dict[str, Any]) -> None:
        """What the core tells it."""
        if "call" in message:  # a callback: a model's text so far, a state that changed
            if (handler := self._calls.get(message["call"])) is not None:
                handler(*message.get("args") or [])
        elif "audio" in message:  # a satellite's audio: to every card that listens
            for _, handler in self.subscribers("audio"):
                handler(message["audio"], message.get("meta") or {})
        elif "data" in message:  # the values it keeps a copy of
            self.data.update(message["data"])
        elif "watch" in message:
            self.watch(message["watch"], self.loop.time() + float(message.get("seconds") or 15))
        elif "log" in message:  # a part of AAC itself writes in the running domain's log
            if self.domain is not None and not self.domain.log.closed:
                extra = {k: v for k, v in message.items() if k not in ("log", "level")}
                self.domain.log.write(message.get("level") or "info", message["log"], **extra)
        elif "requested" in message:  # the core took a card's request: the panel shows the domain's list
            if self.domain is not None:
                self.domain.requests.append(message["requested"])

    # ------------------------------------------------------------------ what the core asks
    async def handle(self, do: str, args: dict[str, Any]) -> Any:
        """The core's asks: which domain runs, the owner's decisions, what Home Assistant's side needs."""
        from . import defaults  # noqa: PLC0415
        from .domain import Domain  # noqa: PLC0415

        domain = self.domain
        if do == "run":  # this domain runs now (the core holds its sheet: it comes with the stored setup)
            await self.handle("halt", {})
            self.domain = domain = Domain(self, args["id"], args["name"])
            await domain.load()
            await domain.start()
            domain.log.write("info", f"Domain {args['name']} started ({len(domain.cards)} cards)", what="started")
            return {"cards": len(domain.cards)}
        if do == "halt":
            if domain is not None:
                await domain.stop()
                await domain.unload()
                await domain.save_now()
                self.domain = None
            return None
        if do == "first_start":
            await defaults.first_start(self, args["id"], args["name"])
            return None
        if domain is None:
            raise ValueError("no domain runs")
        if do == "flush":
            await domain.save_now()
            await domain.log.flush()
            return None
        if do == "voice_chain":
            await defaults.voice_chain(domain)
            return None
        if do == "renamed":
            domain.name = args["name"]
            return None
        if do == "sheet":  # the owner changed the sheet
            await domain.apply_sheet(args["sheet"], args.get("changed") or {})
            return None
        if do == "granted":  # the owner allowed (some of) a request
            await domain.granted(args["request"], args["granted"], args["sheet"])
            return None
        if do == "remake":  # card files whose version went up: their cards made again once idle; the types still waiting
            from .cards import CARD_TYPES  # noqa: PLC0415

            for card in [c for c in domain.cards.values() if c.type in args["types"]]:
                if not card.busy:
                    await domain.remake(card.id)
            return sorted({t for t in args["types"] if any(c.type == t and type(c) is not CARD_TYPES.get(t) for c in domain.cards.values())})
        if do == "converse":  # Home Assistant's Assist: the card on the "ha_conversation" channel answers
            takers = self.subscribers("ha_conversation")
            if not takers:
                return None
            return await takers[0][1](args["text"], args.get("conversation_id"), args.get("where") or "Home Assistant")
        if do == "calendar":  # Home Assistant's calendar entity: the entries the cards offer
            return await self._calendar(args)
        if do == "panel":  # one of the panel's commands on the running domain
            from . import panel  # noqa: PLC0415

            return await panel.run(self, args)
        raise ValueError(f"the card layer does nothing called {do}")

    async def _calendar(self, args: dict[str, Any]) -> Any:
        from datetime import datetime  # noqa: PLC0415

        if args["op"] == "entries":
            found = self.calendar.entries(datetime.fromisoformat(args["start"]), datetime.fromisoformat(args["end"]))
            return [{**e, "start_v": e["start_v"].isoformat(), "end_v": e["end_v"].isoformat()} for e in found]
        if args["op"] == "add":
            return await self.calendar.add(args["entry"])
        if args["op"] == "change":
            return await self.calendar.change(args["uid"], args["entry"])
        if args["op"] == "remove":
            return await self.calendar.remove(args["uid"])
        raise ValueError(f"no calendar {args['op']}")


class _Link:
    """One of the core's services, asked over the link."""

    name = ""

    def __init__(self, layer: Layer) -> None:
        self.layer = layer

    async def _ask(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return await self.layer.link.ask(f"{self.name}.{method}", {"args": list(args), "kwargs": kwargs})


class HomeLink(_Link):
    """home.HomeService, over the link."""

    name = "home"

    async def entities(self) -> list[dict[str, Any]]:
        return await self._ask("entities")

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        return await self._ask("state", entity_id)

    async def call(self, domain: str, service: str, entity_id: str, data: dict[str, Any]) -> None:
        return await self._ask("call", domain, service, entity_id, data)

    def watch(self, entity_ids: list[str], handler: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        if not entity_ids:
            return lambda: None
        call_id = self.layer.callback(handler)
        self.layer.task(self._ask("watch", entity_ids, call_id))

        def cancel() -> None:
            self.layer.forget(call_id)
            self.layer.link.tell({"unwatch": call_id})
        return cancel

    async def calendar_events(self, entity_id: str, start: str, end: str) -> list[dict[str, Any]]:
        return await self._ask("calendar_events", entity_id, start, end)

    async def catalog(self, kinds: list[str]) -> list[dict[str, Any]]:
        return await self._ask("catalog", kinds)


class ServicesLink(_Link):
    """services.Services, over the link."""

    name = "services"
    file_name = staticmethod(plain_name)

    async def fetch(self, url: str, method: str = "GET", params: dict[str, Any] | None = None, body: Any = None,
                    headers: dict[str, str] | None = None, seconds: float = 20) -> dict[str, Any]:
        return await self._ask("fetch", url, method, params, body, headers, seconds)

    async def read_file(self, name: str) -> str:
        return await self._ask("read_file", name)

    async def write_file(self, name: str, text: str, append: bool = False) -> str:
        return await self._ask("write_file", name, text, append)

    async def notification(self, service: str, message: str, title: str) -> None:
        return await self._ask("notification", service, message, title)

    async def data_read(self, parts: list[str]) -> bytes | None:
        return await self._ask("data_read", parts)

    async def data_write(self, parts: list[str], data: bytes, append: bool = False) -> None:
        return await self._ask("data_write", parts, data, append)

    async def data_list(self, parts: list[str], suffix: str = "") -> list[str]:
        return await self._ask("data_list", parts, suffix)

    async def data_remove(self, parts: list[str]) -> None:
        return await self._ask("data_remove", parts)

    async def data_move(self, parts: list[str], to: list[str]) -> None:
        return await self._ask("data_move", parts, to)


class ModelsLink(_Link):
    """models.ModelService, over the link (a model's text as it is written comes back by a callback)."""

    name = "models"

    async def chat(self, wanted: list[dict[str, Any]] | None, tag: str, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None, on_delta: Callable[[str, str], None] | None = None,
                   params: dict[str, Any] | None = None, give_up: bool = False) -> dict[str, Any]:
        call_id = self.layer.callback(on_delta) if on_delta else None
        try:
            return await self._ask("chat", wanted, tag, messages, tools, call_id, params, give_up)
        finally:
            if call_id is not None:
                self.layer.forget(call_id)

    async def embed(self, wanted: list[dict[str, Any]] | None, text: str) -> list[float] | None:
        return await self._ask("embed", wanted, text)

    async def template(self, model_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._ask("template", model_id, body)

    async def servers(self) -> list[dict[str, Any]]:
        return await self._ask("servers")

    async def server(self, action: str, aac_model: str, model: str) -> dict[str, Any]:
        return await self._ask("server", action, aac_model, model)


class AudioLink(_Link):
    """audio.AudioHub, over the link (spoken: the copy the core keeps up to date)."""

    name = "audio"

    async def tts(self, text: str, language: str | None = None, engine: str | None = None,
                  voices: dict[str, Any] | None = None, wait: float | None = None) -> bytes | None:
        return await self._ask("tts", text, language, engine, voices, wait)

    async def stt(self, pcm: bytes, language: str | None = None, models: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        return await self._ask("stt", pcm, language, models)

    async def play(self, source_id: str, wav: bytes, text: str = "", queue: bool = False) -> bool:
        return await self._ask("play", source_id, wav, text, queue)

    async def stop(self, source_id: str) -> bool:
        return await self._ask("stop", source_id)

    async def voices(self, engine: str | None = None) -> dict[str, Any]:
        return await self._ask("voices", engine)

    async def clip_url(self, wav: bytes) -> str:
        return await self._ask("clip_url", wav)

    async def waveforms(self, source: str | None, speaker: str, test: bool = False, seconds: float = 5) -> dict[str, Any]:
        return await self._ask("waveforms", source, speaker, test, seconds)

    def mark(self, source_id: str | None, stage: str) -> None:
        if source_id:
            self.layer.link.tell({"mark": source_id, "stage": stage})

    async def spoken(self) -> dict[str, Any]:
        """When AAC last spoke (seconds since, overall and per satellite)."""
        return await self._ask("spoken")


class AsksLink(_Link):
    """asks.Asks, over the link: an action at ask waits for the owner's yes."""

    name = "asks"

    async def ask(self, card: Any, thing: str, what: str) -> bool:
        return await self._ask("ask", {"card": card.id, "name": card.name}, thing, what)


class StoreLink:
    """A record the core keeps (a domain's setup, a card's data): as Home Assistant's Store is used."""

    def __init__(self, layer: Layer, key: str) -> None:
        self.layer, self.key = layer, key
        self._cancel: Callable[[], None] | None = None
        self._data: Callable[[], Any] | None = None

    async def async_load(self) -> Any:
        return await self.layer.link.ask("store.load", {"args": [self.key], "kwargs": {}})

    async def async_save(self, data: Any) -> None:
        if self._cancel:
            self._cancel()
            self._cancel = None
        await self.layer.link.ask("store.save", {"args": [self.key, data], "kwargs": {}})

    def async_delay_save(self, data: Callable[[], Any], delay: float = 0) -> None:
        self._data = data
        if self._cancel is None:
            self._cancel = self.layer.later(delay, self._flush)

    async def _flush(self) -> None:
        self._cancel = None
        if self._data is not None:
            await self.async_save(self._data())

    async def async_remove(self) -> None:
        if self._cancel:
            self._cancel()
            self._cancel = None
        await self.layer.link.ask("store.remove", {"args": [self.key], "kwargs": {}})
