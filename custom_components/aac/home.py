"""Home Assistant for the card layer (docs/v2 page 6): the core's side of `Card.ha`. It answers in plain data (an
entity's state as a dict), so it can answer over the worker's channel too, and it holds every card to the domain's
sheet (the owner's cap): what is hidden there does not exist, an action needs ask or full there.

The card's side (`permissions.Home`) holds each card to its own May use within that, and asks the owner at ask.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .permissions import THINGS, rank

if TYPE_CHECKING:
    from .core import Core


def plain(state: State | None) -> dict[str, Any] | None:
    """A state as plain data: entity_id, state, attributes, last_changed."""
    if state is None:
        return None
    return {"entity_id": state.entity_id, "state": state.state,
            "attributes": json.loads(json.dumps(dict(state.attributes), default=str)), "last_changed": state.last_changed.isoformat()}


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


class HomeService:
    """Home Assistant, as far as the running domain's sheet lets its cards reach it."""

    def __init__(self, core: Core) -> None:
        self.core, self.hass = core, core.hass

    def _allows(self, thing: str, level: str) -> bool:
        return self.core.domain is not None and rank(self.core.domain.allows(thing)) >= rank(level)

    async def entities(self) -> list[dict[str, Any]]:
        """Every entity the domain lets exist: {entity_id, name, area, state (when it may be read)}."""
        return [{"entity_id": st.entity_id, "name": st.attributes.get("friendly_name") or st.entity_id,
                 "area": area(self.hass, st.entity_id), **({"state": st.state} if self._allows(st.entity_id, "read") else {})}
                for st in self.hass.states.async_all() if self._allows(st.entity_id, "list")]

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        """An entity's state (None: there is none, or the domain lets it not be read)."""
        return plain(self.hass.states.get(entity_id)) if self._allows(entity_id, "read") else None

    async def call(self, domain: str, service: str, entity_id: str, data: dict[str, Any]) -> None:
        """A service on one entity (the card's side checked what it may and asked the owner)."""
        if not self._allows(entity_id, "ask"):
            raise PermissionError(f"the domain does not let {entity_id} be switched")
        await self.hass.services.async_call(domain, service, {**data, "entity_id": entity_id}, blocking=True)

    def watch(self, entity_ids: list[str], handler: Callable[[dict[str, Any]], Any]) -> Callable[[], None]:
        """handler({entity_id, old, new}) on every change of those the domain lets be read; returns: cancel."""
        allowed = [e for e in entity_ids if self._allows(e, "read")]

        @callback
        def changed(event: Event) -> None:
            handler({"entity_id": event.data.get("entity_id"), "old": plain(event.data.get("old_state")),
                     "new": plain(event.data.get("new_state"))})
        return async_track_state_change_event(self.hass, allowed, changed) if allowed else (lambda: None)

    async def calendar_events(self, entity_id: str, start: str, end: str) -> list[dict[str, Any]]:
        """The events of a calendar entity between two times."""
        if not self._allows(entity_id, "read"):
            raise PermissionError(f"the domain does not let {entity_id} be read")
        got = await self.hass.services.async_call("calendar", "get_events", {"entity_id": entity_id, "start_date_time": start,
                                                  "end_date_time": end}, blocking=True, return_response=True)
        return ((got or {}).get(entity_id) or {}).get("events") or []

    async def catalog(self, kinds: list[str]) -> list[dict[str, Any]]:
        """What a card's May use can be about (kinds: "entities" and names of THINGS), each with the domain's level."""
        return [{**x, "cap": cap} for x in catalog(self.hass, set(kinds))
                if self.core.domain is not None and (cap := self.core.domain.allows(x["id"])) != "hidden"]
