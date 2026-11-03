"""The core (docs/v2 page 1): not a card, cannot be removed. It holds the model pool (shared by every domain) and
the domains; one domain runs at a time. A domain is a complete setup: its cards (each owning its data), wires,
positions, Undo history and four save slots (the owner's three and a model's draft), all kept on the server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from copy import deepcopy
from pathlib import Path
import re
import shutil
import time
from traceback import format_exc
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import HassJob, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store

from .calendars import Calendars
from .cards import CARD_TYPES, Card, renamed_refs
from .const import DRAFT, LOGGER, SIGNAL
from .flow import Flow
from .log import DomainLog
from .models import ModelPool
from .context import new_id
from .permissions import LEVELS, kinds_of, rank

UNDO_STEPS = 20
SLOTS = ("1", "2", "3", DRAFT)  # the owner's three, and the draft
KEEP_WAIT = 120  # seconds after a model's switch until the setup before comes back, unless keep came
SWITCH_WAIT = 60  # seconds a switch waits at most for the domain to be idle (the model's answer done)
STORM = 30  # contexts within a second that turn a wire red (AAC's settings change it: a fast model's snapshots must not)


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
        self.red: list[dict[str, Any]] = []  # blocked wires (a storm went through them): {from, to, since, count}
        self.permissions: dict[str, str] = {}  # its sheet: thing → level, the owner's alone (docs/v2 page 6); not set: hidden
        self.requests: list[dict[str, Any]] = []  # cards asking for more: {id, card, name, things {thing: level}, reason, since}
        self.trial: dict[str, Any] | None = None  # a model's switch waiting for keep: {slot, by, back, until, context}
        self._trial_timer: Callable[[], None] | None = None  # cancel
        self.flow = Flow(self)
        self.log = DomainLog(self)
        self._specs: dict[str, dict[str, Any]] = {}
        self.running = False  # its cards' timers and listeners run

    # ------------------------------------------------------------------ storage
    async def load(self) -> None:
        """Read the setup and make its cards."""
        await self.log.load()
        data = await self._store.async_load() or {}
        self._specs = data.get("cards", {})
        self.wires = data.get("wires", [])
        self.positions = data.get("positions", {})
        self.undo = unpacked(data.get("undo", []), {"cards": self._specs, "wires": self.wires})
        self.redo: list[dict[str, Any]] = []  # what Undo took back (until the next change; not kept over a restart)
        self.slots = data.get("slots", {})
        self.user_tags = data.get("user_tags", [])
        self.red = data.get("red", [])
        self.permissions = data.get("permissions", {})
        self.requests = data.get("requests", [])
        self.trial = data.get("trial")
        self.cards = {}
        for card_id, spec in list(self._specs.items()):  # a card made here may add another
            await self._make(card_id, spec)
        await self.follow_names()

    async def start(self) -> None:
        """Run: the cards start their timers and listeners."""
        self.running = True
        for card in list(self.cards.values()):
            await card.start()
        if self.trial:
            self._time_trial()

    async def unload(self) -> None:
        """Another domain runs (or AAC stops): every card ends its timers; Redo is not kept."""
        self.running = False
        if self._trial_timer:
            self._trial_timer()
        self._tidy(i for step in self.redo for i in step["cards"])
        self.redo = []
        for card in list(self.cards.values()):
            await self._end(card)
        await self.log.close()

    async def _end(self, card: Card) -> None:
        """A card stops: its timers end (its unload) and the core channels it subscribed to forget it."""
        await card.unload()
        self.core.unsubscribe(card)

    async def _make(self, card_id: str, spec: dict[str, Any]) -> Card | None:
        kind = CARD_TYPES.get(spec.get("type", ""))
        if kind is None:
            self.log.write("warning", f"Card type {spec.get('type')} is not available: the card is left out", card=card_id, what="missing")
            return None
        before, made_with = None, spec.get("version") or "0"
        if spec.get("version") != kind.version:  # saved by another version of the card's file
            before = dict(spec.get("settings") or {})
            spec["settings"] = kind.upgrade(dict(before), made_with)
            spec["version"] = kind.version
        card = kind(self, card_id, spec.get("title", ""), spec.get("settings"))
        await card.load()
        if before is not None:
            await card.upgrade_data(before, made_with)
        if old := self.cards.get(card_id):
            await self._end(old)
        self.cards[card_id] = card
        if self.running:
            await card.start()
        return card

    def _data(self) -> dict[str, Any]:
        return {"cards": self._specs, "wires": self.wires, "positions": self.positions,
                "undo": packed(self.undo, {"cards": self._specs, "wires": self.wires}), "slots": self.slots, "user_tags": self.user_tags,
                "red": self.red, "permissions": self.permissions, "requests": self.requests,
                "trial": {k: v for k, v in self.trial.items() if k != "context"} if self.trial else None}

    # ------------------------------------------------------------------ permissions (docs/v2 page 6)
    def allows(self, thing: str) -> str:
        """How far this domain lets its cards reach a thing (its sheet; not on it: hidden)."""
        return self.permissions.get(thing) or "hidden"

    async def set_permissions(self, levels: dict[str, str]) -> None:
        """The owner changes the sheet (hidden takes a thing off it). Not part of the setup: Undo, the slots and a file
        loaded into the domain leave it as it is. Every card is made again with it."""
        for thing, level in levels.items():
            if level not in LEVELS:
                raise ValueError(f"no level {level}")
            if level == "hidden":
                self.permissions.pop(thing, None)
            else:
                self.permissions[thing] = level
        self.log.write("info", f"Permissions changed: {', '.join(f'{t} {l}' for t, l in levels.items())}"[:500], what="permissions")
        self.save()
        for card_id, spec in list(self._specs.items()):  # what a card set up at its start (listeners) follows the sheet
            if self.cards.get(card_id) is not None and self.cards[card_id].needs:
                await self._make(card_id, spec)
        self.core.notify("setup", domain=self.id)

    def request(self, card: Card, levels: dict[str, str], reason: str) -> dict[str, Any]:
        """A card asks the owner for more (its kinds of things only; only what the domain lets it know exists; not what
        it has already). Waits here until the owner decides (decide)."""
        from .asks import asked_for, request_note  # noqa: PLC0415

        wanted, refused = {}, []
        for thing, level in asked_for(levels).items():
            if kinds_of(thing) not in card.needs or self.allows(thing) == "hidden":
                refused.append(thing)  # unknown to it: as if there were no such thing
            elif rank(card.level(thing)) < rank(level):
                wanted[thing] = level
        if not wanted:
            return {"asked": {}, "unknown": refused, "note": "nothing to ask for (it has that already, or there is no such thing)"}
        req = {"id": new_id(), "card": card.id, "name": card.name, "things": wanted, "reason": reason.strip()[:500] or "(no reason given)",
               "since": time.time()}
        self.requests.append(req)
        self.log.write("info", f"Asks for {', '.join(f'{t} {l}' for t, l in wanted.items())}: {req['reason']}"[:500], card=card.id, what="request")
        request_note(self.hass, self, req)
        self.save()
        self.core.notify("requests", domain=self.id)
        return {"asked": wanted, "unknown": refused, "note": "the owner decides; until then nothing changes"}

    async def decide(self, request_id: str, allowed: list[str]) -> dict[str, str]:
        """The owner's answer to a request: the things allowed (all, some, none) are the card's at the level asked
        (the domain's sheet raised where it was lower: it is the owner who says so). Returns what was granted."""
        from .asks import request_done  # noqa: PLC0415

        req = next((r for r in self.requests if r["id"] == request_id), None)
        if req is None:
            raise ValueError("no such request (decided already?)")
        self.requests.remove(req)
        granted = {t: lvl for t, lvl in req["things"].items() if t in allowed}
        if granted and (card := self.cards.get(req["card"])) is not None:
            await self.set_permissions({t: lvl for t, lvl in granted.items() if rank(self.allows(t)) < rank(lvl)})
            spec = self._specs[card.id]
            spec["settings"] = {**spec.get("settings", {}), "access": {**(spec.get("settings") or {}).get("access", {}), **granted}}
            await self._make(card.id, spec)
        self.log.write("info", f"Request of {req['name']}: allowed {', '.join(granted) or 'nothing'}", card=req["card"], what="request")
        request_done(self.hass, request_id)
        self.save()
        self.core.notify("requests", domain=self.id)
        self.core.notify("setup", domain=self.id)
        return granted

    # ------------------------------------------------------------------ red wires (a storm stop)
    def is_red(self, a: str, b: str) -> bool:
        """A wire (output `card.port` → input `card.port`) is blocked."""
        return any(r["from"] == a and r["to"] == b for r in self.red)

    def redden(self, a: str, b: str, count: int, seconds: float) -> None:
        """A storm went through a wire: it is blocked (red on the Cards page) until the owner, or a model through the
        Card editor, unblocks it. The owner gets a Home Assistant notification."""
        if self.is_red(a, b):
            return
        self.red.append({"from": a, "to": b, "since": time.time(), "count": count})
        text = (f"More than {count} contexts went through the wire {a} → {b} within {seconds:g} s (the wiring loops?). It is "
                "blocked now, red on the Cards page, until you unblock it there (click the wire) or a model does with the "
                "Card editor.")
        self.log.write("error", f"Storm stop: {text}", card=a.split(".")[0], what="red")
        self.save()
        self.core.notify("setup", domain=self.id)
        try:
            persistent_notification.async_create(self.hass, text, title=f"AAC ({self.name}): a wire was blocked",
                                                 notification_id=self._red_note(a, b))
        except Exception as err:  # noqa: BLE001 - the block counts, the notification is extra
            self.log.write("warning", f"No Home Assistant notification: {err!r}", what="red")

    def unblock(self, a: str, b: str, by: str = "you") -> bool:
        """A red wire carries again."""
        if not self.is_red(a, b):
            return False
        self.red = [r for r in self.red if not (r["from"] == a and r["to"] == b)]
        self.log.write("info", f"The wire {a} → {b} was unblocked ({by})", card=a.split(".")[0], what="red")
        with contextlib.suppress(Exception):
            persistent_notification.async_dismiss(self.hass, self._red_note(a, b))
        self.save()
        self.core.notify("setup", domain=self.id)
        return True

    def _unred_gone(self) -> None:
        """A wire that is gone is no longer red."""
        for r in [r for r in self.red if {"from": r["from"], "to": r["to"]} not in self.wires]:
            self.unblock(r["from"], r["to"], "the wire was removed")

    def _red_note(self, a: str, b: str) -> str:
        return f"aac_red_{self.id}_{slug(a)}_{slug(b)}"

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
            self.core.task(self._drop_data(found, only_unused=True), eager=False)

    async def _drop_data(self, card_ids: set[str], only_unused: bool = False) -> None:
        """Cards' stored data and files go (only_unused: of those no setup knows any more)."""
        for card_id in card_ids - (self._ids_taken() if only_unused else set()):
            await self._card_store(card_id).async_remove()
            self._card_stores.pop(card_id, None)
            await self.core.blocking(shutil.rmtree, self.core.folder(self.id, card_id), True)

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
        self.log.write("info", f"Added ({CARD_TYPES[card_type].title})", card=new, what="added")
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
        self._unred_gone()
        if drop_data:
            await self._drop_data(set(gone))
        for card_id in gone:
            self.log.write("info", "Removed" + (" with its data" if drop_data else ""), card=card_id, what="removed")
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
            changed = [k for k, v in settings.items() if (spec.get("settings") or {}).get(k) != v]
            spec["settings"] = {**spec.get("settings", {}), **settings}
            if changed:
                self.log.write("info", f"Settings changed: {', '.join(changed)}", card=card_id, what="settings")
        card = await self._make(card_id, spec)
        if title is not None and (new := self._wanted(card_id, spec["title"])):
            card = await self._move(card_id, new)
        self.save()
        self.core.notify("setup", domain=self.id)
        return card

    def set_wires(self, wires: list[dict[str, str]]) -> None:
        """The owner's wires (only output → input of the same kind: a read output only into a read input; an input may
        take several kinds, e.g. "audio|clip"; "any" takes every kind)."""
        found = self._fit(wires, self._ports("now"))
        self._remember()
        if found != self.wires:
            self.log.write("info", f"Wires changed ({len(found)} now)", what="wires")
        self.wires = found
        self._unred_gone()
        self.save()
        self.core.notify("setup", domain=self.id)

    async def undo_last(self) -> bool:
        """Back to the setup before the last change."""
        if not self.undo:
            return False
        last = self.undo.pop()
        self.redo = self._on_top(self.redo, self._snapshot())
        self.log.write("info", "Undo", what="undo")
        await self._restore(last)
        return True

    async def redo_last(self) -> bool:
        """Forward again to the setup the last Undo left."""
        if not self.redo:
            return False
        nxt = self.redo.pop()
        self.undo = self._on_top(self.undo, self._snapshot())
        self.log.write("info", "Redo", what="redo")
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
        self._unred_gone()
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

    def save_slot(self, slot: str, source: str = "now") -> None:
        """Keep a setup (cards, wires, positions: the running one, or another slot's) in a slot."""
        setup = self.place(source)
        self._tidy((self.slots.get(slot) or {}).get("cards") or {})
        self.slots[slot] = setup
        self.save()
        self.core.notify("setup", domain=self.id)

    # ------------------------------------------------------------------ building in a slot, a model's switch
    def place(self, where: str) -> dict[str, Any]:
        """A setup to look at or build in: "now" (the running one) or a slot (none kept: empty)."""
        if where == "now":
            return {**self._snapshot(), "positions": deepcopy(self.positions)}
        if where not in SLOTS:
            raise ValueError(f"no setup {where!r}: now, or a slot {', '.join(SLOTS)}")
        return deepcopy(self.slots.get(where) or {"cards": {}, "wires": [], "positions": {}})

    def _ports(self, where: str, cards: dict[str, Any] | None = None) -> Callable[[str], tuple[dict, dict] | None]:
        """A card's ports in a setup: a running card's own, else what its type has with its settings (None: no such card
        or type)."""
        def ports(card_id: str) -> tuple[dict, dict] | None:
            if where == "now":
                return made.ports() if (made := self.cards.get(card_id)) else None
            spec = (cards or {}).get(card_id)
            if spec is None or (kind := CARD_TYPES.get(spec.get("type", ""))) is None:
                return None
            if (made := self.cards.get(card_id)) is not None and self._specs.get(card_id) == spec:
                return made.ports()
            return kind.ports_for({k: f.get("default") for k, f in kind.settings_schema.items()} | dict(spec.get("settings") or {}))
        return ports

    @staticmethod
    def _fit(wires: list[dict[str, str]], ports: Callable[[str], tuple[dict, dict] | None]) -> list[dict[str, str]]:
        """The wires that fit: output → input of the same kind (a read output only into a read input; an input may take
        several kinds, e.g. "audio|clip"; "any" takes every kind), each once."""
        found = []
        for w in wires:
            a, _, ap = str(w.get("from", "")).partition(".")
            b, _, bp = str(w.get("to", "")).partition(".")
            src, dst = ports(a), ports(b)
            if not src or not dst:
                continue
            kind, takes = src[1].get(ap), dst[0].get(bp)
            if kind and (takes == "any" or kind in str(takes or "").split("|")) and {"from": w["from"], "to": w["to"]} not in found:
                found.append({"from": w["from"], "to": w["to"]})
        return found

    def wire_problem(self, where: str, wire: dict[str, str]) -> str:
        """Why a wire does not fit in a setup ("" when it does)."""
        cards = self.place(where)["cards"]
        ports = self._ports(where, cards)
        if self._fit([wire], ports):
            return ""
        a, _, ap = str(wire.get("from", "")).partition(".")
        b, _, bp = str(wire.get("to", "")).partition(".")
        here = "what runs" if where == "now" else f"slot {where}"
        for card_id in (a, b):
            if ports(card_id) is None:
                runs = where != "now" and card_id in self._specs  # in what runs, not in this slot
                return (f"there is no card {card_id!r} in {here} (it holds: {', '.join(cards) or 'nothing'})"
                        + (f"; {card_id} is in what runs: copy from now to {where} first to build on what runs" if runs else ""))
        (_, outs), (ins, _) = ports(a), ports(b)
        if ap not in outs:
            return f"{a} has no output {ap!r} (its outputs: {', '.join(outs) or 'none'})"
        if bp not in ins:
            return f"{b} has no input {bp!r} (its inputs: {', '.join(ins) or 'none'})"
        return f"{a}.{ap} carries {outs[ap]}, {b}.{bp} takes {ins[bp]}"

    def build(self, slot: str, change: Callable[[dict[str, Any]], Any]) -> Any:
        """A change to a kept setup (it does not run: nothing is made); returns what the change returns."""
        if slot == "now":
            raise ValueError("build changes a slot; the running setup changes card by card")
        setup = self.place(slot)
        before = set(setup["cards"])
        result = change(setup)
        self.slots[slot] = setup
        self._tidy(before - set(setup["cards"]))
        self.save()
        self.core.notify("setup", domain=self.id)
        return result

    def slot_add(self, slot: str, card_type: str, title: str = "", settings: dict[str, Any] | None = None) -> str:
        """A new card in a slot; its id (its name, free in every setup)."""
        if card_type not in CARD_TYPES:
            raise ValueError(f"No card type {card_type}")
        title = title or CARD_TYPES[card_type].title
        def add(setup: dict[str, Any]) -> str:
            new = self._free(slug(title), self._ids_taken() | set(setup["cards"]))
            setup["cards"][new] = {"type": card_type, "title": title, "settings": dict(settings or {})}
            return new
        return self.build(slot, add)

    def slot_update(self, slot: str, card_id: str, title: str | None = None, settings: dict[str, Any] | None = None) -> None:
        """A card's name or settings in a slot (its id follows its name once the slot runs)."""
        def change(setup: dict[str, Any]) -> None:
            spec = setup["cards"][card_id]
            if title is not None and title.strip():
                spec["title"] = title.strip()
            if settings is not None:
                spec["settings"] = {**(spec.get("settings") or {}), **settings}
        self.build(slot, change)

    def slot_remove(self, slot: str, card_id: str) -> None:
        """A card out of a slot, with its wires."""
        def remove(setup: dict[str, Any]) -> None:
            setup["cards"].pop(card_id, None)
            (setup.get("positions") or {}).pop(card_id, None)
            setup["wires"] = [w for w in setup["wires"] if card_id not in (w["from"].split(".")[0], w["to"].split(".")[0])]
        self.build(slot, remove)

    def slot_wires(self, slot: str, wires: list[dict[str, str]]) -> list[dict[str, str]]:
        """A slot's wires, all of them (only fitting ones); returns them as they are now."""
        def change(setup: dict[str, Any]) -> list[dict[str, str]]:
            setup["wires"] = self._fit(wires, self._ports(slot, setup["cards"]))
            return setup["wires"]
        return self.build(slot, change)

    def problems(self, where: str, keeper: str | None = None) -> list[str]:
        """What is wrong with a setup before it runs: types that do not exist, wires that fit nothing, settings a type
        does not have; keeper: the card that switches to it must be wired from a model and back there (else no keep)."""
        setup = self.place(where)
        cards, found = setup["cards"], []
        for card_id, spec in cards.items():
            if (kind := CARD_TYPES.get(spec.get("type", ""))) is None:
                found.append(f"{card_id}: there is no card type {spec.get('type')!r} (see types)")
            elif extra := [k for k in spec.get("settings") or {} if k not in kind.settings_schema]:
                found.append(f"{card_id}: a {kind.title} card has no setting {', '.join(extra)} (see help {kind.type})")
        fits = self._fit(setup["wires"], self._ports(where, cards))
        found += [f"the wire {w['from']} -> {w['to']} does not fit: {self.wire_problem(where, w)}"
                  for w in setup["wires"] if {"from": w["from"], "to": w["to"]} not in fits]
        if keeper and not (any(w["to"].startswith(f"{keeper}.") for w in fits) and any(w["from"].startswith(f"{keeper}.") for w in fits)):
            found.append(f"{keeper} (the card that switches) is not wired in and out there: no keep could come, the setup "
                         "before would come back")
        return found

    async def switch_after(self, slot: str, by: str, call_id: str | None) -> None:
        """A model's switch to a slot, once the domain is idle (its answer written: no card busy for a second; at most
        SWITCH_WAIT). The conversation that asked for it goes along (the newest context holding the call): the card
        that switched hands it on in the new setup, and keep must come within KEEP_WAIT, else the setup before comes back."""
        await asyncio.sleep(1)
        quiet, waited = 0, 0.0
        while quiet < 2 and waited < SWITCH_WAIT:
            quiet = 0 if any(c.busy for c in self.cards.values()) else quiet + 1
            await asyncio.sleep(0.5)
            waited += 0.5
        held = [c for c in self.flow.held.values() if call_id and any(call_id in [x.get("id") for x in e["data"].get("calls") or []] for e in c.entries)]
        context = max(held, key=lambda c: len(c.entries)).to_dict() if held else None
        try:
            await self.switch_to(slot, by, context)
        except ValueError as err:
            self.log.write("warning", f"Not switched to slot {slot}: {err}", card=by, what="switch")

    async def switch_to(self, slot: str, by: str, context: dict[str, Any] | None = None) -> None:
        """Run a slot now, on trial: keep within KEEP_WAIT, else the setup before comes back (the slot stays as it is)."""
        if self.trial:
            raise ValueError("a switch waits for keep already")
        setup = self.place(slot)
        if not setup["cards"]:
            raise ValueError(f"slot {slot} is empty")
        self.trial = {"slot": slot, "by": by, "back": self.place("now"), "until": time.time() + KEEP_WAIT, "context": context}
        self._remember()
        self.log.write("info", f"Switched to slot {slot}: keep within {KEEP_WAIT} s, else the setup before comes back",
                       card=by, what="switch")
        await self._restore(setup)
        self._time_trial()

    def _time_trial(self) -> None:
        if self._trial_timer:
            self._trial_timer()
        later = max(0.0, self.trial["until"] - time.time())
        self._trial_timer = self.core.later(later, self.go_back)

    def keep(self, by: str = "you") -> bool:
        """The switch stays."""
        if not self.trial:
            return False
        if self._trial_timer:
            self._trial_timer()
        self.log.write("info", f"Kept slot {self.trial['slot']} (by {by})", what="switch")
        self.trial = None
        self.save()
        self.core.notify("setup", domain=self.id)
        return True

    async def go_back(self, by: str = "") -> bool:
        """The setup before the switch comes back (no keep in time, or the owner said so); Undo goes to the switched one."""
        if not (trial := self.trial):
            return False
        if self._trial_timer:
            self._trial_timer()
        self.trial = None
        why = f"{by} took the switch back" if by else f"No keep within {KEEP_WAIT} s after the switch to slot {trial['slot']}"
        self._remember()
        self.log.write("warning", f"{why}: the setup before it runs again", card=trial["by"], what="switch")
        await self._restore(trial["back"])
        if not by:
            with contextlib.suppress(Exception):
                persistent_notification.async_create(self.hass, f"{why}: the setup before it runs again (slot {trial['slot']} "
                                                     "stays as it was, Undo goes to it).", title=f"AAC ({self.name}): a switch went back")
        return True

    async def load_slot(self, slot: str) -> bool:
        """Back to a kept setup (Undo brings the current one back)."""
        if not (setup := self.slots.get(slot)):
            return False
        self._remember()
        self.log.write("info", f"Slot {slot} loaded", what="slot")
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
        self.log.write("info", f"Loaded from a file ({setup.get('domain') or 'no name'})", what="file")
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
        await self.core.blocking(move_folder, self.core.folder(self.id, old), self.core.folder(self.id, new))
        for card_id, other in list(self.cards.items()):  # the others whose settings name it: made again with them
            if any(other.settings.get(k) != v for k, v in (self._specs[card_id].get("settings") or {}).items()):
                await self._make(card_id, self._specs[card_id])
        card = await self._make(new, self._specs[new]) if new in self._specs else None
        for other in list(self.cards.values()):  # stored entries name it (its own too)
            await other.renamed(old, new)
        self.flow.held = {(f"{new}{k[len(old):]}" if k.startswith(f"{old}.") else k): v for k, v in self.flow.held.items()}
        self.log.renamed(old, new)
        self.log.write("info", f"Renamed: {old} is now {new} (its name)", card=new, what="renamed")
        if old in (folds := self.core.data.get("folds") or {}):
            folds[new] = folds.pop(old)
            self.core.save()
        return card

    # ------------------------------------------------------------------ the rest
    def template_values(self) -> dict[str, Any]:
        """Every card's values for templates: {{ <card>.<value> }} (and {{ cards['<card>'].<value> }} for any id)."""
        found: dict[str, Any] = {}
        for card in self.cards.values():
            if type(card).values:
                try:
                    found[card.id] = card.current()
                except Exception as err:  # noqa: BLE001 - one card's values must not break a template
                    self.log.write("error", f"Its values failed: {err!r}", card=card.id, what="failed", trace=format_exc()[-3000:])
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
                "slots": {s: bool(self.slots.get(s)) for s in SLOTS}, "tags": self.tag_pool(), "red": self.red,
                "trial": {"slot": self.trial["slot"], "by": self.trial["by"], "left": round(self.trial["until"] - time.time())} if self.trial else None}


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
            await self.hass.async_add_executor_job(lambda: dst.log.path.unlink(missing_ok=True))  # a copy starts its own log
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
