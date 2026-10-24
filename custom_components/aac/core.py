"""The core (docs/v2 page 1): not a card, cannot be removed. It holds the model pool (shared by every domain) and
the domains; one domain runs at a time. A domain is a complete setup: its cards (each owning its data), wires,
positions, Undo history and three save slots, all kept on the server.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import re
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .cards import CARD_TYPES, Card
from .const import LOGGER, SIGNAL
from .flow import Flow
from .models import ModelPool

UNDO_STEPS = 20
SLOTS = ("1", "2", "3")


def slug(text: str) -> str:
    """An id from a name."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "card"


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
        self.undo = data.get("undo", [])
        self.slots = data.get("slots", {})
        self.user_tags = data.get("user_tags", [])
        self.cards = {}
        for card_id, spec in self._specs.items():
            await self._make(card_id, spec)

    async def start(self) -> None:
        """Run: the cards start their timers and listeners."""
        self.running = True
        for card in list(self.cards.values()):
            await card.start()

    async def unload(self) -> None:
        """Another domain runs (or AAC stops): every card ends its timers."""
        self.running = False
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
        return {"cards": self._specs, "wires": self.wires, "positions": self.positions, "undo": self.undo[-UNDO_STEPS:],
                "slots": self.slots, "user_tags": self.user_tags}

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
        self.undo = [*self.undo, self._snapshot()][-UNDO_STEPS:]

    async def add_card(self, card_type: str, title: str = "", settings: dict[str, Any] | None = None,
                       at: dict[str, float] | None = None, card_id: str | None = None) -> Card:
        """A new card."""
        if card_type not in CARD_TYPES:
            raise ValueError(f"No card type {card_type}")
        title = title or CARD_TYPES[card_type].title
        base = card_id or slug(title)
        new, n = base, 2
        while new in self._specs:
            new, n = f"{base}_{n}", n + 1
        self._remember()
        self._specs[new] = {"type": card_type, "title": title, "settings": dict(settings or {})}
        card = await self._make(new, self._specs[new])
        if at:
            self.positions[new] = {"x": at.get("x", 0), "y": at.get("y", 0)}
        self.save()
        self.core.notify("setup", domain=self.id)
        return card

    async def remove_card(self, card_id: str) -> None:
        """Take a card out (its data stays stored until the card comes back or the domain is cleared)."""
        if card_id not in self._specs:
            return
        self._remember()
        self._specs.pop(card_id)
        if card := self.cards.pop(card_id, None):
            await self._end(card)
        self.wires = [w for w in self.wires if w["from"].split(".")[0] != card_id and w["to"].split(".")[0] != card_id]
        self.positions.pop(card_id, None)
        self.save()
        self.core.notify("setup", domain=self.id)

    async def update_card(self, card_id: str, title: str | None = None, settings: dict[str, Any] | None = None) -> Card:
        """New name or settings: the card is made again with the same data."""
        spec = self._specs[card_id]
        self._remember()
        if title is not None:
            spec["title"] = title.strip() or spec["title"]
        if settings is not None:
            spec["settings"] = {**spec.get("settings", {}), **settings}
        card = await self._make(card_id, spec)
        self.save()
        self.core.notify("setup", domain=self.id)
        return card

    def set_wires(self, wires: list[dict[str, str]]) -> None:
        """The owner's wires (only output → input of the same kind)."""
        found = []
        for w in wires:
            a, _, ap = w["from"].partition(".")
            b, _, bp = w["to"].partition(".")
            src, dst = self.cards.get(a), self.cards.get(b)
            if not src or not dst:
                continue
            kind, takes = src.ports()[1].get(ap), dst.ports()[0].get(bp)
            if kind and (kind == takes or (takes == "read" and kind == "text")) and {"from": w["from"], "to": w["to"]} not in found:
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
        await self._restore(last)
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
        for card_id, spec in self._specs.items():
            await self._make(card_id, spec)
        if "positions" in setup:
            self.positions = deepcopy(setup["positions"])
        self.save()
        self.core.notify("setup", domain=self.id)

    def save_slot(self, slot: str) -> None:
        """Keep the setup (cards, wires, positions) in one of three slots."""
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
        self.undo = [*self.undo, {**self._snapshot(), "positions": deepcopy(self.positions)}][-UNDO_STEPS:]
        await self._restore({"cards": setup["cards"], "wires": setup["wires"], "positions": setup.get("positions") or {}})

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
        own = {"states", "now", "utcnow", "is_state", "state_attr", "cards", "audio"}  # HA's and the core's names win
        return {**{k: v for k, v in found.items() if k.isidentifier() and k not in own}, "cards": found,
                "audio": self.core.audio.spoken()}

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
                "wires": self.wires, "positions": self.positions, "undo": len(self.undo),
                "slots": {s: bool(self.slots.get(s)) for s in SLOTS}, "tags": self.tag_pool()}


class Core:
    """AAC's core: the model pool and the domains."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self._store: Store = Store(hass, 1, "aac2.core")
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
        self.data.setdefault("domains", [])
        self.pool = ModelPool(self.hass, self.data["models"])
        if first:
            from .defaults import async_first_start  # noqa: PLC0415
            await async_first_start(self)
        if not self.data["domains"]:
            self.data["domains"] = [{"id": "assistant", "name": "Assistant"}]
        self.data.setdefault("active", self.data["domains"][0]["id"])
        await self.switch(self.data["active"])
        from .defaults import async_voice_chain  # noqa: PLC0415
        await async_voice_chain(self)
        for done in ("v1_audio", "v1_triggers", "voice_v2", "assist_card", "calendar_card"):  # flags of steps that are gone
            self.data.pop(done, None)

    def save(self) -> None:
        """Keep the core."""
        self._store.async_delay_save(lambda: self.data, 1)

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

    async def clone(self, name: str) -> str:
        """A copy of the running domain (cards, wires, their data) to try things out."""
        src = self.domain
        new_id, n = slug(name) or "domain", 2
        while any(d["id"] == new_id for d in self.data["domains"]):
            new_id, n = f"{slug(name)}_{n}", n + 1
        await src.save_now()
        dst = Domain(self, new_id, name)
        dst._specs, dst.wires, dst.positions = deepcopy(src._specs), deepcopy(src.wires), deepcopy(src.positions)  # noqa: SLF001
        await dst.save_now()
        for card_id in src._specs:  # noqa: SLF001
            if (data := await src.load_card_data(card_id)) is not None:
                await dst.save_card_data(card_id, data)
        self.data["domains"].append({"id": new_id, "name": name})
        self.save()
        return new_id

    # ------------------------------------------------------------------ models
    def save_model(self, model: dict[str, Any]) -> dict[str, Any]:
        """Add or change a model in the pool."""
        old = self.pool.get(model.get("id", ""))
        if old:
            key = model.get("api_key") or old.get("api_key")
            old.update({k: v for k, v in model.items() if k != "api_key"}, api_key=key)
            return old
        model = {"id": f"m_{slug(model['name'])}", **model}
        self.data["models"].append(model)
        self.save()
        return model

    def delete_model(self, model_id: str) -> None:
        """Take a model out of the pool."""
        self.data["models"][:] = [m for m in self.data["models"] if m["id"] != model_id]
        self.save()
