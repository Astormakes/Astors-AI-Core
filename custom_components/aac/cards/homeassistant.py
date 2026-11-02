"""Home Assistant: the world as far as the card may reach it (docs/v2 page 6): per entity hidden (not there for it),
list (it knows it exists), read (its state) or full (switch it too), never more than its domain allows. Anyone can
build other HA integration cards."""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "1.3.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

MAX_FOUND = 60
SETTLE = 2.0  # seconds a switched entity gets to report its new state (a device answers after the service returns)


@register
class HomeAssistantCard(Card):
    """Home Assistant."""

    type = "homeassistant"
    title = "Home Assistant"
    category = "Home Assistant"
    help = ("Home Assistant for a model or a Relay's script: find entities, read their state, switch them; only as far "
            "as its May use allows (Permissions).")
    needs = {"entities"}
    functions = {
        "find": {"help": "Entities AAC may know: id, name, area, access, state (if readable).",
                 "args": {"search": {"type": "string", "help": "A word in the name, id or area."},
                          "domain": {"type": "string", "help": "e.g. light, switch, sensor."}}},
        "get_state": {"help": "The state and attributes of entities.",
                      "args": {"entity_ids": {"type": "array", "help": "Entity ids.", "required": True}}},
        "call_service": {"help": "Switch something: a service of the entity's own domain on an entity with full access "
                                 "(e.g. switch.turn_on).",
                         "args": {"domain": {"type": "string", "help": "e.g. switch, light.", "required": True},
                                  "service": {"type": "string", "help": "e.g. turn_on, turn_off, toggle.", "required": True},
                                  "entity_id": {"type": "string", "help": "The entity.", "required": True},
                                  "data": {"type": "object", "help": "Extra service data (e.g. brightness_pct)."}}},
    }

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "find":
            word, domain = str(args.get("search") or "").lower(), str(args.get("domain") or "")
            found = []
            for e in self.ha.entities():
                if domain and e["entity_id"].split(".")[0] != domain:
                    continue
                if word and word not in f"{e['entity_id']} {e['name']} {e['area'] or ''}".lower():
                    continue
                found.append(e)
                if len(found) >= MAX_FOUND:
                    break
            return {"entities": found, "more": len(found) >= MAX_FOUND}
        if function == "get_state":
            out = {}
            for entity_id in args.get("entity_ids") or []:
                state = self.ha.state(entity_id)
                out[entity_id] = ({"state": state.state, "attributes": {k: v for k, v in state.attributes.items()
                                                                         if isinstance(v, (str, int, float, bool))}}
                                  if state is not None else "unknown or not allowed")
            return out
        if function == "call_service":
            entity_id = str(args.get("entity_id") or "")
            if not self.may(entity_id, "full"):
                return {"error": f"{entity_id} may not be switched (its access is {self.level(entity_id)})."}
            changed = asyncio.Event()
            unwatch = self.ha.watch([entity_id], lambda _event: changed.set())
            try:
                await self.ha.call(str(args["domain"]), str(args["service"]), entity_id, args.get("data") or {})
                try:  # the state it reports is the new one (no change within SETTLE: it already was so)
                    await asyncio.wait_for(changed.wait(), SETTLE)
                except TimeoutError:
                    pass
            finally:
                unwatch()
            state = self.ha.state(entity_id)
            return {"done": True, "state": state.state if state else None}
        return await super().call(function, args, context)
