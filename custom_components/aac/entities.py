"""What AAC may see, read and switch in Home Assistant: two layers (owner design 2026-10-19).

1. Per domain, the entities it can **see** at all (the owner's tick list, domain menu). Anything
   else does not exist for that domain, its agent or its modules. New domains see nothing.
2. Per entity, for everything, **read** (the state) and **write** (switch it) (Entities menu).

What a domain may do is where both meet: invisible · hidden (seen, no read) · read · control.
HA's own "expose to Assist" setting stays HA's; until the owner chooses, AAC starts from it.
AAC can ask for read/write on entities it sees; the owner decides (requests.py).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .store import AACStore, DomainSettings

# Switching these needs the owner in the chat (they open the house or switch off safety)
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "cover", "valve", "siren"}


def _exposed(hass: HomeAssistant) -> set[str]:
    return {s.entity_id for s in hass.states.async_all() if async_should_expose(hass, "conversation", s.entity_id)}


def visible_ids(hass: HomeAssistant, settings: DomainSettings) -> set[str]:
    """The entities a domain can see."""
    return set(settings.visible) if settings.visible is not None else _exposed(hass)


def access(hass: HomeAssistant, store: AACStore, entity_id: str) -> dict[str, bool]:
    """Read/write of an entity."""
    if store.entity_access is None:
        on = async_should_expose(hass, "conversation", entity_id)
        return {"read": on, "write": on}
    return {"read": False, "write": False, **store.entity_access.get(entity_id, {})}


def level(hass: HomeAssistant, settings: DomainSettings, entity_id: str) -> str:
    """invisible · hidden · read · control, for one domain."""
    if entity_id not in visible_ids(hass, settings):
        return "invisible"
    acc = access(hass, settings.store, entity_id)
    return "control" if acc["write"] else "read" if acc["read"] else "hidden"


async def async_set_visible(hass: HomeAssistant, settings: DomainSettings, entity_ids: list[str]) -> None:
    """The domain's tick list."""
    await settings.async_set_visible(entity_ids)


async def async_set_access(hass: HomeAssistant, store: AACStore, changes: dict[str, dict[str, bool]]) -> None:
    """Change read/write of some entities (the first change starts from HA's Assist exposure)."""
    current = (dict(store.entity_access) if store.entity_access is not None
               else {e: {"read": True, "write": True} for e in _exposed(hass)})
    for entity_id, acc in changes.items():
        merged = {**current.get(entity_id, {"read": False, "write": False}), **acc}
        if acc.get("write"):
            merged["read"] = True  # writing needs reading
        if acc.get("read") is False:
            merged["write"] = False
        current[entity_id] = merged
    await store.async_set_entity_access(current)


def info(hass: HomeAssistant, state: State) -> dict[str, Any]:
    """Name, room and type of an entity (no state: that needs read)."""
    ent_reg, area_reg = er.async_get(hass), ar.async_get(hass)
    entry = ent_reg.async_get(state.entity_id)
    area_id = entry.area_id if entry else None
    if entry and not area_id and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    area = area_reg.async_get_area(area_id) if area_id else None
    return {"entity_id": state.entity_id, "name": state.attributes.get("friendly_name", state.entity_id),
            "area": area.name if area else None, "domain": state.domain,
            "device_class": state.attributes.get("device_class")}


def _sorted(items):
    return sorted(items, key=lambda e: (e["area"] or "~", e["domain"], e["name"].lower()))


def all_entities(hass: HomeAssistant, store: AACStore, settings: DomainSettings | None = None) -> list[dict[str, Any]]:
    """Every entity with read/write (and, for a domain, whether it is visible there)."""
    seen = visible_ids(hass, settings) if settings is not None else None
    return _sorted(
        {**info(hass, s), **access(hass, store, s.entity_id),
         **({"visible": s.entity_id in seen} if seen is not None else {})}
        for s in hass.states.async_all()
    )


def domain_view(hass: HomeAssistant, settings: DomainSettings) -> list[dict[str, Any]]:
    """What a domain's agent may know: only visible entities, with its level."""
    seen = visible_ids(hass, settings)
    return _sorted({**info(hass, s), "level": level(hass, settings, s.entity_id)}
                   for s in hass.states.async_all() if s.entity_id in seen)


def integrations(hass: HomeAssistant, settings: DomainSettings) -> list[dict[str, Any]]:
    """Installed integrations with their entity counts and how many this domain can see (names only,
    no entity details: those stay behind the owner's lists)."""
    ent_reg = er.async_get(hass)
    seen = visible_ids(hass, settings)
    by_entry: dict[str, dict[str, Any]] = {}
    for reg in ent_reg.entities.values():
        if reg.disabled_by or not reg.config_entry_id:
            continue
        item = by_entry.setdefault(reg.config_entry_id, {"entities": 0, "visible": 0})
        item["entities"] += 1
        item["visible"] += reg.entity_id in seen
    found = []
    for entry in hass.config_entries.async_entries():
        if entry.domain == "aac":
            continue
        counts = by_entry.get(entry.entry_id, {"entities": 0, "visible": 0})
        found.append({"id": entry.entry_id, "integration": entry.domain, "title": entry.title, **counts})
    return sorted(found, key=lambda x: (x["integration"], x["title"]))


def entities_of(hass: HomeAssistant, entry_ids: list[str]) -> list[str]:
    """The (enabled) entities of some integration entries."""
    ent_reg = er.async_get(hass)
    return [r.entity_id for r in ent_reg.entities.values() if r.config_entry_id in entry_ids and not r.disabled_by]
