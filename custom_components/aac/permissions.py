"""Permissions (docs/v2 page 6): what lies outside AAC, and how far a card may reach it.

Two layers, the same levels: the domain says per thing what exists for its cards at all (its sheet, set by the owner
only); each card gets what it may use within that (its `access` setting). What a card can use is the lower of the two.
A thing is a Home Assistant entity (by its id) or one of THINGS. Levels, lowest first: hidden (does not exist), list
(cards may know it exists and ask for it), read, ask (full, but every action waits for the owner's yes), full. Not on
the domain's sheet: hidden; not on a card's: list (it knows what the domain lets exist, and can ask).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, State, callback, is_callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

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


def area(hass: HomeAssistant, entity_id: str) -> str | None:
    """The room of an entity (its own, else its device's)."""
    ent = er.async_get(hass).async_get(entity_id)
    area_id = ent.area_id if ent else None
    if ent and not area_id and ent.device_id and (dev := dr.async_get(hass).async_get(ent.device_id)):
        area_id = dev.area_id
    found = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return found.name if found else None


def catalog(hass: HomeAssistant, kinds: set[str] | None = None) -> list[dict[str, Any]]:
    """Every thing (kinds: only these: "entities" and names of THINGS): {id, name, area, group, help}; the things first,
    then the entities by room and kind."""
    found = [{"id": t, "name": spec["title"], "area": "", "group": "AAC",
              "help": f"read: {spec['read']} · full: {spec['full']}"}
             for t, spec in THINGS.items() if kinds is None or t in kinds]
    if kinds is None or "entities" in kinds:
        found += sorted(({"id": st.entity_id, "name": st.attributes.get("friendly_name") or st.entity_id,
                          "area": area(hass, st.entity_id) or "", "group": st.domain, "help": ""} for st in hass.states.async_all()),
                        key=lambda x: (x["area"] or "~", x["group"], x["name"].lower()))
    return found


def kinds_of(thing: str) -> str:
    """Which kind of thing it is: "entities" or the thing itself."""
    return "entities" if is_entity(thing) else thing


class Home:
    """Home Assistant's entities as one card may reach them (Card.ha): what is hidden for it does not exist."""

    def __init__(self, card: Card) -> None:
        self.card, self.hass = card, card.hass

    def entities(self) -> list[dict[str, Any]]:
        """Every entity it may know: {entity_id, name, area, access}, with the state from read on."""
        found = []
        for st in self.hass.states.async_all():
            level = self.card.level(st.entity_id)
            if level != "hidden":
                found.append({"entity_id": st.entity_id, "name": st.attributes.get("friendly_name") or st.entity_id,
                              "area": area(self.hass, st.entity_id), "access": level,
                              **({"state": st.state} if rank(level) >= rank("read") else {})})
        return found

    def state(self, entity_id: str) -> State | None:
        """An entity's state (None: there is none, or it may not read it)."""
        return self.hass.states.get(entity_id) if self.card.may(entity_id, "read") else None

    async def call(self, domain: str, service: str, entity_id: str, data: dict[str, Any] | None = None) -> None:
        """A service on one entity (an action on it): a service of the entity's own domain, or homeassistant.turn_on /
        turn_off / toggle; the data cannot name other targets."""
        if domain != entity_id.split(".")[0] and not (domain == "homeassistant" and service in GENERIC):
            raise PermissionError(f"{domain}.{service} is not a service of {entity_id} (only {entity_id.split('.')[0]}.… "
                                  "or homeassistant.turn_on / turn_off / toggle)")
        extra = {k: v for k, v in (data or {}).items() if k not in TARGETS}
        name = (state.attributes.get("friendly_name") if (state := self.hass.states.get(entity_id)) else None) or entity_id
        await self.card.act(entity_id, f"{domain}.{service} on {name}{f' {extra}' if extra else ''}")
        await self.hass.services.async_call(domain, service, {**extra, "entity_id": entity_id}, blocking=True)

    def watch(self, entity_ids: list[str], handler: Callable[[Any], Any]) -> Callable[[], None]:
        """handler(event) on every change of those it may read (Home Assistant's state_changed event); returns the
        unsubscribe."""
        allowed = [e for e in entity_ids if self.card.may(e, "read")]
        if not inspect.iscoroutinefunction(handler) and not is_callback(handler):  # on HA's loop (a plain function would
            plain = handler                                                         # run in a worker thread)
            handler = callback(lambda event: plain(event))
        return async_track_state_change_event(self.hass, allowed, handler) if allowed else (lambda: None)

    async def calendar_events(self, entity_id: str, start: str, end: str) -> list[dict[str, Any]]:
        """The events of a calendar entity between two times (needs it at read)."""
        self.card.need(entity_id, "read")
        got = await self.hass.services.async_call("calendar", "get_events", {"entity_id": entity_id, "start_date_time": start,
                                                  "end_date_time": end}, blocking=True, return_response=True)
        return ((got or {}).get(entity_id) or {}).get("events") or []
