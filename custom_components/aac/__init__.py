"""AAC – Astor's AI Core.

Home Assistant is the body (world layer), AAC is the brain on top.
Everything is configured in the AAC panel; the config entry only anchors it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import websocket
from .const import (
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_NAME,
    DOMAIN,
    LOGGER,
)
from .store import CONF_WORKER, AACStore

PLATFORMS = (Platform.CONVERSATION,)
PANEL_URL = "aac"
STATIC_URL = "/aac_static"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AACData:
    """Runtime data of the loaded entry."""

    store: AACStore
    version: str


type AACConfigEntry = ConfigEntry[AACData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the panel's WebSocket commands and static files."""
    websocket.async_register(hass)
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False
            )
        ]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Set up AAC from a config entry."""
    store = AACStore(hass)
    await store.async_load()
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry.runtime_data = AACData(store, version)

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="aac-panel",
        sidebar_title=DEFAULT_NAME,
        sidebar_icon="mdi:robot-happy-outline",
        module_url=f"{STATIC_URL}/aac-panel.js?v={version}",
        require_admin=True,
        config_panel_domain=DOMAIN,
        # The panel pads for notches / gesture bars itself (env(safe-area-inset-*));
        # otherwise HA's padding plus our full-height layout pushes the bottom off-screen
        handle_safe_area=True,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Unload a config entry."""
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 kept server/model in the entry; v2 keeps everything in the panel store."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        store = AACStore(hass)
        await store.async_load()
        if entry.data.get(CONF_BASE_URL) and entry.data.get(CONF_MODEL):
            worker = await store.async_save_worker(
                {
                    "name": entry.data[CONF_MODEL],
                    CONF_BASE_URL: entry.data[CONF_BASE_URL],
                    CONF_API_KEY: entry.data.get(CONF_API_KEY),
                    CONF_MODEL: entry.data[CONF_MODEL],
                }
            )
            master = {
                k: entry.options[k]
                for k in (
                    CONF_PROMPT,
                    CONF_LLM_HASS_API,
                    CONF_TEMPERATURE,
                    CONF_MAX_TOKENS,
                    CONF_THINKING,
                )
                if k in entry.options
            }
            await store.async_update_master({**master, CONF_WORKER: worker["id"]})
        hass.config_entries.async_update_entry(
            entry, data={}, options={}, title=DEFAULT_NAME, version=2
        )
        LOGGER.info("Migrated AAC config entry to version 2 (settings now in the panel)")
    return True
