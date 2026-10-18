"""Setup steps for the panel."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .store import CONF_MASTER_MODEL, AACStore
from .models import known_servers, resolve_model


def aac_agent_entity_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """Entity id of AAC's conversation agent."""
    return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, entry_id)


def setup_steps(hass: HomeAssistant, store: AACStore) -> list[dict]:
    """The SETUP workflow. A step is closed when done or skipped (optional only)."""
    skipped = set(store.data["setup"]["skipped"])
    steps = [
        {
            "id": "models",
            "title": "Connect a model",
            "hint": "Add a model server, or let AAC find the ones HA already knows.",
            "target": "models",
            "optional": False,
            "done": bool(known_servers(hass, store)),
        },
        {
            "id": "master",
            "title": "Choose the master model",
            "hint": "The model that thinks and answers for AAC.",
            "target": "settings/master",
            "optional": False,
            "done": resolve_model(hass, store, store.master[CONF_MASTER_MODEL]) is not None,
        },
        {
            "id": "heartbeat",
            "title": "Let AAC think on its own",
            "hint": "A heartbeat wakes the master now and then; it only speaks up when it matters.",
            "target": "settings/triggers",
            "optional": True,
            "done": bool(store.heartbeat["enabled"]),
        },
    ]
    for step in steps:
        step["skipped"] = step["optional"] and step["id"] in skipped and not step["done"]
    return steps
