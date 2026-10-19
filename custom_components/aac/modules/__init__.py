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

from ..capabilities import CalendarProvider
from ..const import DOMAIN, LOGGER

if TYPE_CHECKING:
    from .. import AACConfigEntry
    from ..domains import Domain

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
        """For the panel (pages a domain switched off are left out)."""
        m = self.manifest
        pages = [p for p in self.pages if not (entry and domain_id and p.get("visible_setting"))
                 or setting(entry, m, domain_id, p["visible_setting"])]
        return {"id": m["id"], "name": m.get("name", m["id"]), "version": m.get("version"),
                "description": m.get("description", ""), "pages": pages}


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
        """Provide (and maybe take) calendar entries."""
        self.entry.async_on_unload(self.entry.runtime_data.capabilities.add_calendar(provider))

    def add_llm_api(self, factory: Callable[[Domain, str, str], llm.API]) -> None:
        """Tools for the agents: factory(domain, api_id, name) → an llm.API, registered per domain."""
        title = self.module.manifest.get("name", self.module.id)
        for dom in self.domains.values():
            api_id = dom.api_id(f"aac_{self.module.id}")
            name = f"AAC {title.lower()}" + ("" if dom.first else f" ({dom.name})")
            self.entry.async_on_unload(llm.async_register_api(self.hass, factory(dom, api_id, name)))
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
    loaded = []
    for manifest in await hass.async_add_executor_job(_manifests):
        module = LoadedModule(manifest)
        try:
            code = await hass.async_add_import_executor_job(importlib.import_module, f".{manifest['id']}", __name__)
            await code.async_setup_module(ModuleContext(hass, entry, module))
        except Exception:  # noqa: BLE001
            LOGGER.exception("Module %s failed to load", manifest.get("id"))
            continue
        loaded.append(module)
    return loaded
