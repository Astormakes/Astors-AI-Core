"""The core (docs/v2 page 1): not a card, cannot be removed. It holds the model pool (shared by every domain) and
the domains; one domain runs at a time. A domain is a complete setup: its cards (each owning its data), wires,
positions, Undo history and three save slots, all kept on the server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
import re
import shutil
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .cards import CARD_TYPES, Card, renamed_refs
from .const import LOGGER, SIGNAL
from .flow import Flow
from .models import ModelPool

UNDO_STEPS = 20
SLOTS = ("1", "2", "3")


def slug(text: str) -> str:
    """An id from a name."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "card"


def fits(an_id: str, name: str) -> bool:
    """An id is its name: the name's slug, a number after it when another one had that name first."""
    return re.fullmatch(rf"{re.escape(slug(name))}(_\d+)?", an_id) is not None


def packed(steps: list[dict[str, Any]], now: dict[str, Any]) -> list[dict[str, Any]]:
    """Undo steps as they are stored: each only what differs from the setup after it (its card ids, the cards and wires
    that changed; positions as they are), so a big setting that stays the same is stored once."""
    out, newer = [], now
    for step in reversed(steps):
        kept = {"ids": list(step["cards"]), "cards": {k: v for k, v in step["cards"].items() if newer["cards"].get(k) != v}}
        if step["wires"] != newer["wires"]:
            kept["wires"] = step["wires"]
        if "positions" in step:
            kept["positions"] = step["positions"]
        out.append(kept)
        newer = step
    return out[::-1]


def unpacked(steps: list[dict[str, Any]], now: dict[str, Any]) -> list[dict[str, Any]]:
    """Stored Undo steps as whole setups again (a step stored whole has every card and its wires)."""
    out, newer = [], now
    for kept in reversed(steps):
        cards = {k: kept["cards"][k] if k in kept["cards"] else newer["cards"].get(k) for k in kept.get("ids", kept["cards"])}
        step = {"cards": {k: v for k, v in cards.items() if v is not None}, "wires": kept.get("wires", newer["wires"])}
        if "positions" in kept:
            step["positions"] = kept["positions"]
        out.append(step)
        newer = step
    return deepcopy(out[::-1])


def move_folder(src: Path, dst: Path) -> None:
    """Files kept under an id go with it (what was left under the new id is gone)."""
    if src.is_dir():
        shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


WATCH_SECONDS = 15  # an open Cards page says every few seconds which views it shows
CARD_FILES_EVERY = 10  # seconds between looks at the cards folder (a card file whose VERSION went up)

