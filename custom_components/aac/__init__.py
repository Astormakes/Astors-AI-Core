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
from homeassistant.const import (
    CONF_API_KEY,
    CONF_LLM_HASS_API,
    CONF_PROMPT,
    EVENT_HOMEASSISTANT_STARTED,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
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
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .chats import ChatStore
from .memory import MemoryStore, memory_path
from .memory_api import AACMemoryAPI
from .schedule_api import AACScheduleAPI
from .models import resolve_model
from .provider import OpenAICompatProvider
from .runs import RunRegistry
from .store import CONF_MASTER_MODEL, AACStore
from .triggers import TriggerEngine

PLATFORMS = (Platform.CONVERSATION,)
PANEL_URL = "aac"
STATIC_URL = "/aac_static"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AACData:
    """Runtime data of the loaded entry."""

    store: AACStore
    version: str
    runs: RunRegistry
    chats: ChatStore
    memory: MemoryStore | None = None
    triggers: TriggerEngine | None = None


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
    runs = RunRegistry(hass)
    await runs.async_load()
    chats = ChatStore(hass)
    await chats.async_load()
    entry.runtime_data = AACData(store, version, runs, chats)

    async def embed(texts: list[str]):
        endpoint = resolve_model(hass, store, store.memory["embed_model"])
        if endpoint is None:
            return None
        provider = OpenAICompatProvider(
            async_get_clientsession(hass), endpoint.base_url, endpoint.api_key
        )
        return await provider.embed(endpoint.model, texts), endpoint.model

    memory = MemoryStore(hass, memory_path(hass), embed)
    await memory.async_open()
    entry.runtime_data.memory = memory
    entry.async_on_unload(
        llm.async_register_api(
            hass, AACMemoryAPI(hass=hass, id="aac_memory", name="AAC memory", memory=memory)
        )
    )
    entry.async_on_unload(
        llm.async_register_api(
            hass, AACScheduleAPI(hass=hass, id="aac_schedule", name="AAC planning", entry=entry)
        )
    )

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

    # Heartbeat and event triggers start once HA is up (entities exist)
    engine = TriggerEngine(hass, entry)
    entry.runtime_data.triggers = engine
    entry.async_on_unload(engine.async_stop)
    if hass.is_running:
        engine.async_start()
    else:

        @callback
        def _started(_event: Event) -> None:
            engine.async_start()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _started)
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Unload a config entry."""
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and entry.runtime_data.memory:
        await entry.runtime_data.memory.async_close()
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 kept server/model in the entry; v2 keeps everything in the panel store."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        store = AACStore(hass)
        await store.async_load()
        if entry.data.get(CONF_BASE_URL) and entry.data.get(CONF_MODEL):
            cfg = await store.async_save_model(
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
            await store.async_update_master({**master, CONF_MASTER_MODEL: cfg["id"]})
        hass.config_entries.async_update_entry(
            entry, data={}, options={}, title=DEFAULT_NAME, version=2
        )
        LOGGER.info("Migrated AAC config entry to version 2 (settings now in the panel)")
    return True
