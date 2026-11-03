"""A domain (docs/v2 page 1): one complete setup of cards and wires, with its positions, Undo history, four save slots
(the owner's three and a model's draft), its permission sheet and its log. Part of the card layer: it reaches the world
only through the core's services (`self.core`: storage, notifications, the data folder, time and tasks), so the card
worker runs it as it is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
import re
import time
from traceback import format_exc
from typing import TYPE_CHECKING, Any

from .cards import CARD_TYPES, Card, renamed_refs
from .const import DRAFT
from .context import new_id
from .flow import Flow
from .log import DomainLog
from .permissions import LEVELS, kinds_of, rank

if TYPE_CHECKING:
    from .core import Core

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


class Domain:
    """One setup of cards and wires."""

    def __init__(self, core: Core, domain_id: str, name: str) -> None:
        """Initialize."""
        self.core, self.id, self.name = core, domain_id, name
        self._store = core.store(f"aac2.domain.{domain_id}")
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
        request_note(self.core, self, req)
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
        request_done(self.core, request_id)
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
        self.core.note(self._red_note(a, b), f"AAC ({self.name}): a wire was blocked", text)

    def unblock(self, a: str, b: str, by: str = "you") -> bool:
        """A red wire carries again."""
        if not self.is_red(a, b):
            return False
        self.red = [r for r in self.red if not (r["from"] == a and r["to"] == b)]
        self.log.write("info", f"The wire {a} → {b} was unblocked ({by})", card=a.split(".")[0], what="red")
        self.core.unnote(self._red_note(a, b))
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
            self._card_stores[card_id] = self.core.store(f"aac2.card.{self.id}.{card_id}")
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
            await self.core.services.data_remove([self.id, card_id])

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
            self.core.note(f"aac_switch_{self.id}", f"AAC ({self.name}): a switch went back",
                           f"{why}: the setup before it runs again (slot {trial['slot']} stays as it was, Undo goes to it).")
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
        await self.core.services.data_move([self.id, old], [self.id, new])
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
