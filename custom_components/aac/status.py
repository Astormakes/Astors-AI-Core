"""Setup steps for the panel."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .models import known_servers
from .pools import role_configured
from .store import AACStore, DomainSettings


def aac_agent_entity_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """Entity id of AAC's conversation agent."""
    return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, entry_id)


def setup_steps(hass: HomeAssistant, store: AACStore, domain: DomainSettings | None = None) -> list[dict]:
    """The SETUP workflow of a domain (most steps are shared). Closed when done or skipped (optional only)."""
    domain = domain or store.default_domain
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
            "target": "settings/pools",
            "optional": False,
            "done": role_configured(hass, store, "master"),
        },
        {
            "id": "memory",
            "title": "Give AAC a memory",
            "hint": "Pick an embedding model so it finds memories by meaning, not only by words.",
            "target": "settings/pools",
            "optional": True,
            "done": role_configured(hass, store, "memory"),
        },
        {
            "id": "audio",
            "title": "Give AAC ears and a voice",
            "hint": "Run the AAC satellite on a PC or next to a headset; it streams the mic to AAC and plays the answers.",
            "target": "settings/audio",
            "optional": True,
            "done": bool(store.audio["known"]),
        },
        {
            "id": "heartbeat",
            "title": "Let AAC think on its own",
            "hint": "A heartbeat wakes the master now and then; it only speaks up when it matters.",
            "target": "settings/triggers",
            "optional": True,
            "done": bool(domain.heartbeat["enabled"]),
        },
    ]
    for step in steps:
        step["skipped"] = step["optional"] and step["id"] in skipped and not step["done"]
    return steps
