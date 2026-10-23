"""AAC settings, stored in .storage/aac.config (edited only through the panel)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
import re
import uuid

from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BASE_URL,
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DOMAIN,
)

STORAGE_VERSION = 1

STORAGE_KEY = f"{DOMAIN}.config"

CONF_MASTER_MODEL = "model"

DEFAULT_CONFIG: dict[str, Any] = {
    # Manual models: {id, name, base_url, api_key, model}
    "models": [],
    # Setup steps the user chose to skip
    "setup": {"skipped": []},
    # Domains: whole AAC setups (assistant, a work helper, …) that share models, pools, audio and
    # skins; each has its own settings (DOMAIN_DEFAULTS), memory, chats and runs
    "domains": [],
    # The domain that owns the voice (microphones talk to it)
    "voice_domain": None,
    # The home's languages, first = primary (languages.py); None = as HA describes the home (language, country)
    "languages": None,
    # AAC's own audio pipeline (engines are the pool roles "stt"/"tts"):
    # the names that address AAC by voice, how long a follow-up needs no name (seconds),
    # known = sources that ever connected ({id, name}), for the setup step
    "audio": {"names": ["computer"], "follow_up": 10, "known": [], "volume": 40, "pause_ms": 800,
              "voices": {},  # language (de, en, …) → {"language": the engine's code, "voice": its voice id}
              "languages_seen": {}},  # language → how often it was heard or spoken
    # Pools (thinking / voice / vision, above domains): role → ordered list of model ids
    # or HA engine entity ids; the pool uses the first reachable one (pools.py)
    "pools": {"master": [], "memory": [], "stt": [], "tts": []},
    # Per resource: measured speed (tokens/s, moving average), size (billions of parameters),
    # the owner's own tags (e.g. "efficient"): {resource: {speed, samples, params_b, tags}}
    "traits": {},
    # Panel look: a skin id (neon = the original; others are CSS files, see skins.py)
    "ui": {"skin": "neon"},
    # Job queue (jobs.py): tell the owner once when no model is available; a job that waited
    # longer than postpone_notice minutes is told so when it finally runs
    "queue": {"notify_no_model": True, "postpone_notice": 10},
    # Everything a domain's graph holds (v1, spec/v1.md), one record per domain:
    # {domain id: {"modules": {module id: {"settings": {key: value}, enabled, name, instructions, access, auto,
    #                                      destructive_needs_owner, hidden}},   (defaults: the manifests, ACCESS_DEFAULTS)
    #              "nodes": {instance id: {"type", "title", "settings"}},       (added node instances)
    #              "wires": [{"from": "a.port", "to": "b.port"}],               (absent = automatic)
    #              "containers": [{id, name, nodes, x, y, w, h}]}}              (node positions stay in the browser)
    # Up to 1.0.0-dev.12: module_settings, module_access and node_view (moved here on load)
    "graphs": {},
    # Read/write per entity, for every domain and module: {entity_id: {"read": bool, "write": bool}}.
    # None = not chosen yet: what HA exposes to Assist may be read and written (entities.py)
    "entity_access": None,
    # Requests from AAC that wait for the owner's OK (requests.py)
    "requests": [],
    # Machines (hosts that run model servers, models.py): {host: {"name": str}}
    "machines": {},
}

DEFAULT_DOMAIN_ID = "assistant"
DOMAIN_DEFAULTS: dict[str, Any] = {
    "master": {
        CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
        CONF_THINKING: DEFAULT_THINKING,
    },
    # The entities this domain can see at all (the owner's tick list; entities.py). Read/write per
    # entity are global (entity_access). None = not chosen yet: what HA exposes to Assist
    "visible": None,
}

MASTER_KEYS = set(DOMAIN_DEFAULTS["master"])


def _domain_from(stored: dict[str, Any], domain_id: str, name: str) -> dict[str, Any]:
    """A domain's settings with defaults filled in (stored = the domain dict, or the old flat config)."""
    d = deepcopy(DOMAIN_DEFAULTS)
    master = dict(stored.get("master", {}))
    if "worker" in master:  # 0.4 and older
        master.setdefault(CONF_MASTER_MODEL, master.pop("worker"))
    d["master"].update({k: v for k, v in master.items() if k in MASTER_KEYS})
    if stored.get("triggers"):  # up to 0.44: the triggers module moves them to its own storage on its first start
        d["triggers"] = list(stored["triggers"])
    if isinstance(stored.get("visible"), list):
        d["visible"] = list(stored["visible"])
    elif isinstance(stored.get("entities"), dict):  # 0.21 kept one level list per domain
        d["visible"] = list(stored["entities"])
    else:
        d["visible"] = None
    return {"id": domain_id, "name": name, **d}


