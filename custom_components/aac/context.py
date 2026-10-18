"""Context recipes: what an agent gets to see before it starts.

First, tiny piece of the context engine from the framework design: the
heartbeat gets a compact snapshot of the home (the entities exposed to
assistants) instead of having to explore with tool calls, which small
models do badly and slowly.
"""

from __future__ import annotations

from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

MAX_ENTITIES = 80

# binary_sensor "on"/"off" means nothing to a model; say what it means (like HA's UI)
_BINARY_MEANING = {
    **dict.fromkeys(("smoke", "gas", "carbon_monoxide", "motion", "occupancy", "presence",
                     "sound", "vibration", "tamper"), ("DETECTED", "clear")),
    **dict.fromkeys(("door", "window", "opening", "garage_door"), ("OPEN", "closed")),
    "moisture": ("WET", "dry"),
    "problem": ("PROBLEM", "ok"),
    "safety": ("UNSAFE", "safe"),
    "battery": ("LOW", "normal"),
    "cold": ("cold", "normal"),
    "heat": ("hot", "normal"),
    "lock": ("unlocked", "locked"),
    "connectivity": ("connected", "DISCONNECTED"),
    "plug": ("plugged in", "unplugged"),
    "running": ("running", "not running"),
}


def _state_text(state) -> str:
    if state.domain == "binary_sensor" and state.state in ("on", "off"):
        meaning = _BINARY_MEANING.get(state.attributes.get("device_class", ""))
        if meaning:
            return meaning[0] if state.state == "on" else meaning[1]
    unit = state.attributes.get("unit_of_measurement", "")
    return f"{state.state} {unit}".strip()


def home_snapshot(hass: HomeAssistant) -> str:
    """One line per exposed entity: area, name, state (+ unit)."""
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    lines = []
    for state in sorted(hass.states.async_all(), key=lambda s: s.entity_id):
        if not async_should_expose(hass, "conversation", state.entity_id):
            continue
        entry = ent_reg.async_get(state.entity_id)
        area_id = entry.area_id if entry else None
        if entry and not area_id and entry.device_id:
            device = dr.async_get(hass).async_get(entry.device_id)
            area_id = device.area_id if device else None
        area = area_reg.async_get_area(area_id) if area_id else None
        name = state.attributes.get("friendly_name", state.entity_id)
        lines.append(f"- {area.name if area else 'no area'} | {name} | {_state_text(state)}")
        if len(lines) >= MAX_ENTITIES:
            lines.append("- … (more not shown)")
            break
    return "\n".join(lines) if lines else "(no entities exposed)"
