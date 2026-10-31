"""AAC – Astor's AI Core, v2 (docs/v2): an open AI companion and sandbox inside Home Assistant, built from cards."""

from __future__ import annotations

import asyncio
from pathlib import Path
import re

from aiohttp import web

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import HomeAssistantView, StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .audio import AudioView, ClipView, async_satellite_access
from .cards import UI_URL, load_all
from .const import DOMAIN, PANEL_URL
from .core import Core
from .skins import USER_URL, user_dir
from .websocket import async_register

type AACConfigEntry = ConfigEntry[Core]
PLATFORMS = ["calendar", "conversation", "media_player"]


async def async_setup_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Start the core, its running domain and the panel."""
    await hass.async_add_executor_job(load_all, hass.config.path("aac", "cards"))  # AAC's cards, then yours
    core = Core(hass)
    await core.load()
    entry.runtime_data = core
    hass.data[DOMAIN] = core
    async_register(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not hass.data.get(f"{DOMAIN}_audio_view"):  # satellites stream here; card pages are served from the cards folder
        hass.http.register_view(AudioView())
        hass.http.register_view(ClipView())
        hass.http.register_view(CardUiView(hass.config.path("aac", "cards")))
        hass.data[f"{DOMAIN}_audio_view"] = True
    entry.async_create_background_task(hass, async_satellite_access(core), "aac satellite access")
    # it never ends by itself: a plain task (not one HA waits for), cancelled when AAC unloads
    watcher = asyncio.get_running_loop().create_task(core.watch_cards(hass.config.path("aac", "cards")), name="aac card files")

    def stop_watching() -> None:  # not watcher.cancel itself: HA would take its True for a task to run
        watcher.cancel()

    entry.async_on_unload(stop_watching)
    version = (await async_get_integration(hass, DOMAIN)).version
    if not hass.data.get(f"{DOMAIN}_static"):
        skins = user_dir(hass)
        await hass.async_add_executor_job(lambda: skins.mkdir(parents=True, exist_ok=True))
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_URL, str(Path(__file__).parent / "www"), cache_headers=False),
             StaticPathConfig(USER_URL, str(skins), cache_headers=False)])
        hass.data[f"{DOMAIN}_static"] = True
    await panel_custom.async_register_panel(
        hass, webcomponent_name="aac-panel", frontend_url_path="aac", sidebar_title="AAC", sidebar_icon="mdi:robot-outline",
        module_url=f"{PANEL_URL}/aac.js?v={version}", require_admin=True, config={"version": str(version)})
    return True


class CardUiView(HomeAssistantView):
    """A card's page or view: `<config>/aac/cards/<stem>.js` or `<folder>/<file>.js` (only .js files; the panel imports
    them as modules)."""

    url = f"{UI_URL}/{{name:.+}}"
    name = "aac:card_ui"
    requires_auth = False  # a module import sends no token; card files hold no secrets

    def __init__(self, folder: str) -> None:
        """Serve from the cards folder."""
        self.folder = Path(folder)

    async def get(self, request: web.Request, name: str) -> web.StreamResponse:
        """One card page."""
        if not re.fullmatch(r"[a-z0-9_]+(/[a-z0-9_]+)?\.js", name) or not (path := self.folder / name).is_file():
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Content-Type": "text/javascript", "Cache-Control": "no-cache"})


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
