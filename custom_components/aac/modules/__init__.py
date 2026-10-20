"""Built-in module loader (spec/module.md).

A module is a folder here with a `manifest.json` and Python code exposing
`async_setup_module(ctx)`. Through the context it adds what it provides: storage
per AAC domain, calendar entries, tools for the agents (one LLM API per domain),
WebSocket commands and its own panel page. The core stays generic; it only loads
modules and hands them the shared interfaces. External and AAC-made modules come
later through the same context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.storage import Store

from ..capabilities import CalendarProvider, Hearing, Stage
from ..const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from .. import AACConfigEntry
    from ..domains import Domain

# The owner's per-domain permissions and properties of a module (always there, for every module)
ACCESS_DEFAULTS: dict[str, Any] = {
    "enabled": True,  # off: no tools, no page, no calendar entries in this domain
    "name": None,  # own display name (None = the manifest's)
    "instructions": None,  # own instructions for the AI (None = the manifest's)
    "access": "full",  # what agents may do with its tools: full · read · none
    "auto": True,  # automatic runs (heartbeat, triggers) may use its tools
    "destructive_needs_owner": True,  # deleting tools only while the owner is in a chat
    "hidden": False,  # left out of the panel's sidebar (still on; the eye button shows it)
}

# Modules whose manifest says "enabled_by_default": false (the owner switches them on per domain)
_DEFAULT_OFF: set[str] = set()

MODULES_DIR = Path(__file__).parent
MODULES_URL = "/aac_modules"
_WS_DONE = f"{DOMAIN}_module_ws"


@dataclass
class LoadedModule:
    """A running module."""

    manifest: dict[str, Any]
    pages: list[dict[str, Any]] = field(default_factory=list)
    api_ids: dict[str, str] = field(default_factory=dict)  # domain id → LLM API id

    @property
    def id(self) -> str:
        """Module id."""
        return self.manifest["id"]

    def summary(self, entry: AACConfigEntry | None = None, domain_id: str | None = None) -> dict[str, Any]:
        """For the panel (pages a domain switched off, or a disabled module's, are left out)."""
        m = self.manifest
        acc = access(entry, self.id, domain_id) if entry and domain_id else ACCESS_DEFAULTS
        pages = [] if not acc["enabled"] else [
            p for p in self.pages if not (entry and domain_id and p.get("visible_setting"))
            or setting(entry, m, domain_id, p["visible_setting"])
        ]
        name = acc["name"] or m.get("name", m["id"])
        if acc["name"] and len(pages) == 1:
            pages = [{**pages[0], "title": acc["name"]}]
        return {"id": m["id"], "name": name, "version": m.get("version"), "enabled": acc["enabled"],
                "hidden": acc["hidden"], "description": m.get("description", ""), "pages": pages}


class ModuleContext:
    """What a module may use while it sets itself up."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry, module: LoadedModule) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.module = module

    @property
    def domains(self) -> dict[str, Domain]:
        """The AAC domains (each module keeps its data per domain)."""
        return self.entry.runtime_data.domains

    def storage(self, domain: Domain, name: str = "data") -> Store:
        """HA storage for this module in one domain (.storage/aac.mod.<module>.<name>[.<domain>])."""
        suffix = "" if domain.first else f".{domain.id}"
        return Store(self.hass, 1, f"{DOMAIN}.mod.{self.module.id}.{name}{suffix}")

    def add_calendar(self, provider: CalendarProvider) -> None:
        """Provide (and maybe take) calendar entries (not in domains where the module is off)."""
        provider.module = self.module.id
        self.entry.async_on_unload(self.entry.runtime_data.capabilities.add_calendar(provider))

    def add_hearing(self, listener: Hearing) -> None:
        """Hear what AAC's microphones hear (only while the module is on in the voice domain)."""
        listener.module = self.module.id
        self.entry.async_on_unload(self.entry.runtime_data.capabilities.add_hearing(listener))

    def add_stage(self, stage: Stage) -> None:
        """Work on the audio and add to the buffer (only while the module is on in the voice domain)."""
        stage.module = self.module.id
        self.entry.async_on_unload(self.entry.runtime_data.capabilities.add_stage(stage))

    @property
    def audio(self):
        """The audio hub: sources, transcript, speaking, answering through the master."""
        return self.entry.runtime_data.audio

    def add_llm_api(self, factory: Callable[[Domain, str, str], llm.API]) -> None:
        """Tools for the agents: factory(domain, api_id, name) → an llm.API, registered per domain."""
        title = self.module.manifest.get("name", self.module.id)
        for dom in self.domains.values():
            api_id = dom.api_id(f"aac_{self.module.id}")
            name = f"AAC {title.lower()}" + ("" if dom.first else f" ({dom.name})")
            guarded = GuardedAPI(hass=self.hass, id=api_id, name=name, inner=factory(dom, api_id, name),
                                 entry=self.entry, module=self.module, domain_id=dom.id)
            self.entry.async_on_unload(llm.async_register_api(self.hass, guarded))
            self.module.api_ids[dom.id] = api_id

    def add_ws_commands(self, *handlers) -> None:
        """WebSocket commands (registered once per HA start, they look the entry up themselves)."""
        done = self.hass.data.setdefault(_WS_DONE, set())
        for handler in handlers:
            name = getattr(handler, "_ws_command", handler.__name__)  # the command name HA stored
            if name not in done:
                websocket_api.async_register_command(self.hass, handler)
                done.add(name)

    def setting(self, domain_id: str, key: str) -> Any:
        """A module setting in one domain (the manifest's default when the owner never set it)."""
        return setting(self.entry, self.module.manifest, domain_id, key)

    def add_panel_page(self, page_id: str, title: str, element: str, script: str,
                       visible_setting: str | None = None) -> None:
        """A page in the panel: a web component (script relative to the module folder);
        visible_setting = a bool setting that switches the page on or off per domain."""
        version = self.module.manifest.get("version", "0")
        self.module.pages.append({"id": page_id, "title": title, "element": element, "visible_setting": visible_setting,
                                  "url": f"{MODULES_URL}/{self.module.id}/{script}?v={version}"})


@dataclass(kw_only=True)
class GuardedAPI(llm.API):
    """A module's tools as the owner allows them in one domain: the permissions are applied every
    time an agent asks for tools, so a module cannot get around them."""

    inner: llm.API
    entry: Any
    module: LoadedModule
    domain_id: str

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        """The inner tools, filtered, with the owner's (or the manifest's) instructions."""
        from ..runs import current_run  # noqa: PLC0415
        from ..setup_api import owner_present  # noqa: PLC0415

        inst = await self.inner.async_get_api_instance(llm_context)
        acc = access(self.entry, self.module.id, self.domain_id)
        run = current_run.get()
        tools = list(inst.tools)
        if not acc["enabled"] or acc["access"] == "none" or (run and run.kind == "auto" and not acc["auto"]):
            tools = []
        elif acc["access"] == "read":
            tools = [t for t in tools if t.annotations and t.annotations.read_only]
        if acc["destructive_needs_owner"] and any(_destructive(t) for t in tools):
            user_id = llm_context.context.user_id if llm_context.context else None
            if not await owner_present(self.hass, user_id):
                tools = [t for t in tools if not _destructive(t)]
        prompt = acc["instructions"] or self.module.manifest.get("instructions") or inst.api_prompt
        return llm.APIInstance(api=self, api_prompt=prompt, llm_context=llm_context, tools=tools,
                               custom_serializer=inst.custom_serializer)


def _destructive(tool: llm.Tool) -> bool:
    """Explicitly marked as deleting something (read-only tools never are)."""
    ann = tool.annotations
    return bool(ann and ann.destructive is True and not ann.read_only)


def access(entry: AACConfigEntry, module_id: str, domain_id: str | None) -> dict[str, Any]:
    """A module's permissions and properties in a domain (defaults filled in)."""
    stored = entry.runtime_data.store.data["module_access"].get(domain_id or "", {}).get(module_id, {})
    return {**ACCESS_DEFAULTS, "enabled": module_id not in _DEFAULT_OFF,
            **{k: v for k, v in stored.items() if k in ACCESS_DEFAULTS}}


async def async_set_access(entry: AACConfigEntry, module_id: str, domain_id: str, values: dict[str, Any]) -> None:
    """Store permissions/properties (None or "" resets a text field to the manifest's)."""
    store = entry.runtime_data.store
    current = store.data["module_access"].setdefault(domain_id, {}).setdefault(module_id, {})
    for key, value in values.items():
        if key not in ACCESS_DEFAULTS:
            continue
        if key in ("name", "instructions") and not (value or "").strip():
            current.pop(key, None)
        else:
            current[key] = value.strip() if isinstance(value, str) else value
    await store.async_save()


def setting(entry: AACConfigEntry, manifest: dict[str, Any], domain_id: str, key: str) -> Any:
    """A module's setting in a domain, or its default."""
    stored = entry.runtime_data.store.data["module_settings"].get(domain_id, {}).get(manifest["id"], {})
    return stored[key] if key in stored else (manifest.get("settings", {}).get(key) or {}).get("default")


async def async_set_settings(entry: AACConfigEntry, module: LoadedModule, domain_id: str, values: dict[str, Any]) -> None:
    """Store a module's settings for a domain (unknown keys are dropped)."""
    schema = module.manifest.get("settings", {})
    store = entry.runtime_data.store
    current = store.data["module_settings"].setdefault(domain_id, {}).setdefault(module.id, {})
    current.update({k: v for k, v in values.items() if k in schema})
    await store.async_save()


def _manifests() -> list[dict[str, Any]]:
    found = []
    for path in sorted(MODULES_DIR.glob("*/manifest.json")):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as err:
            LOGGER.error("Module %s: bad manifest: %s", path.parent.name, err)
    return found


async def async_load_modules(hass: HomeAssistant, entry: AACConfigEntry) -> list[LoadedModule]:
    """Load every built-in module (a failing one is skipped, the rest keep working)."""
    entry.runtime_data.capabilities.module_enabled = lambda domain_id, module_id: access(entry, module_id, domain_id)["enabled"]
    loaded = []
    for manifest in await hass.async_add_executor_job(_manifests):
        module = LoadedModule(manifest)
        if manifest.get("enabled_by_default") is False:
            _DEFAULT_OFF.add(manifest["id"])
        try:
            code = await hass.async_add_import_executor_job(importlib.import_module, f".{manifest['id']}", __name__)
            await code.async_setup_module(ModuleContext(hass, entry, module))
        except Exception:  # noqa: BLE001
            LOGGER.exception("Module %s failed to load", manifest.get("id"))
            continue
        loaded.append(module)
    return loaded
