"""AAC – Astor's AI Core, v2 (docs/v2): an open AI companion and sandbox inside Home Assistant, built from cards."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .audio import AudioView, async_satellite_access
from .cards import load_all
from .const import DOMAIN, PANEL_URL
from .core import Core
from .websocket import async_register

type AACConfigEntry = ConfigEntry[Core]
PLATFORMS = ["conversation"]


async def async_setup_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Start the core, its running domain and the panel."""
    load_all()
    core = Core(hass)
    await core.load()
    entry.runtime_data = core
    hass.data[DOMAIN] = core
    async_register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not hass.data.get(f"{DOMAIN}_audio_view"):  # satellites stream here
        hass.http.register_view(AudioView())
        hass.data[f"{DOMAIN}_audio_view"] = True
    entry.async_create_background_task(hass, async_satellite_access(core), "aac satellite access")
    version = (await async_get_integration(hass, DOMAIN)).version
    if not hass.data.get(f"{DOMAIN}_static"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_URL, str(Path(__file__).parent / "www"), cache_headers=False)])
        hass.data[f"{DOMAIN}_static"] = True
    await panel_custom.async_register_panel(
        hass, webcomponent_name="aac-panel", frontend_url_path="aac", sidebar_title="AAC", sidebar_icon="mdi:robot-outline",
        module_url=f"{PANEL_URL}/aac.js?v={version}", require_admin=True, config={"version": str(version)})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Stop: the setup is kept."""
    frontend.async_remove_panel(hass, "aac")
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    core = entry.runtime_data
    if core.domain:
        await core.domain.stop()
        await core.domain.unload()
        await core.domain.save_now()
    hass.data.pop(DOMAIN, None)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1's entry becomes v2's (v2 keeps everything in its own storage; v1's files stay untouched)."""
    if entry.version < 3:
        hass.config_entries.async_update_entry(entry, data={}, options={}, version=3, minor_version=1)
    return True
