"""AAC – Astor's AI Core.

Home Assistant is the body (world layer), AAC is the brain on top.
Everything is configured in the AAC panel; the config entry only anchors it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from homeassistant.util import dt as dt_util

from . import languages, websocket
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

from .audio_hub import AudioHub
from .audio_view import AACAudioView
from .chats import ChatStore
from .domains import Domain, async_load_domain
from .capabilities import CalendarProvider, Capabilities, parse_time, plan_occurrences
from .home_api import AACHomeAPI, apply_entities, apply_visibility
from .jobs import JobQueue
from .requests import Requests
from .broker import BrokerNode
from .modules import MODULES_DIR, MODULES_URL, LoadedModule, async_load_modules
from .pools import TIERS, Pool
from .skins import USER_URL as USER_SKINS_URL, user_dir
from .memory import MemoryStore, memory_path
from .memory_api import AACMemoryAPI
from .menu_api import MENU_API_ID, AACMenuAPI
from .schedule_api import AACScheduleAPI
from .setup_api import AACSetupAPI
from .provider import OpenAICompatProvider, ProviderConnectionError
from .runs import RunRegistry
from .store import AACStore
from .streams import Bus
from .triggers import TriggerEngine

PLATFORMS = (Platform.CONVERSATION, Platform.CALENDAR)
PANEL_URL = "aac"
STATIC_URL = "/aac_static"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class AACData:
    """Runtime data of the loaded entry: shared parts + one Domain per AAC domain."""

    store: AACStore
    version: str
    domains: dict[str, Domain] = field(default_factory=dict)
    audio: AudioHub | None = None
    pool: Pool | None = None
    jobs: JobQueue | None = None
    capabilities: Capabilities = field(default_factory=Capabilities)
    bus: Bus = field(default_factory=Bus)
    modules: list[LoadedModule] = field(default_factory=list)
    requests: Requests | None = None

    def domain(self, domain_id: str | None = None) -> Domain | None:
        """A domain by id (None = the first)."""
        return next(iter(self.domains.values()), None) if domain_id is None else self.domains.get(domain_id)

    @property
    def voice_domain(self) -> Domain:
        """The domain the microphones talk to."""
        return self.domain(self.store.data["voice_domain"]) or self.domain()

    # The first domain's parts, for code that is not domain-aware
    @property
    def chats(self) -> ChatStore:
        """First domain."""
        return self.domain().chats

    @property
    def runs(self) -> RunRegistry:
        """First domain."""
        return self.domain().runs

    @property
    def memory(self) -> MemoryStore | None:
        """First domain."""
        return self.domain().memory

    @property
    def triggers(self) -> TriggerEngine | None:
        """First domain."""
        return self.domain().triggers


type AACConfigEntry = ConfigEntry[AACData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the panel's WebSocket commands and static files."""
    websocket.async_register(hass)
    hass.http.register_view(AACAudioView())
    skins_dir = user_dir(hass)
    await hass.async_add_executor_job(lambda: skins_dir.mkdir(parents=True, exist_ok=True))
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                STATIC_URL, str(Path(__file__).parent / "www"), cache_headers=False
            ),
            StaticPathConfig(USER_SKINS_URL, str(skins_dir), cache_headers=False),
            StaticPathConfig(MODULES_URL, str(MODULES_DIR), cache_headers=False),
        ]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Set up AAC from a config entry."""
    store = AACStore(hass)
    await store.async_load()
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry.runtime_data = AACData(store, version)
    pool = entry.runtime_data.pool = Pool(hass, store)
    entry.runtime_data.jobs = JobQueue(hass, entry)
    entry.runtime_data.requests = Requests(hass, entry)
    entry.runtime_data.requests.register_kind("entities", apply_entities)
    entry.runtime_data.requests.register_kind("visibility", apply_visibility)

    async def plans(domain_id: str, start, end):
        dom = entry.runtime_data.domain(domain_id)
        return plan_occurrences(dom.settings.triggers, start, end) if dom else []

    def _own_plan(domain_id: str, key: str):
        dom = entry.runtime_data.domain(domain_id)
        trig = next((t for t in dom.settings.triggers if t["id"] == key), None) if dom else None
        if trig is None:
            raise LookupError("That plan no longer exists")
        if trig.get("created_by") != "agent":
            raise LookupError("That is the owner's trigger; change it in the panel")  # agents organize tasks, not rules
        return dom, trig

    async def change_plan(domain_id: str, key: str, changes: dict, recurrence_id: str | None = None):
        # A repeating plan moves as a whole (single occurrences are not kept apart yet)
        dom, trig = _own_plan(domain_id, key)
        when = dict(trig["when"])
        if start := parse_time(changes.get("start")):
            local = dt_util.as_local(start)
            when["at"] = local.strftime("%H:%M")
            if when.get("date"):
                when["date"] = local.date().isoformat()
        saved = await dom.settings.async_save_trigger({**trig, "when": when, "name": changes.get("name") or trig["name"]})
        dom.triggers.async_start()
        return {"name": saved["name"], "when": saved["when"]}

    async def cancel_plan(domain_id: str, key: str, recurrence_id: str | None = None) -> bool:
        dom, trig = _own_plan(domain_id, key)
        await dom.settings.async_delete_trigger(trig["id"])
        dom.triggers.async_start()
        return True

    entry.runtime_data.capabilities.add_calendar(
        CalendarProvider("core", "AAC's plans and timed triggers", plans, update=change_plan, delete=cancel_plan)
    )
    entry.runtime_data.audio = AudioHub(hass, entry)
    try:
        await hass.async_add_import_executor_job(languages.load)
    except Exception as err:  # noqa: BLE001 - without it, answers use the engine's default voice
        LOGGER.warning("AAC: language detection unavailable: %s", err)

    async def embed(texts: list[str]):
        if (picked := pool.endpoint("memory")) is None:
            return None  # keywords only
        model_id, endpoint = picked
        provider = OpenAICompatProvider(
            async_get_clientsession(hass), endpoint.base_url, endpoint.api_key, connect_timeout=3
        )
        try:
            vectors = await provider.embed(endpoint.model, texts)
        except ProviderConnectionError as err:
            pool.mark_down(model_id, str(err))
            raise
        pool.mark_up(model_id)
        return vectors, endpoint.model

    for i, settings in enumerate(store.domains.values()):
        dom = await async_load_domain(hass, settings, first=i == 0)
        entry.runtime_data.domains[dom.id] = dom
        dom.memory = MemoryStore(hass, memory_path(hass, dom.suffix), embed)
        await dom.memory.async_open()
        label = "" if dom.first else f" ({dom.name})"
        for api in (
            AACMemoryAPI(hass=hass, id=dom.api_id("aac_memory"), name=f"AAC memory{label}", memory=dom.memory),
            AACScheduleAPI(hass=hass, id=dom.api_id("aac_schedule"), name=f"AAC planning{label}", entry=entry, domain=dom),
            AACSetupAPI(hass=hass, id=dom.api_id("aac_setup"), name=f"AAC setup{label}", entry=entry, domain=dom),
            AACHomeAPI(hass=hass, id=dom.api_id("aac_home"), name=f"AAC home{label}", entry=entry, domain=dom),
            AACMenuAPI(hass=hass, id=dom.api_id(MENU_API_ID), name=f"AAC menu{label}"),
        ):
            entry.async_on_unload(llm.async_register_api(hass, api))

    # The broker is the core's node on the stream bus: modules ask it through broker.request
    entry.async_on_unload(entry.runtime_data.bus.add_node(BrokerNode(hass, entry).node))
    entry.runtime_data.capabilities.options["tiers"] = lambda domain_id: [
        (t, t) for t in dict.fromkeys([*TIERS, *(tag for tr in store.data["traits"].values() for tag in tr.get("tags", []))])]

    # Modules after the domains (they keep data per domain), before the agents (they bring tools)
    entry.runtime_data.modules = await async_load_modules(hass, entry)

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

    # Event triggers (one engine per domain) start once HA is up (entities exist)
    for dom in entry.runtime_data.domains.values():
        dom.triggers = TriggerEngine(hass, entry, dom)
        entry.async_on_unload(dom.triggers.async_stop)

    entry.async_on_unload(entry.runtime_data.jobs.async_stop)

    @callback
    def _start(_event: Event | None = None) -> None:
        for dom in entry.runtime_data.domains.values():
            dom.triggers.async_start()
        entry.async_create_background_task(hass, entry.runtime_data.jobs.async_start(), "aac jobs restore")

    if hass.is_running:
        _start()
    else:
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AACConfigEntry) -> bool:
    """Unload a config entry."""
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for dom in entry.runtime_data.domains.values():
            if dom.memory:
                await dom.memory.async_close()
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
            await store.async_update_master(master)
            await store.async_set_pool("master", [cfg["id"]])
        hass.config_entries.async_update_entry(
            entry, data={}, options={}, title=DEFAULT_NAME, version=2
        )
        LOGGER.info("Migrated AAC config entry to version 2 (settings now in the panel)")
    return True