class DomainSettings:
    """One domain's settings (stored inside the AAC config)."""

    def __init__(self, store: AACStore, data: dict[str, Any]) -> None:
        """Initialize."""
        self._store = store
        self.data = data

    async def async_save(self) -> None:
        """Save the whole config."""
        await self._store.async_save()

    @property
    def store(self) -> AACStore:
        """The whole config (models, pools, entity access are shared)."""
        return self._store

    @property
    def id(self) -> str:
        """Stable id (also in entity and storage names)."""
        return self.data["id"]

    @property
    def name(self) -> str:
        """Shown in the panel."""
        return self.data["name"]

    @property
    def master(self) -> dict[str, Any]:
        """Master agent settings."""
        return self.data["master"]

    async def async_update_master(self, changes: dict[str, Any]) -> None:
        """Change master settings (unknown keys are ignored)."""
        self.master.update({k: v for k, v in changes.items() if k in MASTER_KEYS})
        await self.async_save()

    @property
    def visible(self) -> list[str] | None:
        """The entities this domain can see at all (None = not chosen: HA's Assist exposure)."""
        return self.data["visible"]

    async def async_set_visible(self, entity_ids: list[str]) -> None:
        """The owner's tick list."""
        self.data["visible"] = sorted(set(entity_ids))
        await self.async_save()


