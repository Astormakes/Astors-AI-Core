"""Workers: the models AAC can use.

- Manual workers are made by the user in the panel (stored in AACStore).
- Auto workers are found: model servers already configured in HA
  (llama.cpp, Ollama integrations) and the servers of manual workers.
  Every model such a server offers becomes an auto worker.

A worker id resolves to (base_url, api_key, model):
- manual: "m_xxxxxxxx"
- auto:   "auto|<source>|<model>", source = HA entry id or "w:<manual worker id>"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, CONF_MODEL
from .provider import OpenAICompatProvider, ProviderError
from .store import AACStore

# HA integrations whose config entries point at an OpenAI-compatible server
_KNOWN_INTEGRATIONS = {
    "llama_cpp": ("llama.cpp", lambda d: d.get(CONF_BASE_URL)),
    "ollama": (
        "Ollama",
        lambda d: f"{d[CONF_URL].rstrip('/')}/v1" if d.get(CONF_URL) else None,
    ),
}


@dataclass
class Server:
    """A model server AAC knows about."""

    source: str
    label: str
    base_url: str
    api_key: str | None


@dataclass
class Endpoint:
    """What a worker resolves to."""

    base_url: str
    api_key: str | None
    model: str


def known_servers(hass: HomeAssistant, store: AACStore) -> list[Server]:
    servers: dict[str, Server] = {}
    for worker in store.workers:
        url = worker[CONF_BASE_URL].rstrip("/")
        servers.setdefault(
            url,
            Server(f"w:{worker['id']}", worker["name"], url, worker.get(CONF_API_KEY)),
        )
    for domain, (label, get_url) in _KNOWN_INTEGRATIONS.items():
        for entry in hass.config_entries.async_entries(domain):
            if not (url := get_url(entry.data)):
                continue
            url = url.rstrip("/")
            servers.setdefault(
                url,
                Server(entry.entry_id, f"{label}: {entry.title}", url,
                       entry.data.get(CONF_API_KEY)),
            )
    return list(servers.values())


def _server_for_source(hass: HomeAssistant, store: AACStore, source: str):
    return next((s for s in known_servers(hass, store) if s.source == source), None)


def resolve_worker(
    hass: HomeAssistant, store: AACStore, worker_id: str | None
) -> Endpoint | None:
    """Turn a worker id into something the provider can call."""
    if not worker_id:
        return None
    if worker_id.startswith("auto|"):
        _, source, model = worker_id.split("|", 2)
        if server := _server_for_source(hass, store, source):
            return Endpoint(server.base_url, server.api_key, model)
        return None
    if worker := store.get_worker(worker_id):
        return Endpoint(worker[CONF_BASE_URL], worker.get(CONF_API_KEY), worker[CONF_MODEL])
    return None


async def async_list_models(
    hass: HomeAssistant, base_url: str, api_key: str | None, timeout: float = 5
) -> list[str]:
    """List models of a server (raises ProviderError)."""
    provider = OpenAICompatProvider(async_get_clientsession(hass), base_url, api_key)
    async with asyncio.timeout(timeout):
        return await provider.list_models()


async def _probe(hass: HomeAssistant, server: Server) -> dict[str, Any]:
    try:
        models = await async_list_models(hass, server.base_url, server.api_key)
    except (ProviderError, TimeoutError) as err:
        return {"online": False, "models": [], "error": str(err) or "timeout"}
    return {"online": True, "models": models, "error": None}


async def async_overview(hass: HomeAssistant, store: AACStore) -> dict[str, Any]:
    """Everything the WORKERS segment shows, with live state."""
    servers = known_servers(hass, store)
    probes = await asyncio.gather(*(_probe(hass, s) for s in servers))
    by_url = {s.base_url: p for s, p in zip(servers, probes, strict=True)}

    manual = []
    taken: set[tuple[str, str]] = set()
    for worker in store.workers:
        url = worker[CONF_BASE_URL].rstrip("/")
        probe = by_url.get(url, {"online": False, "models": [], "error": None})
        taken.add((url, worker[CONF_MODEL]))
        manual.append(
            {
                "id": worker["id"],
                "name": worker["name"],
                "base_url": worker[CONF_BASE_URL],
                "model": worker[CONF_MODEL],
                "has_key": bool(worker.get(CONF_API_KEY)),
                "online": probe["online"] and worker[CONF_MODEL] in probe["models"],
                "error": probe["error"],
            }
        )

    auto = []
    for server, probe in zip(servers, probes, strict=True):
        auto.append(
            {
                "source": server.source,
                "label": server.label,
                "base_url": server.base_url,
                "online": probe["online"],
                "error": probe["error"],
                "workers": [
                    {"id": f"auto|{server.source}|{model}", "model": model}
                    for model in probe["models"]
                    if (server.base_url, model) not in taken
                ],
            }
        )
    return {"manual": manual, "auto": auto}
