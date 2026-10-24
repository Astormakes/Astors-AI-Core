"""Home Assistant: the world through its own access list. Per entity: hidden, read-only (see its state) or full
(switch it too); everything else gets the default. Anyone can build other HA integration cards."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from custom_components.aac.api import Card, Context, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

LEVELS = ["hidden", "read", "full"]
MAX_FOUND = 60


def _area(hass: HomeAssistant, entity_id: str) -> str | None:
    ent = er.async_get(hass).async_get(entity_id)
    area_id = ent.area_id if ent else None
    if ent and not area_id and ent.device_id and (dev := dr.async_get(hass).async_get(ent.device_id)):
        area_id = dev.area_id
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


@register
class HomeAssistantCard(Card):
    """Home Assistant."""

    type = "homeassistant"
    title = "Home Assistant"
    category = "Home Assistant"
    help = ("Home Assistant for a model or a Conditional card: find entities, read their state, switch them; only what "
            "its access list allows (hidden, read-only, full).")
    settings_schema = {
        "default": {"type": "select", "title": "Everything else is", "default": "hidden",
                    "options": [["hidden", "hidden"], ["read", "read-only"], ["full", "full (may switch)"]]},
        "levels": {"type": "access", "title": "Access per entity", "default": {},
                   "help": "hidden: not there for AAC · read-only: it sees the state · full: it may switch it."},
    }
    functions = {
        "find": {"help": "Entities AAC may see: id, name, area, state (if readable), access.",
                 "args": {"search": {"type": "string", "help": "A word in the name, id or area."},
                          "domain": {"type": "string", "help": "e.g. light, switch, sensor."}}},
        "get_state": {"help": "The state and attributes of entities.",
                      "args": {"entity_ids": {"type": "array", "help": "Entity ids.", "required": True}}},
        "call_service": {"help": "Switch something: a service on an entity with full access (e.g. switch.turn_on).",
                         "args": {"domain": {"type": "string", "help": "e.g. switch, light.", "required": True},
                                  "service": {"type": "string", "help": "e.g. turn_on, turn_off, toggle.", "required": True},
                                  "entity_id": {"type": "string", "help": "The entity.", "required": True},
                                  "data": {"type": "object", "help": "Extra service data (e.g. brightness_pct)."}}},
    }

    def level(self, entity_id: str) -> str:
        """hidden, read or full."""
        return (self.settings.get("levels") or {}).get(entity_id) or self.settings.get("default") or "hidden"

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        hass = self.hass
        if function == "find":
            word, domain = str(args.get("search") or "").lower(), str(args.get("domain") or "")
            found = []
            for state in hass.states.async_all():
                level = self.level(state.entity_id)
                if level == "hidden" or (domain and state.domain != domain):
                    continue
                name, area = state.attributes.get("friendly_name", state.entity_id), _area(hass, state.entity_id)
                if word and word not in f"{state.entity_id} {name} {area or ''}".lower():
                    continue
                found.append({"entity_id": state.entity_id, "name": name, "area": area, "access": level, "state": state.state})
                if len(found) >= MAX_FOUND:
                    break
            return {"entities": found, "more": len(found) >= MAX_FOUND}
        if function == "get_state":
            out = {}
            for entity_id in args.get("entity_ids") or []:
                state = hass.states.get(entity_id)
                if state is None or self.level(entity_id) == "hidden":
                    out[entity_id] = "unknown or not allowed"
                    continue
                out[entity_id] = {"state": state.state, "attributes": {k: v for k, v in state.attributes.items()
                                                                        if isinstance(v, (str, int, float, bool))}}
            return out
        if function == "call_service":
            entity_id = str(args.get("entity_id") or "")
            if self.level(entity_id) != "full":
                return {"error": f"{entity_id} may not be switched (its access is {self.level(entity_id)})."}
            await hass.services.async_call(args["domain"], args["service"], {"entity_id": entity_id, **(args.get("data") or {})},
                                           blocking=True)
            state = hass.states.get(entity_id)
            return {"done": True, "state": state.state if state else None}
        return await super().call(function, args, context)