class Domain:
    """One setup of cards and wires."""

    def __init__(self, core: Core, domain_id: str, name: str) -> None:
        """Initialize."""
        self.core, self.hass, self.id, self.name = core, core.hass, domain_id, name
        self._store: Store = Store(core.hass, 1, f"aac2.domain.{domain_id}")
        self._card_stores: dict[str, Store] = {}
        self.cards: dict[str, Card] = {}
        self.wires: list[dict[str, str]] = []
        self.positions: dict[str, dict[str, float]] = {}
        self.undo: list[dict[str, Any]] = []
        self.slots: dict[str, Any] = {}
        self.user_tags: list[str] = []
        self.flow = Flow(self)
        self._specs: dict[str, dict[str, Any]] = {}
        self.running = False  # its cards' timers and listeners run

    # ------------------------------------------------------------------ storage
    async def load(self) -> None:
        """Read the setup and make its cards."""
        data = await self._store.async_load() or {}
        self._specs = data.get("cards", {})
        self.wires = data.get("wires", [])
        self.positions = data.get("positions", {})
        self.undo = unpacked(data.get("undo", []), {"cards": self._specs, "wires": self.wires})
        self.redo: list[dict[str, Any]] = []  # what Undo took back (until the next change; not kept over a restart)
        self.slots = data.get("slots", {})
        self.user_tags = data.get("user_tags", [])
        self.cards = {}
        for card_id, spec in list(self._specs.items()):  # a card made here may add another
            await self._make(card_id, spec)
        await self.follow_names()

    async def start(self) -> None:
        """Run: the cards start their timers and listeners."""
        self.running = True
        for card in list(self.cards.values()):
            await card.start()

    async def unload(self) -> None:
        """Another domain runs (or AAC stops): every card ends its timers; Redo is not kept."""
        self.running = False
        self._tidy(i for step in self.redo for i in step["cards"])
        self.redo = []
        for card in list(self.cards.values()):
            await self._end(card)

    async def _end(self, card: Card) -> None:
        """A card stops: its timers end (its unload) and the core channels it subscribed to forget it."""
        await card.unload()
        self.core.unsubscribe(card)

    async def _make(self, card_id: str, spec: dict[str, Any]) -> Card | None:
        kind = CARD_TYPES.get(spec.get("type", ""))
        if kind is None:
            LOGGER.warning("AAC: card type %s is not available (card %s)", spec.get("type"), card_id)
            return None
        if spec.get("version") != kind.version:  # saved by another version of the card's file
            spec["settings"] = kind.upgrade(dict(spec.get("settings") or {}), spec.get("version") or "0")
            spec["version"] = kind.version
        card = kind(self, card_id, spec.get("title", ""), spec.get("settings"))
        await card.load()
        if old := self.cards.get(card_id):
            await self._end(old)
        self.cards[card_id] = card
        if self.running:
            await card.start()
        return card

    def _data(self) -> dict[str, Any]:
        return {"cards": self._specs, "wires": self.wires, "positions": self.positions,
                "undo": packed(self.undo, {"cards": self._specs, "wires": self.wires}), "slots": self.slots, "user_tags": self.user_tags}

    def save(self) -> None:
        """Keep the setup (soon)."""
        self._store.async_delay_save(self._data, 1)

    async def save_now(self) -> None:
        """Keep the setup now."""
        await self._store.async_save(self._data())

    def _card_store(self, card_id: str) -> Store:
        if card_id not in self._card_stores:
            self._card_stores[card_id] = Store(self.hass, 1, f"aac2.card.{self.id}.{card_id}")
        return self._card_stores[card_id]

    async def load_card_data(self, card_id: str) -> dict[str, Any] | None:
        """A card's own data."""
        return await self._card_store(card_id).async_load()

    async def save_card_data(self, card_id: str, data: dict[str, Any]) -> None:
        """Keep a card's own data."""
        await self._card_store(card_id).async_save(data)

    # ------------------------------------------------------------------ changing the setup (with Undo)
    def _snapshot(self) -> dict[str, Any]:
        return deepcopy({"cards": self._specs, "wires": self.wires})

    def _remember(self) -> None:
        self.undo = self._on_top(self.undo, self._snapshot())
        self._tidy(i for step in self.redo for i in step["cards"])
        self.redo = []  # a new change: what was undone cannot come back

    def _on_top(self, steps: list[dict[str, Any]], step: dict[str, Any]) -> list[dict[str, Any]]:
        """A step on top of Undo or Redo; the oldest beyond UNDO_STEPS goes."""
        steps = [*steps, step]
        self._tidy(i for old in steps[:-UNDO_STEPS] for i in old["cards"])
        return steps[-UNDO_STEPS:]

    def _tidy(self, card_ids: Any) -> None:
        """These cards may have left every setup (now, Undo, Redo, the slots): then their data and files go (soon,
        once the change that let them go is done)."""
        if found := set(card_ids):
            self.hass.async_create_task(self._drop_data(found, only_unused=True), eager_start=False)

    async def _drop_data(self, card_ids: set[str], only_unused: bool = False) -> None:
        """Cards' stored data and files go (only_unused: of those no setup knows any more)."""
        for card_id in card_ids - (self._ids_taken() if only_unused else set()):
            await self._card_store(card_id).async_remove()
            self._card_stores.pop(card_id, None)
            await self.hass.async_add_executor_job(shutil.rmtree, self.core.folder(self.id, card_id), True)

    async def add_card(self, card_type: str, title: str = "", settings: dict[str, Any] | None = None,
                       at: dict[str, float] | None = None) -> Card:
        """A new card (its id is its name, see follow_names)."""
        if card_type not in CARD_TYPES:
            raise ValueError(f"No card type {card_type}")
        title = title or CARD_TYPES[card_type].title
        new = self._free(slug(title), self._ids_taken())
        self._remember()
        self._specs[new] = {"type": card_type, "title": title, "settings": dict(settings or {})}
        card = await self._make(new, self._specs[new])
        if at:
            self.positions[new] = {"x": at.get("x", 0), "y": at.get("y", 0)}
        self.save()
        self.core.notify("setup", domain=self.id)
        return card

    async def remove_card(self, *card_ids: str, drop_data: bool = False) -> None:
        """Take cards out, several in one Undo step. Their data and files stay for Undo (until no setup knows them any
        more), or go now (drop_data: Undo brings them back empty)."""
        gone = [c for c in dict.fromkeys(card_ids) if c in self._specs]
        if not gone:
            return
        self._remember()
        self.undo[-1]["positions"] = deepcopy(self.positions)  # Undo puts them back where they were
        for card_id in gone:
            self._specs.pop(card_id)
            if card := self.cards.pop(card_id, None):
                await self._end(card)
            self.positions.pop(card_id, None)
        self.wires = [w for w in self.wires if w["from"].split(".")[0] not in gone and w["to"].split(".")[0] not in gone]
        if drop_data:
            await self._drop_data(set(gone))
        self.save()
        self.core.notify("setup", domain=self.id)

    async def remake(self, card_id: str) -> Card | None:
        """The card made again from its saved setup (a newer version of its file: settings upgraded, data kept)."""
        return await self._make(card_id, self._specs[card_id]) if card_id in self._specs else None

    async def update_card(self, card_id: str, title: str | None = None, settings: dict[str, Any] | None = None) -> Card:
        """New name or settings: the card is made again with the same data."""
        spec = self._specs[card_id]
        self._remember()
        if title is not None:
            spec["title"] = title.strip() or spec["title"]
        if settings is not None:
            spec["settings"] = {**spec.get("settings", {}), **settings}
        card = await self._make(card_id, spec)
        if title is not None and (new := self._wanted(card_id, spec["title"])):
            card = await self._move(card_id, new)
        self.save()
        self.core.notify("setup", domain=self.id)
        return card

    def set_wires(self, wires: list[dict[str, str]]) -> None:
        """The owner's wires (only output → input of the same kind: a read output only into a read input; an input may
        take several kinds, e.g. "audio|clip"; "any" takes every kind)."""
        found = []
        for w in wires:
            a, _, ap = w["from"].partition(".")
            b, _, bp = w["to"].partition(".")
            src, dst = self.cards.get(a), self.cards.get(b)
            if not src or not dst:
                continue
            kind, takes = src.ports()[1].get(ap), dst.ports()[0].get(bp)
            if kind and (takes == "any" or kind in str(takes or "").split("|")) and {"from": w["from"], "to": w["to"]} not in found:
                found.append({"from": w["from"], "to": w["to"]})
        self._remember()
        self.wires = found
        self.save()
        self.core.notify("setup", domain=self.id)

    async def undo_last(self) -> bool:
        """Back to the setup before the last change."""
        if not self.undo:
            return False
        last = self.undo.pop()
        self.redo = self._on_top(self.redo, self._snapshot())
        await self._restore(last)
        return True

    async def redo_last(self) -> bool:
        """Forward again to the setup the last Undo left."""
        if not self.redo:
            return False
        nxt = self.redo.pop()
        self.undo = self._on_top(self.undo, self._snapshot())
        await self._restore(nxt)
        return True

    async def _restore(self, setup: dict[str, Any]) -> None:
        if setup["cards"] == self._specs and setup["wires"] == self.wires:  # only the layout changed (Arrange)
            self.positions = deepcopy(setup.get("positions", self.positions))
            self.save()
            self.core.notify("setup", domain=self.id)
            return
        self._specs = deepcopy(setup["cards"])
        self.wires = deepcopy(setup["wires"])
        for card in list(self.cards.values()):
            await self._end(card)
        self.cards = {}
        for card_id, spec in list(self._specs.items()):  # a card made here may add another (it waits for its turn)
            await self._make(card_id, spec)
        if "positions" in setup:
            self.positions = deepcopy(setup["positions"])
        await self.follow_names()  # a name Undo (or a slot, a file) brought back takes its id along
        self.save()
        self.core.notify("setup", domain=self.id)

    def save_slot(self, slot: str) -> None:
        """Keep the setup (cards, wires, positions) in one of three slots."""
        self._tidy((self.slots.get(slot) or {}).get("cards") or {})
        self.slots[slot] = {**self._snapshot(), "positions": deepcopy(self.positions)}
        self.save()

    async def load_slot(self, slot: str) -> bool:
        """Back to a kept setup (Undo brings the current one back)."""
        if not (setup := self.slots.get(slot)):
            return False
        self._remember()
        await self._restore(setup)
        return True

    def export(self) -> dict[str, Any]:
        """The setup as a file: cards (with settings), wires, positions (the cards' own data stays here)."""
        return {"aac": 2, "domain": self.name, **deepcopy({"cards": self._specs, "wires": self.wires, "positions": self.positions})}

    async def import_setup(self, setup: dict[str, Any]) -> None:
        """A setup from a file replaces this one (Undo brings it back)."""
        if not isinstance(setup.get("cards"), dict) or not isinstance(setup.get("wires"), list):
            raise ValueError("not an AAC setup file")
        self.undo = self._on_top(self.undo, {**self._snapshot(), "positions": deepcopy(self.positions)})
        await self._restore({"cards": setup["cards"], "wires": setup["wires"], "positions": setup.get("positions") or {}})

    # ------------------------------------------------------------------ a card's id is its name
    def _ids_taken(self) -> set[str]:
        """Every card id in the setup, its Undo steps and its slots (a new or moved id meets none of them)."""
        return {i for setup in [{"cards": self._specs}, *self.undo, *self.redo, *self.slots.values()] for i in setup.get("cards") or {}}

    @staticmethod
    def _free(base: str, taken: set[str]) -> str:
        new, n = base, 2
        while new in taken:
            new, n = f"{base}_{n}", n + 1
        return new

    def _wanted(self, card_id: str, title: str) -> str | None:
        """The id a card's name asks for (its slug; a number after it when the name is taken), None: it has it."""
        return None if fits(card_id, title) else self._free(slug(title), self._ids_taken())

    async def follow_names(self) -> None:
        """Every card's id is its name, so a card is called the same everywhere (wires, tags, scripts, its data): an id
        that no longer fits its name moves."""
        moved = False
        for card_id, spec in list(self._specs.items()):
            if new := self._wanted(card_id, spec.get("title") or ""):
                await self._move(card_id, new)
                moved = True
        if moved:
            self.save()
            self.core.notify("setup", domain=self.id)

    @staticmethod
    def _moved_setup(setup: dict[str, Any], old: str, new: str) -> dict[str, Any]:
        """One setup (the current one, an Undo step, a slot) with a card's id moved: its key, wires, positions, and
        what the cards' settings say about it (each card type knows its own)."""
        end = lambda ref: f"{new}{ref[len(old):]}" if ref.startswith(f"{old}.") else ref  # noqa: E731
        def settings(spec: dict[str, Any]) -> dict[str, Any]:
            kind = CARD_TYPES.get(spec.get("type", ""))
            found = dict(spec.get("settings") or {})
            return kind.renamed_settings(found, old, new) if kind else renamed_refs(found, old, new)
        out = {**setup, "cards": {(new if k == old else k): {**spec, "settings": settings(spec)} for k, spec in setup["cards"].items()},
               "wires": [{"from": end(w["from"]), "to": end(w["to"])} for w in setup.get("wires") or []]}
        if "positions" in setup:
            out["positions"] = {(new if k == old else k): v for k, v in setup["positions"].items()}
        return out

    async def _move(self, old: str, new: str) -> Card | None:
        """A card's id moves everywhere: the setup, its Undo steps and slots, its stored data and files, what the other
        cards keep about it (their settings and data), the sidebar's fold, what dotted wires last saw."""
        now = self._moved_setup({"cards": self._specs, "wires": self.wires, "positions": self.positions}, old, new)
        self._specs, self.wires, self.positions = now["cards"], now["wires"], now["positions"]
        self.undo = [self._moved_setup(s, old, new) for s in self.undo]
        self.redo = [self._moved_setup(s, old, new) for s in self.redo]
        self.slots = {k: self._moved_setup(s, old, new) for k, s in self.slots.items()}
        if card := self.cards.pop(old, None):
            await self._end(card)
        store = self._card_store(old)
        if (data := await store.async_load()) is not None:
            await self._card_store(new).async_save(data)
            await store.async_remove()
        self._card_stores.pop(old, None)
        await self.hass.async_add_executor_job(move_folder, self.core.folder(self.id, old), self.core.folder(self.id, new))
        for card_id, other in list(self.cards.items()):  # the others whose settings name it: made again with them
            if any(other.settings.get(k) != v for k, v in (self._specs[card_id].get("settings") or {}).items()):
                await self._make(card_id, self._specs[card_id])
        card = await self._make(new, self._specs[new]) if new in self._specs else None
        for other in list(self.cards.values()):  # stored entries name it (its own too)
            await other.renamed(old, new)
        self.flow.held = {(f"{new}{k[len(old):]}" if k.startswith(f"{old}.") else k): v for k, v in self.flow.held.items()}
        if old in (folds := self.core.data.get("folds") or {}):
            folds[new] = folds.pop(old)
            self.core.save()
        LOGGER.info("AAC: card %s is now %s (its name)", old, new)
        return card

    # ------------------------------------------------------------------ the rest
    def template_values(self) -> dict[str, Any]:
        """Every card's values for templates: {{ <card>.<value> }} (and {{ cards['<card>'].<value> }} for any id)."""
        found: dict[str, Any] = {}
        for card in self.cards.values():
            if type(card).values:
                try:
                    found[card.id] = card.current()
                except Exception:  # noqa: BLE001 - one card's values must not break a template
                    LOGGER.exception("AAC: values of %s", card.id)
        own = {"states", "now", "utcnow", "is_state", "state_attr", "cards", "audio", "languages"}  # HA's and the core's names win
        return {**{k: v for k, v in found.items() if k.isidentifier() and k not in own}, "cards": found,
                "audio": self.core.audio.spoken(), "languages": self.core.languages()}

    def tag_pool(self) -> list[dict[str, str]]:
        """Every tag the cards of this domain write, plus the owner's own (`user.…`)."""
        found = [{"tag": card.tag(t), "card": card.name, "what": what} for card in self.cards.values()
                 for t, what in card.tags.items()]
        return found + [{"tag": t, "card": "You", "what": ""} for t in self.user_tags]

    async def stop(self) -> None:
        """The Send stop button: every card drops what it is doing."""
        for card in list(self.cards.values()):
            await card.stop()

    def summary(self) -> dict[str, Any]:
        """For the panel."""
        return {"id": self.id, "name": self.name, "cards": [c.summary() for c in self.cards.values()],
                "wires": self.wires, "positions": self.positions, "undo": len(self.undo), "redo": len(self.redo),
                "slots": {s: bool(self.slots.get(s)) for s in SLOTS}, "tags": self.tag_pool()}


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

    async def load(self) -> None:
        """Read the core; the first time, build the default domain."""
        self.data = await self._store.async_load() or {}
        first = not self.data
        self.data.setdefault("models", [])
        self.data.setdefault("benches", {})  # the last benchmark of each server's model ("<address> <model>")
        self.data.setdefault("servers", [])
        self.data.setdefault("domains", [])
        self.pool = ModelPool(self.hass, self.data["models"], self.data["benches"], self.data["servers"])
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
    def cards_page_open(self) -> bool:
        """A Cards page is open (live wires are only sent then)."""
        return self._page_until > time.monotonic()

    # ------------------------------------------------------------------ channels (design rule: the core never asks for a card type)
    @callback
    def subscribe(self, channel: str, card: Card, handler: Callable[..., Any]) -> None:
        """A card takes what comes in on a core channel ("audio": satellite audio, "ha_conversation": HA's Assist,
        "ha_calendar": HA's calendar entity). Any card may: a card of your own can replace a built-in one."""
        self._channels.setdefault(channel, []).append((card, handler))

    @callback
    def unsubscribe(self, card: Card) -> None:
        """A card stops taking anything (called when it unloads)."""
        for channel, subs in self._channels.items():
            self._channels[channel] = [(c, h) for c, h in subs if c is not card]

    def subscribers(self, channel: str) -> list[tuple[Card, Callable[..., Any]]]:
        """Who takes a channel (cards of the running domain), in the order they subscribed."""
        return list(self._channels.get(channel, []))

    @callback
    def languages(self) -> list[str]:
        """The home's languages, main first (ISO codes): the owner's list (Audio page), else as HA describes the home."""
        from .languages import from_ha  # noqa: PLC0415

        return list(self.data.get("languages") or from_ha(self.hass.config.language, self.hass.config.country))

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
        LOGGER.info("AAC: domain %s is now %s (its name)", old, new)
        if running:
            await self.switch(new)

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
        dst = Domain(self, new_id, name)
        dst._specs, dst.wires, dst.positions = deepcopy(src._specs), deepcopy(src.wires), deepcopy(src.positions)  # noqa: SLF001
        await dst.save_now()
        for card_id in src._specs:  # noqa: SLF001
            if (data := await src.load_card_data(card_id)) is not None:
                await dst.save_card_data(card_id, data)
        if await self.hass.async_add_executor_job(self.folder(src.id).is_dir):
            await self.hass.async_add_executor_job(shutil.copytree, self.folder(src.id), self.folder(new_id))
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
