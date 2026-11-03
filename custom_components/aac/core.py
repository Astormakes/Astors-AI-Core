"""The core (docs/v2 page 1): not a card, cannot be removed. It holds the model pool (shared by every domain) and
the domains (`domain.py`); one domain runs at a time. It is what touches Home Assistant: the card layer reaches the
world only through its services (storage, notifications, Home Assistant's entities, the web, files, models, audio,
time and tasks).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from copy import deepcopy
from pathlib import Path
import shutil
import time
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store

from .cards import CARD_TYPES, Card
from .const import LOGGER, SIGNAL
from .domain import STORM, Domain, fits, slug, unpacked
from .permissions import rank
from .models import ModelPool


def move_folder(src: Path, dst: Path) -> None:
    """Files kept under an id go with it (what was left under the new id is gone)."""
    if src.is_dir():
        shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


ANSWERS = {  # what the card layer may ask of each of the core's services (models.chat, home.watch, asks.ask: above)
    "home": ("entities", "state", "call", "calendar_events", "catalog"),
    "services": ("fetch", "read_file", "write_file", "notification", "data_read", "data_write", "data_list", "data_remove", "data_move"),
    "models": ("embed", "template", "servers", "server"),
    "audio": ("tts", "stt", "play", "stop", "voices", "clip_url", "waveforms"),
}
WATCH_SECONDS = 15  # an open Cards page says every few seconds which views it shows
CARD_FILES_EVERY = 10  # seconds between looks at the cards folder (a card file whose VERSION went up)


class Core:
    """AAC's core: the model pool and the domains."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self._store: Store = Store(hass, 1, "aac2.core")
        self.data: dict[str, Any] = {}
        self.pool = ModelPool(hass, [])
        from .layer import Layer  # noqa: PLC0415
        self.layer = Layer(self, self)  # the card layer, here in Home Assistant (the link and the clock: the core itself)
        self.sheet: dict[str, str] = {}  # the running domain's permissions: the owner's, held here (docs/v2 page 6)
        self.requests: list[dict[str, Any]] = []  # cards asking for more, held here until the owner decides
        self.running: dict[str, str] = {}  # the running domain: {id, name}
        self.wants_audio = False  # a card of the running domain listens to the satellites
        self._watches: dict[int, Callable[[], None]] = {}  # the layer's state watches by id: their cancel
        from .audio import AudioHub  # noqa: PLC0415
        self.audio = AudioHub(self)
        from .asks import Asks  # noqa: PLC0415
        self.asks = Asks(self)
        from .home import HomeService  # noqa: PLC0415
        self.home = HomeService(self)
        from .services import Services  # noqa: PLC0415
        self.services = Services(self)
        from .models import ModelService  # noqa: PLC0415
        self.models = ModelService(self)

    async def load(self) -> None:
        """Read the core; the first time, build the default domain."""
        self.data = await self._store.async_load() or {}
        first = not self.data
        self.data.setdefault("models", [])
        self.data.setdefault("benches", {})  # the last benchmark of each server's model ("<address> <model>")
        self.data.setdefault("facts", {})  # what llama.cpp said about each server's model while it was loaded (same keys)
        self.data.setdefault("servers", [])
        self.data.setdefault("domains", [])
        self.pool = ModelPool(self.hass, self.data["models"], self.data["benches"], self.data["servers"], self.data["facts"], self.save,
                              lambda level, text: self.log("models", level, text))
        for model in self.data["models"]:  # every model belongs to a server (its address and key come from it)
            self._give_server(model)
        if first:
            from .permissions import THINGS  # noqa: PLC0415
            self.data["domains"], self.data["active"] = [{"id": "assistant", "name": "Assistant"}], "assistant"
            self.running = {"id": "assistant", "name": "Assistant"}
            self.sheet, self.requests = {thing: "full" for thing in THINGS}, []  # AAC's own setup; entities are the owner's pick
            await self.layer_ask("first_start", {"id": "assistant", "name": "Assistant", "sheet": self.sheet})
            self.save()
        if not self.data["domains"]:
            self.data["domains"] = [{"id": "assistant", "name": "Assistant"}]
        self.data.setdefault("active", self.data["domains"][0]["id"])
        for info in list(self.data["domains"]):  # a domain's id is its name, as a card's is
            if not fits(info["id"], info["name"]):
                await self._move(info["id"], self._free_id(info["name"]))
        await self.switch(self.data["active"])
        await self.voice_chain()

    async def voice_chain(self) -> None:
        """Once satellites are known, the default domain gets the voice chain (once)."""
        if self.data.get("voice_chain"):
            return
        self.data["voice_chain"] = True
        self.save()
        if self.audio.settings.get("known"):
            await self.layer_ask("voice_chain", {})

    def save(self) -> None:
        """Keep the core."""
        self._store.async_delay_save(lambda: self.data, 1)

    # ------------------------------------------------------------------ what the card layer keeps, and tells the owner
    def store(self, key: str) -> Store:
        """A stored record (a domain's setup, a card's data)."""
        return Store(self.hass, 1, key)

    def _held(self, key: str, data: Any) -> Any:
        """The running domain's record always carries the sheet and the requests as the core holds them: the card
        layer can neither raise its own permissions nor make up the owner's yes."""
        if self.running and key == f"aac2.domain.{self.running['id']}" and isinstance(data, dict):
            return {**data, "permissions": dict(self.sheet), "requests": list(self.requests)}
        return data

    # ------------------------------------------------------------------ the link to the card layer (layer.py)
    async def layer_ask(self, do: str, args: dict[str, Any]) -> Any:
        """Ask the card layer (in the worker, or here)."""
        return await self.layer.handle(do, args)

    def layer_tell(self, message: dict[str, Any]) -> None:
        """Tell the card layer (no answer)."""
        self.layer.told(message)

    @property
    def domain(self) -> Domain | None:
        """The running Domain object (only while the card layer runs here: tests, Home Assistant's own side)."""
        return self.layer.domain

    def ask(self, do: str, args: dict[str, Any]) -> Any:
        """The link's ask, as the card layer calls it."""
        return self.answer(do, args)

    def tell(self, message: dict[str, Any]) -> None:
        """The link's tell, as the card layer calls it."""
        self.told(message)

    def allows(self, thing: str) -> str:
        """How far the running domain lets its cards reach a thing (the sheet; not on it: hidden)."""
        return self.sheet.get(thing) or "hidden"

    async def answer(self, do: str, args: dict[str, Any]) -> Any:
        """What the card layer asks the core: only these, in plain data; each service holds it to the sheet."""
        a, kw = list(args.get("args") or []), dict(args.get("kwargs") or {})
        if do == "models.chat":
            wanted, tag, messages, tools, call_id, params, give_up = a
            on_delta = (lambda text, thinking: self.layer_tell({"call": call_id, "args": [text, thinking]})) if call_id else None
            return await self.models.chat(wanted, tag, messages, tools, on_delta, params, give_up)
        if do == "home.watch":
            ids, call_id = a
            self._watches[call_id] = self.home.watch(ids, lambda change: self.layer_tell({"call": call_id, "args": [change]}))
            return None
        if do == "asks.ask":
            who, thing, what = a
            if rank(self.allows(thing)) < rank("ask"):
                raise PermissionError(f"the domain does not allow {thing} at ask")
            return await self.asks.ask({**who, "domain": self.running.get("id")}, thing, what)
        if do == "requests.add":
            return self.request(*a)
        if do == "audio.spoken":
            return self.audio.spoken()
        if do == "store.load":
            return self._held(a[0], await self.store(a[0]).async_load())
        if do == "store.save":
            return await self.store(a[0]).async_save(self._held(a[0], a[1]))
        if do == "store.remove":
            return await self.store(a[0]).async_remove()
        service, _, method = do.partition(".")
        if method not in ANSWERS.get(service, ()):
            raise PermissionError(f"the core answers no {do}")
        return await getattr(getattr(self, service), method)(*a, **kw)

    def told(self, message: dict[str, Any]) -> None:
        """What the card layer tells the core."""
        if "notify" in message:
            self.notify(message.pop("notify"), **message)
        elif "note" in message:
            self.note(message["note"], message["title"], message["text"])
        elif "unnote" in message:
            self.unnote(message["unnote"])
        elif "renamed" in message:
            self.card_renamed(message["renamed"], message["new"])
        elif "wants_audio" in message:
            self.wants_audio = bool(message["wants_audio"])
        elif "unwatch" in message:
            if (cancel := self._watches.pop(message["unwatch"], None)) is not None:
                cancel()
        elif "mark" in message:
            self.audio.mark(message["mark"], message["stage"])

    def hear(self, chunk: bytes, meta: dict[str, Any]) -> None:
        """A satellite's audio: to the card layer when a card there listens."""
        if self.wants_audio:
            self.layer_tell({"audio": chunk, "meta": meta})

    def share(self) -> None:
        """The values the card layer keeps a copy of: AAC's settings, the home's languages."""
        self.layer_tell({"data": {"settings": dict(self.data.get("settings") or {}), "languages": self.languages()}})

    # ------------------------------------------------------------------ the sheet and the requests (the owner's)
    async def set_permissions(self, levels: dict[str, str]) -> dict[str, str]:
        """The owner changes the sheet (hidden takes a thing off it); the cards follow it."""
        from .permissions import LEVELS  # noqa: PLC0415

        for thing, level in levels.items():
            if level not in LEVELS:
                raise ValueError(f"no level {level}")
            if level == "hidden":
                self.sheet.pop(thing, None)
            else:
                self.sheet[thing] = level
        await self.layer_ask("sheet", {"sheet": self.sheet, "changed": levels})
        return dict(self.sheet)

    def request(self, card: dict[str, str], wanted: dict[str, str], reason: str) -> dict[str, Any]:
        """A card asks for more (the layer worked out what it does not have yet): only what the sheet lets exist."""
        from .asks import request_note  # noqa: PLC0415
        from .context import new_id  # noqa: PLC0415

        wanted = {t: lvl for t, lvl in wanted.items() if self.allows(t) != "hidden"}
        if not wanted:
            return {"asked": {}, "note": "nothing to ask for (it has that already, or there is no such thing)"}
        req = {"id": new_id(), "card": card["card"], "name": card["name"], "things": wanted,
               "reason": str(reason).strip()[:500] or "(no reason given)", "since": time.time()}
        self.requests.append(req)
        request_note(self, self.running.get("name", ""), req)
        self.layer_tell({"requested": req})
        self.notify("requests", domain=self.running.get("id"))
        return {"asked": wanted, "note": "the owner decides; until then nothing changes"}

    async def decide(self, request_id: str, allowed: list[str]) -> dict[str, str]:
        """The owner's answer to a request: the things allowed are the card's at the level asked (the sheet raised
        where it was lower: it is the owner who says so). Returns what was granted."""
        from .asks import request_done  # noqa: PLC0415

        req = next((r for r in self.requests if r["id"] == request_id), None)
        if req is None:
            raise ValueError("no such request (decided already?)")
        self.requests.remove(req)
        granted = {t: lvl for t, lvl in req["things"].items() if t in allowed}
        for thing, level in granted.items():
            if rank(self.allows(thing)) < rank(level):
                self.sheet[thing] = level
        await self.layer_ask("granted", {"request": req, "granted": granted, "sheet": self.sheet})
        request_done(self, request_id)
        self.notify("requests", domain=self.running.get("id"))
        self.notify("setup", domain=self.running.get("id"))
        return granted

    def card_renamed(self, old: str, new: str) -> None:
        """A card's id moved: what the core keeps under it (the sidebar's fold) moves along."""
        if old in (folds := self.data.get("folds") or {}):
            folds[new] = folds.pop(old)
            self.save()

    def note(self, note_id: str, title: str, text: str) -> None:
        """A notification in Home Assistant for the owner (the same id again replaces it)."""
        try:
            persistent_notification.async_create(self.hass, text, title=title, notification_id=note_id)
        except Exception as err:  # noqa: BLE001 - what it tells about happened anyway
            self.log("core", "warning", f"No Home Assistant notification: {err!r}")

    def unnote(self, note_id: str) -> None:
        """That notification goes."""
        with contextlib.suppress(Exception):
            persistent_notification.async_dismiss(self.hass, note_id)

    # ------------------------------------------------------------------ time and tasks: the card layer's only way to them
    # (in the worker: its own loop; here Home Assistant's, so its tests can move the time)
    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self.hass.loop

    def task(self, coro: Any, name: str = "aac", background: bool = False, eager: bool = True) -> asyncio.Task:
        """Run a coroutine on its own (background: nobody waits for it, e.g. a long watch; eager: it starts at once,
        up to its first wait; not eager: once the caller waits)."""
        if background:
            return self.hass.async_create_background_task(coro, name, eager_start=eager)
        return self.hass.async_create_task(coro, name, eager_start=eager)

    def later(self, seconds: float, job: Callable[[], Any]) -> Callable[[], None]:
        """Run job (a function, or one that returns a coroutine) once after some seconds; returns: cancel."""
        def run(_now: Any) -> None:
            if asyncio.iscoroutine(result := job()):
                self.task(result)
        return async_call_later(self.hass, seconds, HassJob(callback(run), cancel_on_shutdown=True))

    def every_minute(self, job: Callable[[Any], Any]) -> Callable[[], None]:
        """Run job(now) at the start of every minute; returns: cancel."""
        return async_track_time_change(self.hass, callback(lambda now: job(now)), second=0)

    async def blocking(self, job: Callable[..., Any], *args: Any) -> Any:
        """Run a function that blocks (disk, CPU) in a thread of its own."""
        return await self.hass.async_add_executor_job(job, *args)

    async def save_now(self) -> None:
        """Keep the core now."""
        await self._store.async_save(self.data)

    async def watch_cards(self, folder: str) -> None:
        """While AAC runs: card files whose VERSION went up are loaded again, and their running cards are made again
        (with their settings and data) as soon as they are not busy; new card types show in the palette."""
        from .cards import reload_changed  # noqa: PLC0415

        waiting: set[str] = set()  # card types made again once their cards are idle
        while True:
            await asyncio.sleep(CARD_FILES_EVERY)
            changed = await self.hass.async_add_executor_job(reload_changed, folder)
            waiting = set(await self.layer_ask("remake", {"types": sorted(waiting | {t for t in changed if t in CARD_TYPES})}))
            if changed:
                self.notify("setup", domain=self.running.get("id"))

    def watch(self, cards: list[str]) -> None:
        """An open Cards page shows these cards' views (it says so again every few seconds)."""
        self.layer_tell({"watch": cards, "seconds": WATCH_SECONDS})

    @property
    def storm(self) -> int:
        """The storm limit (AAC's settings): contexts within a second that turn a wire red."""
        return int((self.data.get("settings") or {}).get("storm") or STORM)

    @callback
    def languages(self) -> list[str]:
        """The home's languages, main first (ISO codes): the owner's list (Audio page), else as HA describes the home."""
        from .languages import from_ha  # noqa: PLC0415

        return list(self.data.get("languages") or from_ha(self.hass.config.language, self.hass.config.country))

    def log(self, part: str, level: str, text: str, **extra: Any) -> None:
        """A part of AAC itself ("audio", "models" …) writes in the running domain's log (HA's log while none runs)."""
        if self.running:
            self.layer_tell({"log": text, "level": level, "part": part, **extra})
        else:
            LOGGER.log(30 if level in ("error", "warning") else 20, "AAC %s: %s", part, text)

    def notify(self, what: str, **data: Any) -> None:
        """Tell open panels."""
        async_dispatcher_send(self.hass, SIGNAL, {"what": what, **data})

    async def switch(self, domain_id: str) -> str:
        """Run another domain (one at a time); its id."""
        info = next((d for d in self.data["domains"] if d["id"] == domain_id), self.data["domains"][0])
        await self.halt()
        data = await self.store(f"aac2.domain.{info['id']}").async_load() or {}
        self.sheet, self.requests = dict(data.get("permissions") or {}), list(data.get("requests") or [])
        self.running = {"id": info["id"], "name": info["name"]}
        self.share()
        await self.layer_ask("run", {"id": info["id"], "name": info["name"]})
        self.data["active"] = info["id"]
        self.save()
        self.notify("setup", domain=info["id"])
        return info["id"]

    async def halt(self) -> None:
        """The running domain stops (its setup and log written)."""
        if self.running:
            await self.layer_ask("halt", {})
            self.running = {}

    def folder(self, *ids: str) -> Path:
        """Where a domain's cards keep files: <config>/aac/data/<domain>[/<card>]."""
        return Path(self.hass.config.path("aac", "data", *ids))

    def _free_id(self, name: str) -> str:
        return Domain._free(slug(name), {d["id"] for d in self.data["domains"]})  # noqa: SLF001

    @staticmethod
    def _stored_cards(data: dict[str, Any]) -> set[str]:
        """The cards a domain's store knows (the setup, its Undo steps and slots): those that may have data stored."""
        steps = unpacked(data.get("undo", []), {"cards": data.get("cards", {}), "wires": data.get("wires", [])})
        return {i for setup in [data, *steps, *(data.get("slots") or {}).values()] for i in (setup or {}).get("cards") or {}}

    async def _move(self, old: str, new: str) -> None:
        """A domain's id moves (its name did): its store, its cards' stores and files, the running mark; a running domain
        stops and starts again under the new one."""
        running = self.running.get("id") == old
        if running:
            await self.halt()
        store = Store(self.hass, 1, f"aac2.domain.{old}")
        data = await store.async_load() or {}
        for card_id in self._stored_cards(data):
            card_store = Store(self.hass, 1, f"aac2.card.{old}.{card_id}")
            if (kept := await card_store.async_load()) is not None:
                await Store(self.hass, 1, f"aac2.card.{new}.{card_id}").async_save(kept)
                await card_store.async_remove()
        await Store(self.hass, 1, f"aac2.domain.{new}").async_save(data)
        await store.async_remove()
        await self.hass.async_add_executor_job(move_folder, self.folder(old), self.folder(new))
        next(d for d in self.data["domains"] if d["id"] == old)["id"] = new
        if self.data.get("active") == old:
            self.data["active"] = new
        self.save()
        if running:
            await self.switch(new)
            self.log("core", "info", f"Renamed: {old} is now {new} (its name)", what="renamed")

    async def create(self, name: str) -> str:
        """A new, empty domain (no cards yet)."""
        new_id = self._free_id(name)
        self.data["domains"].append({"id": new_id, "name": name})
        self.save()
        return new_id

    async def rename(self, domain_id: str, name: str) -> str:
        """A domain's new name; its id follows (returns it)."""
        info = next(d for d in self.data["domains"] if d["id"] == domain_id)
        info["name"] = name
        if self.running.get("id") == domain_id:
            self.running["name"] = name
            await self.layer_ask("renamed", {"name": name})
        if not fits(domain_id, name):
            await self._move(domain_id, self._free_id(name))
        self.save()
        self.notify("setup", domain=info["id"])
        return info["id"]

    async def delete(self, domain_id: str) -> None:
        """A domain goes, with its cards' data (not the last one; the running one first hands over to another)."""
        rest = [d for d in self.data["domains"] if d["id"] != domain_id]
        if not rest or len(rest) == len(self.data["domains"]):
            raise ValueError("the last domain cannot go")
        if self.running.get("id") == domain_id:
            await self.switch(rest[0]["id"])
        store = Store(self.hass, 1, f"aac2.domain.{domain_id}")
        for card_id in self._stored_cards((await store.async_load()) or {}):
            await Store(self.hass, 1, f"aac2.card.{domain_id}.{card_id}").async_remove()
        await store.async_remove()
        await self.hass.async_add_executor_job(shutil.rmtree, self.folder(domain_id), True)
        self.data["domains"] = rest
        self.save()
        self.notify("setup", domain=self.running.get("id"))

    async def clone(self, name: str) -> str:
        """A copy of the running domain (cards, wires, their data, its sheet) to try things out."""
        src, new_id = self.running["id"], self._free_id(name)
        await self.layer_ask("flush", {})
        data = await self.store(f"aac2.domain.{src}").async_load() or {}
        await self.store(f"aac2.domain.{new_id}").async_save({k: deepcopy(data.get(k)) for k in ("cards", "wires", "positions")
                                                             if k in data} | {"permissions": dict(self.sheet)})
        for card_id in data.get("cards") or {}:
            if (kept := await self.store(f"aac2.card.{src}.{card_id}").async_load()) is not None:
                await self.store(f"aac2.card.{new_id}.{card_id}").async_save(kept)
        if await self.hass.async_add_executor_job(self.folder(src).is_dir):
            await self.hass.async_add_executor_job(shutil.copytree, self.folder(src), self.folder(new_id))
            await self.services.data_remove([new_id, "log.jsonl"])  # a copy starts its own log
        self.data["domains"].append({"id": new_id, "name": name})
        self.save()
        return new_id

    # ------------------------------------------------------------------ servers and models
    def save_server(self, server: dict[str, Any]) -> dict[str, Any]:
        """Add or change a model server (name, base_url, api_key); its models follow a new address or key."""
        old = next((s for s in self.data["servers"] if s["id"] == server.get("id")), None)
        if old:
            key = server.get("api_key") or old.get("api_key")
            old.update({k: v for k, v in server.items() if k != "api_key"}, api_key=key)
            for model in self.data["models"]:
                if model.get("server") == old["id"]:
                    model.update(base_url=old["base_url"], api_key=old.get("api_key"))
            self.save()
            return old
        server = {**server, "id": server.get("id") or self._new_id("s", server.get("name") or server["base_url"], self.data["servers"])}
        self.data["servers"].append(server)
        self.save()
        return server

    def delete_server(self, server_id: str) -> None:
        """Take a server and its models out of AAC."""
        self.data["servers"][:] = [s for s in self.data["servers"] if s["id"] != server_id]
        self.data["models"][:] = [m for m in self.data["models"] if m.get("server") != server_id]
        self.save()

    def _give_server(self, model: dict[str, Any]) -> None:
        """A model's server: the one it names (its address and key come from it), else the one at its address (a new
        one if there is none)."""
        server = next((s for s in self.data["servers"] if s["id"] == model.get("server")), None)
        if server is None and model.get("base_url"):
            server = next((s for s in self.data["servers"] if s["base_url"].rstrip("/") == model["base_url"].rstrip("/")
                           and (s.get("api_key") or "") == (model.get("api_key") or "")), None)
            if server is None:
                server = self.save_server({"name": model["base_url"].split("//")[-1].split("/")[0], "base_url": model["base_url"],
                                           "api_key": model.get("api_key")})
        if server is not None:
            model.update(server=server["id"], base_url=server["base_url"], api_key=server.get("api_key"))

    def _new_id(self, prefix: str, name: str, taken: list[dict[str, Any]]) -> str:
        base, ids, n = f"{prefix}_{slug(name) or prefix}", {x["id"] for x in taken}, 2
        new = base
        while new in ids:
            new, n = f"{base}_{n}", n + 1
        return new

    def save_model(self, model: dict[str, Any]) -> dict[str, Any]:
        """Add or change a model in the pool (on a server)."""
        old = self.pool.get(model.get("id", ""))
        if old:
            key = model.get("api_key") or old.get("api_key")
            old.update({k: v for k, v in model.items() if k != "api_key"}, api_key=key)
            self._give_server(old)
            return old
        model = {**model, "id": model.get("id") or self._new_id("m", model["name"], self.data["models"])}
        self._give_server(model)
        self.data["models"].append(model)
        self.save()
        return model

    def delete_model(self, model_id: str) -> None:
        """Take a model out of the pool."""
        self.data["models"][:] = [m for m in self.data["models"] if m["id"] != model_id]
        self.save()
