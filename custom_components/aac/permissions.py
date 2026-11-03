"""Permissions (docs/v2 page 6): what lies outside AAC, and how far a card may reach it.

Two layers, the same levels: the domain says per thing what exists for its cards at all (its sheet, set by the owner
only); each card gets what it may use within that (its `access` setting). What a card can use is the lower of the two.
A thing is a Home Assistant entity (by its id) or one of THINGS. Levels, lowest first: hidden (does not exist), list
(cards may know it exists and ask for it), read, ask (full, but every action waits for the owner's yes), full. Not on
the domain's sheet: hidden; not on a card's: list (it knows what the domain lets exist, and can ask).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cards import Card

LEVELS = ("hidden", "list", "read", "ask", "full")
GENERIC = {"turn_on", "turn_off", "toggle"}  # homeassistant.<these> act on any entity
TARGETS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")  # what a service may act on besides its entity
THINGS = {  # the things outside AAC besides Home Assistant's entities: what read and full let a card do
    "web": {"title": "The web", "read": "search and fetch pages", "full": "the same"},
    "notify": {"title": "Notifications", "read": "nothing yet", "full": "send notifications (Home Assistant's notify services)"},
    "files": {"title": "Files", "read": "read the files in <config>/aac/files", "full": "write them too"},
    "servers": {"title": "Model servers", "read": "see what they offer", "full": "load, unload and download models"},
    "log": {"title": "AAC's log", "read": "read this domain's log (what passed the wires, what went wrong)", "full": "the same"},
    "setup": {"title": "The setup", "read": "see this domain's cards and wires", "full": "add, change and wire cards"},
    "audio": {"title": "Audio", "read": "hear the satellites' microphones", "full": "play on the satellites' speakers too"},
}


def rank(level: str | None) -> int:
    """A level's place (anything unknown: hidden)."""
    return LEVELS.index(level) if level in LEVELS else 0


def lower(a: str | None, b: str | None) -> str:
    """The lower of two levels."""
    return LEVELS[min(rank(a), rank(b))]


def is_entity(thing: str) -> bool:
    """An entity id (`light.kitchen`); the other things have no dot."""
    return "." in thing


def kinds_of(thing: str) -> str:
    """Which kind of thing it is: "entities" or the thing itself."""
    return "entities" if is_entity(thing) else thing


class Home:
    """Home Assistant's entities as one card may reach them (Card.ha): what is hidden for it does not exist. Its own
    May use is checked here, the domain's sheet by the core's side (home.HomeService), which does the work."""

    def __init__(self, card: Card) -> None:
        self.card, self.service = card, card.core.home

    async def entities(self) -> list[dict[str, Any]]:
        """Every entity it may know: {entity_id, name, area, access}, with the state from read on."""
        found = []
        for e in await self.service.entities():
            level = self.card.level(e["entity_id"])
            if level != "hidden":
                found.append({"entity_id": e["entity_id"], "name": e["name"], "area": e["area"], "access": level,
                              **({"state": e["state"]} if rank(level) >= rank("read") and "state" in e else {})})
        return found

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        """An entity's state {entity_id, state, attributes, last_changed} (None: there is none, or it may not read it)."""
        return await self.service.state(entity_id) if self.card.may(entity_id, "read") else None

    async def call(self, domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None) -> None:
        """A service on one entity (an action on it): a service of the entity's own domain, or homeassistant.turn_on /
        turn_off / toggle; the data cannot name other targets."""
        if domain != entity_id.split(".")[0] and not (domain == "homeassistant" and service in GENERIC):
            raise PermissionError(f"{domain}.{service} is not a service of {entity_id} (only {entity_id.split('.')[0]}.… "
                                  "or homeassistant.turn_on / turn_off / toggle)")
        extra = {k: v for k, v in (data or {}).items() if k not in TARGETS}
        state = await self.service.state(entity_id) if self.card.may(entity_id, "read") else None
        name = ((state or {}).get("attributes") or {}).get("friendly_name") or entity_id
        await self.card.act(entity_id, f"{domain}.{service} on {name}{f' {extra}' if extra else ''}")
        await self.service.call(domain, service, entity_id, extra)

    def watch(self, entity_ids: list[str], handler: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """handler({entity_id, old, new}: states as state gives them, None when there is none) on every change of
        those it may read; returns: cancel."""
        return self.service.watch([e for e in entity_ids if self.card.may(e, "read")], handler)

    async def calendar_events(self, entity_id: str, start: str, end: str) -> list[dict[str, Any]]:
        """The events of a calendar entity between two times (needs it at read)."""
        self.card.need(entity_id, "read")
        return await self.service.calendar_events(entity_id, start, end)
