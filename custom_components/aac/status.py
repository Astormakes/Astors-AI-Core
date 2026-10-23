"""AAC's conversation agent entity (the old setup steps are gone: owner, 2026-10-23)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def aac_agent_entity_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """Entity id of AAC's conversation agent."""
    return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, entry_id)
