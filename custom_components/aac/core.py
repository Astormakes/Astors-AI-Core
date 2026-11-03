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

from .calendars import Calendars
from .cards import CARD_TYPES, Card
from .const import LOGGER, SIGNAL
from .domain import STORM, Domain, fits, slug, unpacked
from .models import ModelPool


def move_folder(src: Path, dst: Path) -> None:
    """Files kept under an id go with it (what was left under the new id is gone)."""
    if src.is_dir():
        shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


WATCH_SECONDS = 15  # an open Cards page says every few seconds which views it shows
CARD_FILES_EVERY = 10  # seconds between looks at the cards folder (a card file whose VERSION went up)


class Core:
    """AAC's core: the model pool and the domains."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self._store: Store = Store(hass, 1, "aac2.core")
        self._watching: dict[str, float] = {}  # card → until when a Cards page shows its view
        self._page_until = 0.0  # until when a Cards page is open
        self.data: dict[str, Any] = {}
        self._channels: dict[str, list[tuple[Card, Callable[..., Any]]]] = {}
        self.pool = ModelPool(hass, [])
        self.domain: Domain | None = None
        from .audio import AudioHub  # noqa: PLC0415
        self.audio = AudioHub(self)
        self.calendar = Calendars(self)
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
            from .defaults import async_first_start  # noqa: PLC0415
            await async_first_start(self)
        if not self.data["domains"]:
            self.data["domains"] = [{"id": "assistant", "name": "Assistant"}]
        self.data.setdefault("active", self.data["domains"][0]["id"])
        for info in list(self.data["domains"]):  # a domain's id is its name, as a card's is
            if not fits(info["id"], info["name"]):
                await self._move(info["id"], self._free_id(info["name"]))
        await self.switch(self.data["active"])
        from .defaults import async_voice_chain  # noqa: PLC0415
        await async_voice_chain(self)

    def save(self) -> None:
        """Keep the core."""
        self._store.async_delay_save(lambda: self.data, 1)

    # ------------------------------------------------------------------ what the card layer keeps, and tells the owner
    def store(self, key: str) -> Store:
        """A stored record (the domain's setup, a card's data): async_load, async_save, async_delay_save, async_remove."""
        return Store(self.hass, 1, key)

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
            waiting |= {t for t in changed if t in CARD_TYPES}  # a deleted type's cards stay as they are
            if self.domain is None or not waiting:
                continue
            for card in [c for c in self.domain.cards.values() if c.type in waiting]:
                if not card.busy:
                    await self.domain.remake(card.id)
            waiting = {t for t in waiting if any(c.type == t and type(c) is not CARD_TYPES.get(t) for c in self.domain.cards.values())}
            if changed:
                self.notify("setup", domain=self.domain.id)

    def watch(self, cards: list[str]) -> None:
        """An open Cards page shows these cards' views (it says so again every few seconds)."""
        until = time.monotonic() + WATCH_SECONDS
        self._page_until = until
        self._watching = {**{k: v for k, v in self._watching.items() if v > time.monotonic()}, **dict.fromkeys(cards, until)}

    def watched(self, card_id: str) -> bool:
        """A Cards page shows this card's view now."""
        return self._watching.get(card_id, 0) > time.monotonic()

    @property
    def storm(self) -> int:
        """The storm limit (AAC's settings): contexts within a second that turn a wire red."""
        return int((self.data.get("settings") or {}).get("storm") or STORM)

    @property
    def cards_page_open(self) -> bool:
        """A Cards page is open (live wires are only sent then)."""
        return self._page_until > time.monotonic()

    # ------------------------------------------------------------------ channels (design rule: the core never asks for a card type)
    @callback
    def subscribe(self, channel: str, card: Card, handler: Callable[..., Any]) -> None:
        """A card takes what comes in on a core channel ("audio": satellite audio, "ha_conversation": HA's Assist).
        Any card may: a card of your own can replace a built-in one."""
        self._channels.setdefault(channel, []).append((card, handler))

    @callback
    def unsubscribe(self, card: Card) -> None:
        """A card stops taking anything (called when it unloads)."""
        for channel, subs in self._channels.items():
            self._channels[channel] = [(c, h) for c, h in subs if c is not card]
        self.calendar.forget(card)

    def subscribers(self, channel: str) -> list[tuple[Card, Callable[..., Any]]]:
        """Who takes a channel (cards of the running domain), in the order they subscribed."""
        return list(self._channels.get(channel, []))

    @callback
    def languages(self) -> list[str]:
        """The home's languages, main first (ISO codes): the owner's list (Audio page), else as HA describes the home."""
        from .languages import from_ha  # noqa: PLC0415

        return list(self.data.get("languages") or from_ha(self.hass.config.language, self.hass.config.country))

    def log(self, part: str, level: str, text: str, **extra: Any) -> None:
        """A part of AAC itself ("audio", "models" …) writes in the running domain's log (HA's log while none runs)."""
        if self.domain is not None and not self.domain.log.closed:
            self.domain.log.write(level, text, part=part, **extra)
        else:
            LOGGER.log(30 if level in ("error", "warning") else 20, "AAC %s: %s", part, text)

    def notify(self, what: str, **data: Any) -> None:
        """Tell open panels."""
        async_dispatcher_send(self.hass, SIGNAL, {"what": what, **data})

    async def switch(self, domain_id: str) -> Domain:
        """Run another domain (one at a time)."""
        info = next((d for d in self.data["domains"] if d["id"] == domain_id), self.data["domains"][0])
        if self.domain is not None:
            await self.domain.stop()
            await self.domain.unload()
            await self.domain.save_now()
        self.domain = Domain(self, info["id"], info["name"])
        await self.domain.load()
        await self.domain.start()
        self.domain.log.write("info", f"Domain {info['name']} started ({len(self.domain.cards)} cards)", what="started")
        self.data["active"] = info["id"]
        self.save()
        self.notify("setup", domain=info["id"])
        return self.domain

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
        running = self.domain is not None and self.domain.id == old
        if running:
            await self.domain.stop()
            await self.domain.unload()
            await self.domain.save_now()
            self.domain = None
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
            self.domain.log.write("info", f"Renamed: {old} is now {new} (its name)", what="renamed")

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
        if self.domain is not None and self.domain.id == domain_id:
            self.domain.name = name
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
        if self.domain is not None and self.domain.id == domain_id:
            await self.switch(rest[0]["id"])
        store = Store(self.hass, 1, f"aac2.domain.{domain_id}")
        for card_id in self._stored_cards((await store.async_load()) or {}):
            await Store(self.hass, 1, f"aac2.card.{domain_id}.{card_id}").async_remove()
        await store.async_remove()
        await self.hass.async_add_executor_job(shutil.rmtree, self.folder(domain_id), True)
        self.data["domains"] = rest
        self.save()
        self.notify("setup", domain=self.domain.id)

    async def clone(self, name: str) -> str:
        """A copy of the running domain (cards, wires, their data) to try things out."""
        src = self.domain
        new_id = self._free_id(name)
        await src.save_now()
        await src.log.flush()
        dst = Domain(self, new_id, name)
        dst._specs, dst.wires, dst.positions = deepcopy(src._specs), deepcopy(src.wires), deepcopy(src.positions)  # noqa: SLF001
        dst.permissions = deepcopy(src.permissions)  # your own copy: it may reach what the original may
        await dst.save_now()
        for card_id in src._specs:  # noqa: SLF001
            if (data := await src.load_card_data(card_id)) is not None:
                await dst.save_card_data(card_id, data)
        if await self.hass.async_add_executor_job(self.folder(src.id).is_dir):
            await self.hass.async_add_executor_job(shutil.copytree, self.folder(src.id), self.folder(new_id))
            await self.services.data_remove(dst.log.parts)  # a copy starts its own log
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