class AACStore:
    """Loads, holds and saves the AAC settings."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.hass = hass
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = deepcopy(DEFAULT_CONFIG)
        self.domains: dict[str, DomainSettings] = {}

    async def async_load(self) -> None:
        """Load from disk, filling in defaults for missing keys."""
        stored = await self._store.async_load() or {}
        data = deepcopy(DEFAULT_CONFIG)
        # 0.4 and older called models "workers"
        data["models"] = stored.get("models", stored.get("workers", []))
        data["setup"].update(stored.get("setup", {}))
        data["audio"].update(stored.get("audio", {}))
        data["languages"] = stored.get("languages")
        if forced := data["audio"].pop("language", None):  # 0.41 and older: one language for speech to text
            from .languages import code_of, from_ha  # noqa: PLC0415

            data["languages"] = data["languages"] or list(dict.fromkeys(
                [c for c in [code_of(forced), *from_ha(self.hass.config.language, self.hass.config.country)] if c]))
        data["traits"] = dict(stored.get("traits", {}))
        data["ui"].update(stored.get("ui", {}))
        data["queue"].update(stored.get("queue", {}))
        # The old per-domain places (moved into graphs below, after the older migrations that still write them)
        data["module_settings"] = deepcopy(stored.get("module_settings", {}))
        data["module_access"] = deepcopy(stored.get("module_access", {}))
        data["requests"] = list(stored.get("requests", []))[-100:]
        data["satellite_app"] = dict(stored.get("satellite_app", {}))
        data["machines"] = dict(stored.get("machines", {}))
        if isinstance(stored.get("entity_access"), dict):
            data["entity_access"] = dict(stored["entity_access"])
        elif any(isinstance(d.get("entities"), dict) for d in stored.get("domains", [])):
            # 0.21: per-domain levels → global read/write (the most any domain had)
            access: dict[str, dict[str, bool]] = {}
            for d in stored["domains"]:
                levels = d.get("entities")
                if not isinstance(levels, dict):  # this domain followed HA's Assist exposure
                    # From HA's exposure settings, not the states: during startup many entities have none yet
                    from homeassistant.components.homeassistant.exposed_entities import (  # noqa: PLC0415
                        async_get_assistant_settings,
                    )

                    levels = {entity_id: "control" for entity_id, opts in
                              async_get_assistant_settings(self.hass, "conversation").items() if opts.get("should_expose")}
                for entity_id, lvl in levels.items():
                    cur = access.setdefault(entity_id, {"read": False, "write": False})
                    cur["read"] = True
                    cur["write"] = cur["write"] or lvl == "control"
            data["entity_access"] = access
        # Up to 0.14 there was one implicit domain: its settings were at the top level
        domains = stored.get("domains") or [_domain_from(stored, DEFAULT_DOMAIN_ID, "Assistant")]
        # Up to 0.43 the heartbeat was core: its settings move to the heartbeat module, on or off as it was
        for d in stored.get("domains") or [{**stored, "id": DEFAULT_DOMAIN_ID}]:
            if isinstance(old := d.get("heartbeat"), dict) and old:
                data["module_settings"].setdefault(d["id"], {}).setdefault("heartbeat", {
                    k: old[k] for k in ("interval", "quiet_start", "quiet_end", "instructions", "tools", "tier") if k in old})
                data["module_access"].setdefault(d["id"], {}).setdefault("heartbeat", {})["enabled"] = bool(old.get("enabled"))
            # Up to 0.44 "AAC may plan on its own" was core (autonomy.schedule): now the triggers module's own_plans
            if (old_mode := (d.get("autonomy") or {}).get("schedule")) in ("off", "ask", "allowed"):
                data["module_settings"].setdefault(d["id"], {}).setdefault("triggers", {}).setdefault("own_plans", old_mode)
            # Up to 0.47 the charter was core: the owner's own text becomes the charter module's ("" = built-in)
            if (old_charter := (d.get("charter") or "").strip()):
                data["module_settings"].setdefault(d["id"], {}).setdefault("charter", {}).setdefault("text", old_charter)
            # Up to 0.46 memory was core (memory.enabled): now the memory module's switch
            if (d.get("memory") or {}).get("enabled") is False:
                data["module_access"].setdefault(d["id"], {}).setdefault("memory", {})["enabled"] = False
        data["domains"] = [_domain_from(d, d["id"], d.get("name") or d["id"]) for d in domains]
        ids = [d["id"] for d in data["domains"]]
        data["voice_domain"] = stored.get("voice_domain") if stored.get("voice_domain") in ids else ids[0]
        # Up to 0.11 every role had one model: master.model, memory.embed_model, audio.stt/tts
        master = dict(stored.get("master", {}))
        pools = stored.get("pools") or {
            "master": [master.get(CONF_MASTER_MODEL, master.get("worker"))],
            "memory": [stored.get("memory", {}).get("embed_model")],
            "stt": [stored.get("audio", {}).get("stt")],
            "tts": [stored.get("audio", {}).get("tts")],
        }
        data["pools"].update({role: [r for r in items if r] for role, items in pools.items()})
        for key in ("stt", "tts"):
            data["audio"].pop(key, None)
        # The Mood module was removed in 0.43 (owner: "not helping, just clutter"): its role and settings go too
        data["pools"].pop("mood", None)
        for per_domain in (*data["module_access"].values(), *data["module_settings"].values()):
            per_domain.pop("mood", None)
        # Up to 1.0.0-dev.12 a domain's graph was spread over three places: one record per domain now
        graphs = deepcopy(stored.get("graphs", {}))
        for domain_id, modules in data.pop("module_settings").items():
            for module_id, values in modules.items():
                graphs.setdefault(domain_id, {}).setdefault("modules", {}).setdefault(module_id, {}) \
                    .setdefault("settings", {}).update(values)
        for domain_id, modules in data.pop("module_access").items():
            for module_id, values in modules.items():
                graphs.setdefault(domain_id, {}).setdefault("modules", {}).setdefault(module_id, {}).update(values)
        for domain_id, view in (stored.get("node_view") or {}).items():
            graph = graphs.setdefault(domain_id, {})
            graph.setdefault("nodes", dict(view.get("instances") or {}))
            graph.setdefault("containers", list(view.get("containers") or []))
            if view.get("wires") is not None:
                graph.setdefault("wires", list(view["wires"]))
        data["graphs"] = graphs
        self.data = data
        self.domains = {d["id"]: DomainSettings(self, d) for d in data["domains"]}

    async def async_save(self) -> None:
        """Write to disk."""
        await self._store.async_save(self.data)

    # ------------------------------------------------------------ graphs
    def graph(self, domain_id: str) -> dict[str, Any]:
        """A domain's graph record (created empty when it has none yet): modules, nodes, wires, containers."""
        graph = self.data["graphs"].setdefault(domain_id, {})
        graph.setdefault("modules", {})
        graph.setdefault("nodes", {})
        graph.setdefault("containers", [])
        if not graph.get("time_card"):  # the time as a Trailer card (owner, 2026-10-23); once: it may be removed
            graph["time_card"] = True
            graph["nodes"].setdefault("time", {"type": "trailer", "title": "Time", "auto": True, "settings": {
                "text": "Local time: {{ local_time }}", "replace": False}})
        return graph

    def module_record(self, domain_id: str | None, module_id: str) -> dict[str, Any]:
        """A module's record in a domain's graph, for reading (empty when the owner never set anything)."""
        return (self.data["graphs"].get(domain_id or "") or {}).get("modules", {}).get(module_id) or {}

    # ------------------------------------------------------------ domains
    @property
    def default_domain(self) -> DomainSettings:
        """The first domain (the one that existed before there were several)."""
        return next(iter(self.domains.values()))

    def domain(self, domain_id: str | None) -> DomainSettings | None:
        """A domain by id (None = the first)."""
        return self.default_domain if domain_id is None else self.domains.get(domain_id)

    async def async_create_domain(self, name: str, copy_from: str | None = None) -> DomainSettings:
        """A new domain: empty defaults, or a copy of another one's settings (not its memory or chats)."""
        base = deepcopy(self.domains[copy_from].data) if copy_from in self.domains else {}
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "domain"
        domain_id, n = slug, 2
        while domain_id in self.domains:
            domain_id, n = f"{slug}_{n}", n + 1
        data = _domain_from(base, domain_id, name.strip() or domain_id)
        if not base:
            data["visible"] = []  # a new domain sees nothing until the owner ticks entities
        self.data["domains"].append(data)
        self.domains[domain_id] = DomainSettings(self, data)
        if base and copy_from in self.data["graphs"]:
            # a copy: its graph too (modules switched and set, added nodes, wires, frames); data stays with the original
            self.data["graphs"][domain_id] = deepcopy(self.data["graphs"][copy_from])
        await self.async_save()
        return self.domains[domain_id]

    async def async_rename_domain(self, domain_id: str, name: str) -> None:
        """Change a domain's name (its id stays)."""
        self.domains[domain_id].data["name"] = name.strip() or domain_id
        await self.async_save()

    async def async_delete_domain(self, domain_id: str) -> None:
        """Remove a domain's settings (the first domain cannot be deleted)."""
        if domain_id == self.default_domain.id:
            raise ValueError("The first domain cannot be deleted")
        self.data["domains"] = [d for d in self.data["domains"] if d["id"] != domain_id]
        self.domains.pop(domain_id, None)
        self.data["graphs"].pop(domain_id, None)
        if self.data["voice_domain"] == domain_id:
            self.data["voice_domain"] = self.default_domain.id
        await self.async_save()

    async def async_set_voice_domain(self, domain_id: str) -> None:
        """The domain the microphones talk to."""
        if domain_id in self.domains:
            self.data["voice_domain"] = domain_id
            await self.async_save()

    # The first domain's settings, for code that is not domain-aware (yet)
    @property
    def master(self) -> dict[str, Any]:
        """First domain: master settings."""
        return self.default_domain.master

    async def async_update_master(self, changes: dict[str, Any]) -> None:
        """First domain."""
        await self.default_domain.async_update_master(changes)

    # ------------------------------------------------------------ global
    @property
    def models(self) -> list[dict[str, Any]]:
        """Manual models."""
        return self.data["models"]

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Return a manual model by id."""
        return next((w for w in self.models if w["id"] == model_id), None)

    async def async_save_model(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """Add or update a manual model. A missing api_key keeps the old one."""
        existing = self.get_model(cfg["id"]) if cfg.get("id") else None
        if existing is None:
            existing = {"id": f"m_{uuid.uuid4().hex[:8]}", CONF_API_KEY: None}
            self.models.append(existing)
        for key in ("name", CONF_BASE_URL, CONF_MODEL):
            existing[key] = cfg[key]
        if CONF_API_KEY in cfg:
            existing[CONF_API_KEY] = cfg[CONF_API_KEY] or None
        await self.async_save()
        return existing

    async def async_delete_model(self, model_id: str) -> None:
        """Remove a manual model (and take it out of every pool)."""
        self.data["models"] = [w for w in self.models if w["id"] != model_id]
        for role, items in self.pools.items():
            self.pools[role] = [r for r in items if r != model_id and not r.startswith(f"auto|w:{model_id}|")]
        await self.async_save()

    @property
    def pools(self) -> dict[str, list[str]]:
        """Role → ordered list of resources."""
        return self.data["pools"]

    async def async_set_pool(self, role: str, items: list[str]) -> None:
        """Replace a role's list (duplicates dropped, order kept)."""
        self.pools[role] = list(dict.fromkeys(i for i in items if i))
        await self.async_save()

    @property
    def machines(self) -> dict[str, dict[str, Any]]:
        """Names of the machines that run model servers, by host."""
        return self.data["machines"]

    async def async_set_machine(self, host: str, name: str) -> None:
        """Name a machine (an empty name goes back to the default)."""
        if name.strip():
            self.data["machines"][host] = {**self.data["machines"].get(host, {}), "name": name.strip()}
        else:
            self.data["machines"].pop(host, None)
        await self.async_save()

    @property
    def entity_access(self) -> dict[str, dict[str, bool]] | None:
        """Read/write per entity (None = not chosen: HA's Assist exposure)."""
        return self.data["entity_access"]

    async def async_set_entity_access(self, access: dict[str, dict[str, bool]]) -> None:
        """Replace read/write (the caller merges)."""
        self.data["entity_access"] = {e: {"read": bool(a.get("read") or a.get("write")), "write": bool(a.get("write"))}
                                      for e, a in access.items() if a.get("read") or a.get("write")}
        await self.async_save()

    @property
    def audio(self) -> dict[str, Any]:
        """Audio pipeline settings."""
        return self.data["audio"]

    async def async_set_languages(self, languages: list[str] | None) -> None:
        """The home's languages (first = primary); None = as HA describes the home."""
        self.data["languages"] = list(dict.fromkeys(languages)) if languages else None
        await self.async_save()

    async def async_update_audio(self, changes: dict[str, Any]) -> None:
        """Change audio settings."""
        self.audio.update({k: v for k, v in changes.items() if k in self.audio})
        await self.async_save()

    async def async_add_known_source(self, source_id: str, name: str) -> None:
        """Remember a source that connected once."""
        known = [k for k in self.audio["known"] if k["id"] != source_id]
        self.audio["known"] = [*known, {"id": source_id, "name": name}]
        await self.async_save()

    async def async_set_skipped(self, step: str, skipped: bool) -> None:
        """Mark a setup step as skipped or not."""
        steps = set(self.data["setup"]["skipped"])
        if skipped:
            steps.add(step)
        else:
            steps.discard(step)
        self.data["setup"]["skipped"] = sorted(steps)
        await self.async_save()
