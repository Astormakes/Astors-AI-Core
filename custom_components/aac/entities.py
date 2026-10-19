"""What AAC may see and control in Home Assistant, per AAC domain.

HA's own "expose to Assist" setting belongs to HA (it also drives HA's voice
assistant), so AAC keeps its own list: per entity `read` (AAC sees the state) or
`control` (it may also switch it); everything else is hidden. Until the owner
chooses, AAC follows HA's Assist exposure. AAC may see that an entity exists (name,
room, type) and ask for access; the owner decides (requests.py).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .store import DomainSettings

LEVELS = ("hidden", "read", "control")
# Switching these needs the owner in the chat (they open the house or switch off safety)
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "cover", "valve", "siren"}


def level(hass: HomeAssistant, settings: DomainSettings, entity_id: str) -> str:
    """hidden · read · control."""
    if settings.entities is None:
        return "control" if async_should_expose(hass, "conversation", entity_id) else "hidden"
    return settings.entities.get(entity_id, "hidden")


def current_levels(hass: HomeAssistant, settings: DomainSettings) -> dict[str, str]:
    """The effective list (HA's exposure turned into AAC's own while none is chosen)."""
    if settings.entities is not None:
        return dict(settings.entities)
    return {s.entity_id: "control" for s in hass.states.async_all()
            if async_should_expose(hass, "conversation", s.entity_id)}


async def async_set_levels(hass: HomeAssistant, settings: DomainSettings, changes: dict[str, str]) -> None:
    """Change some entities' levels (the first change turns HA's exposure into AAC's own list)."""
    levels = current_levels(hass, settings)
    for entity_id, lvl in changes.items():
        if lvl in ("read", "control"):
            levels[entity_id] = lvl
        else:
            levels.pop(entity_id, None)
    await settings.async_set_entities(levels)


def info(hass: HomeAssistant, state: State) -> dict[str, Any]:
    """Name, room and type of an entity (no state: that needs read access)."""
    ent_reg, area_reg = er.async_get(hass), ar.async_get(hass)
    entry = ent_reg.async_get(state.entity_id)
    area_id = entry.area_id if entry else None
    if entry and not area_id and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        area_id = device.area_id if device else None
    area = area_reg.async_get_area(area_id) if area_id else None
    return {
        "entity_id": state.entity_id,
        "name": state.attributes.get("friendly_name", state.entity_id),
        "area": area.name if area else None,
        "domain": state.domain,
        "device_class": state.attributes.get("device_class"),
    }


def everything(hass: HomeAssistant, settings: DomainSettings) -> list[dict[str, Any]]:
    """All entities with their info and AAC's access level."""
    return sorted(
        ({**info(hass, s), "level": level(hass, settings, s.entity_id)} for s in hass.states.async_all()),
        key=lambda e: (e["area"] or "~", e["domain"], e["name"].lower()),
    )
