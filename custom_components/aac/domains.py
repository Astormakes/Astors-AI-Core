"""AAC domains at runtime: each domain's own chats and runs (modules keep their own data per domain: lists, triggers, memory).

A domain is a whole AAC setup (the default "Assistant", a work helper, …). Models,
pools, audio and skins are shared (they stand above domains); everything a domain
learns, plans or says stays in it. The first domain keeps the storage names from
before there were several (aac.chats, aac.runs; the memory module: memory.db), so nothing moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .chats import ChatStore
from .const import DOMAIN
from .runs import RunRegistry
from .store import DomainSettings


@dataclass
class Domain:
    """One domain's runtime parts."""

    settings: DomainSettings
    chats: ChatStore
    runs: RunRegistry
    first: bool

    @property
    def id(self) -> str:
        """Stable id."""
        return self.settings.id

    @property
    def name(self) -> str:
        """Shown in the panel and as the HA agent's name."""
        return self.settings.name

    @property
    def suffix(self) -> str:
        """Storage and API name suffix ("" for the first domain)."""
        return "" if self.first else f"_{self.id}"

    def api_id(self, base: str) -> str:
        """This domain's LLM API id, e.g. aac_memory or aac_memory_work."""
        return f"{base}{self.suffix}"

    def unique_id(self, entry_id: str) -> str:
        """Unique id of its conversation entity (the first domain keeps the entry id)."""
        return entry_id if self.first else f"{entry_id}_{self.id}"

    def entity_id(self, hass: HomeAssistant, entry_id: str) -> str | None:
        """Its conversation agent's entity id."""
        return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, self.unique_id(entry_id))


async def async_load_domain(hass: HomeAssistant, settings: DomainSettings, first: bool) -> Domain:
    """Chats and runs of a domain, from storage."""
    suffix = "" if first else f".{settings.id}"
    chats = ChatStore(hass, settings.id, suffix)
    runs = RunRegistry(hass, settings.id, suffix)
    await chats.async_load()
    await runs.async_load()
    return Domain(settings, chats, runs, first)


async def async_remove_domain_data(hass: HomeAssistant, dom: Domain) -> None:
    """Delete a domain's chats, runs and module data for good (never the first domain's; memory: its slot)."""
    if dom.first:
        return
    await dom.chats.store.async_remove()
    await dom.runs.store.async_remove()
    await dom.runs.request_store.async_remove()
    # Modules keep their data per domain as .storage/aac.mod.<module>.<name>.<domain>
    storage = Path(hass.config.path(".storage"))

    def remove_module_data() -> None:
        for path in storage.glob(f"{DOMAIN}.mod.*.*.{dom.id}"):
            path.unlink(missing_ok=True)

    await hass.async_add_executor_job(remove_module_data)
