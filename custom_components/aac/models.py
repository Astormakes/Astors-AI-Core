"""Workers: the models AAC can use.

- Manual models are made by the user in the panel (stored in AACStore).
- Auto models are found: model servers already configured in HA
  (llama.cpp, Ollama integrations) and the servers of manual models.
  Every model such a server offers becomes an auto model.

A model id resolves to (base_url, api_key, model):
- manual: "m_xxxxxxxx"
- auto:   "auto|<source>|<model>", source = HA entry id or "w:<manual model id>"
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
from typing import Any

from homeassistant.const import CONF_API_KEY, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, CONF_MODEL
from .provider import OpenAICompatProvider, ProviderError, ProviderNotFoundError
from .store import AACStore

# HA integrations whose config entries point at an OpenAI-compatible server
_KNOWN_INTEGRATIONS = {
    "llama_cpp": ("llama.cpp", lambda d: d.get(CONF_BASE_URL)),
    "ollama": (
        "Ollama",
        lambda d: f"{d[CONF_URL].rstrip('/')}/v1" if d.get(CONF_URL) else None,
    ),
}


_EMBED_HINT = re.compile(r"embed|bge|e5|nomic|minilm|gte", re.I)
_SPEECH_HINT = re.compile(r"whisper|large-v\d|parakeet|canary|voxtral|moonshine|speech|stt|asr", re.I)


def model_kind(model: str) -> str:
    """Best guess from the name: "embedding", "speech" (to text) or "chat"."""
    if _SPEECH_HINT.search(model):
        return "speech"
    return "embedding" if _EMBED_HINT.search(model) else "chat"


@dataclass
class Server:
    """A model server AAC knows about."""

    source: str
    label: str
    base_url: str
    api_key: str | None


@dataclass
class Endpoint:
    """What a model id resolves to."""

    base_url: str
    api_key: str | None
    model: str


def known_servers(hass: HomeAssistant, store: AACStore) -> list[Server]:
    servers: dict[str, Server] = {}
    for cfg in store.models:
        url = cfg[CONF_BASE_URL].rstrip("/")
        servers.setdefault(
            url,
            Server(f"w:{cfg['id']}", cfg["name"], url, cfg.get(CONF_API_KEY)),
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


def resolve_model(
    hass: HomeAssistant, store: AACStore, model_id: str | None
) -> Endpoint | None:
    """Turn a model id into something the provider can call."""
    if not model_id:
        return None
    if model_id.startswith("auto|"):
        _, source, model = model_id.split("|", 2)
        if server := _server_for_source(hass, store, source):
            return Endpoint(server.base_url, server.api_key, model)
        return None
    if cfg := store.get_model(model_id):
        return Endpoint(cfg[CONF_BASE_URL], cfg.get(CONF_API_KEY), cfg[CONF_MODEL])
    return None


async def async_list_models(
    hass: HomeAssistant, base_url: str, api_key: str | None, timeout: float = 5
) -> list[str]:
    """List models of a server (raises ProviderError)."""
    provider = OpenAICompatProvider(async_get_clientsession(hass), base_url, api_key)
    async with asyncio.timeout(timeout):
        return await provider.list_models()


async def _probe(hass: HomeAssistant, server: Server) -> dict[str, Any]:
    provider = OpenAICompatProvider(async_get_clientsession(hass), server.base_url, server.api_key)
    try:
        async with asyncio.timeout(5):
            meta = await provider.models_meta()
        models = list(meta)
    except ProviderNotFoundError:
        # Reachable, but cannot list its models (e.g. whisper.cpp): manual models count as online
        return {"online": True, "models": [], "error": None, "unlisted": True}
    except (ProviderError, TimeoutError) as err:
        return {"online": False, "models": [], "error": str(err) or "timeout"}
    return {"online": True, "models": models, "error": None, "meta": meta}


async def async_overview(hass: HomeAssistant, store: AACStore) -> dict[str, Any]:
    """Everything the Models page shows, with live state."""
    servers = known_servers(hass, store)
    probes = await asyncio.gather(*(_probe(hass, s) for s in servers))
    by_url = {s.base_url: p for s, p in zip(servers, probes, strict=True)}

    manual = []
    taken: set[tuple[str, str]] = set()
    for cfg in store.models:
        url = cfg[CONF_BASE_URL].rstrip("/")
        probe = by_url.get(url, {"online": False, "models": [], "error": None})
        taken.add((url, cfg[CONF_MODEL]))
        # A server with a single model serves it whatever it is called (llama.cpp names it by its file path
        # and ignores the name in requests); count it as this entry, not as a second model
        single = probe["online"] and len(probe["models"]) == 1
        if single:
            taken.add((url, probe["models"][0]))
        manual.append(
            {
                "id": cfg["id"],
                "name": cfg["name"],
                "base_url": cfg[CONF_BASE_URL],
                "model": cfg[CONF_MODEL],
                "has_key": bool(cfg.get(CONF_API_KEY)),
                "online": probe["online"] and (cfg[CONF_MODEL] in probe["models"] or probe.get("unlisted", False) or single),
                "n_params": (probe.get("meta") or {}).get(cfg[CONF_MODEL], {}).get("n_params"),
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
                "models": [
                    {"id": f"auto|{server.source}|{model}", "model": model,
                     "n_params": (probe.get("meta") or {}).get(model, {}).get("n_params")}
                    for model in probe["models"]
                    if (server.base_url, model) not in taken
                ],
            }
        )
    return {"manual": manual, "auto": auto}
