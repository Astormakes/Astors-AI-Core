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


def _done(hass: HomeAssistant, store: AACStore, cond: dict, values: dict) -> bool:
    """A module step's done_when (spec/module.md)."""
    if "setting" in cond:
        return bool(values.get(cond["setting"]))
    if "entity_exists" in cond:
        return hass.states.get(cond["entity_exists"]) is not None
    if "entities_of" in cond:  # entities from somewhere else than AAC itself
        ent_reg = er.async_get(hass)
        return any(not ((reg := ent_reg.async_get(e)) and reg.platform == DOMAIN)
                   for e in hass.states.async_entity_ids(cond["entities_of"]))
    if "role" in cond:
        return bool(store.pools.get(cond["role"]))
    if cond.get("rule"):
        return any(t.get("rule") for t in store.data["traits"].values())
    return False


def module_steps(hass: HomeAssistant, entry, domain: DomainSettings) -> list[dict]:
    """Setup steps the domain's enabled modules declare in their manifests."""
    from .modules import access, setting  # noqa: PLC0415

    steps = []
    for module in entry.runtime_data.modules if entry else []:
        if not access(entry, module.id, domain.id)["enabled"]:
            continue
        schema = module.manifest.get("settings", {})
        values = {k: setting(entry, module.manifest, domain.id, k) for k in schema}
        for step in module.manifest.get("setup", []):
            steps.append({
                "id": f"{domain.id}/mod/{module.id}/{step['id']}",
                "title": step.get("title", step["id"]),
                "hint": step.get("hint", ""),
                "target": step.get("target") or f"settings/module/{module.id}",
                "optional": bool(step.get("optional", False)),
                "done": _done(hass, entry.runtime_data.store, step.get("done_when") or {}, values),
                "module": module.id,
                "how": step.get("how", ["panel"]),
            })
    return steps


def setup_steps(hass: HomeAssistant, store: AACStore, domain: DomainSettings | None = None, entry=None) -> list[dict]:
    """The SETUP workflow of a domain (most steps are shared, modules add theirs).
    Closed when done or skipped (optional only)."""
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
            "id": "entities",
            "title": "Choose what AAC may see and control",
            "hint": "Two lists: which entities this domain can see at all (domain menu), and which of them may be "
                    "read or switched (Entities). HA's Assist settings stay HA's. AAC can ask you for more.",
            "target": "domain/visible",
            "optional": True,
            "done": domain.visible is not None,
        },
        {
            "id": "rules",
            "title": "Tell AAC when it may use your machines",
            "hint": "Optional rules per model, e.g. \"use my PC for heavy jobs only while I'm at work\". Say it in a chat; "
                    "AAC writes the rule and you see it under Pools. Without rules every model is used whenever it answers.",
            "target": "settings/pools",
            "optional": True,
            "done": any(t.get("rule") for t in store.data["traits"].values()),
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
    steps += module_steps(hass, entry, domain)
    for step in steps:
        step["skipped"] = step["optional"] and step["id"] in skipped and not step["done"]
    return steps
