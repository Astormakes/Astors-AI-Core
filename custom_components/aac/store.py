"""AAC settings, stored in .storage/aac.config (edited only through the panel)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
import re
import uuid

from homeassistant.const import CONF_API_KEY, CONF_LLM_HASS_API, CONF_PROMPT
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
    DEFAULT_PROMPT,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DOMAIN,
)

STORAGE_VERSION = 1

# Tested with Qwen3-8B and Qwen3.6-35B: alarms reported 12/12, quiet home silent.
# Stating the NOTHING rule several times made both models swallow real alarms.
DEFAULT_HEARTBEAT = (
    "Look through the states. If anything needs the user's attention now - an alarm "
    "(smoke, gas, water leak, CO), a door or window OPEN that should not be, a device "
    "that failed, a task due now - tell them in one short sentence. Normal states "
    "(lights, closed doors, weather, time, values in normal ranges) are not worth "
    "mentioning: then answer exactly NOTHING."
)
# Draft from the framework design (section 3b); the owner edits it in the panel.
DEFAULT_CHARTER = (
    "Charter (the owner's values; you follow it and never change it):\n"
    "- Honesty over comfort: never hide or soften facts to be liked, never fake progress, "
    "say when you made a mistake.\n"
    "- Nudge, don't nag: suggest something at most once, accept \"no\", no guilt, no fake urgency.\n"
    "- My decisions stay mine: you advise, I decide. Ask before anything that cannot be "
    "undone or leaves the house (buying, sending, unlocking).\n"
    "- Little noise: speak up only when it matters.\n"
    "- What you learn about me stays private.\n"
    "- Help me manage without you: explain, don't only do.\n"
    "- Limits are not puzzles: if a rule stops you, say so instead of working around it."
)
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
    # AAC's own audio pipeline (engines are the pool roles "stt"/"tts"): language (None = detect),
    # the names that address AAC by voice, how long a follow-up needs no name (seconds),
    # known = sources that ever connected ({id, name}), for the setup step
    "audio": {"language": None, "names": ["computer"], "follow_up": 10, "known": []},
    # Pools (thinking / voice / vision, above domains): role → ordered list of model ids
    # or HA engine entity ids; the broker uses the first reachable one (pools.py)
    "pools": {"master": [], "memory": [], "stt": [], "tts": []},
    # Per resource: measured speed (tokens/s, moving average), size (billions of parameters),
    # the owner's own tags (e.g. "efficient"): {resource: {speed, samples, params_b, tags}}
    "traits": {},
    # Panel look: a skin id (neon = the original; others are CSS files, see skins.py)
    "ui": {"skin": "neon"},
}

DEFAULT_DOMAIN_ID = "assistant"
DOMAIN_DEFAULTS: dict[str, Any] = {
    "master": {
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
        CONF_THINKING: DEFAULT_THINKING,
    },
    # The master wakes up on its own every few minutes (off until the user turns it on)
    "heartbeat": {
        "enabled": False,
        "interval": 30,
        "quiet_start": "22:00",
        "quiet_end": "08:00",
        # "" = the built-in default (so improvements to it reach everyone)
        "instructions": "",
        # Small models explore every room with tools; the snapshot is usually enough
        "tools": False,
        # Which model checks in: a pool tier (master / fast / smart / small / an own tag)
        "tier": "fast",
    },
    # Triggers in the contract format (spec/trigger.md):
    # {id, name, enabled, when: {type, ...}, do: {template, instruction}, cooldown, quiet_hours}
    "triggers": [],
    # What AAC may do on its own (owner-only). schedule: "off" | "ask" | "allowed"
    "autonomy": {"schedule": "allowed"},
    # The owner's values for every agent; "" = the built-in draft
    "charter": "",
    # Long-term memory (its embedding model is the pool role "memory"; none = keywords only)
    "memory": {"enabled": True},
}

MASTER_KEYS = set(DOMAIN_DEFAULTS["master"])
_PER_DOMAIN = ("master", "heartbeat", "triggers", "autonomy", "charter", "memory")


def _trigger_v1(trig: dict[str, Any]) -> dict[str, Any]:
    """0.4/0.5 stored flat state triggers; convert them to the contract format."""
    if "when" in trig:
        return trig
    return {
        "id": trig["id"],
        "name": trig.get("name", ""),
        "enabled": trig.get("enabled", True),
        "when": {"type": "state", "entity_id": trig.get("entity_id", ""), "to": trig.get("to", "")},
        "do": {"template": "event-reaction", "instruction": trig.get("instruction", "")},
        "cooldown": trig.get("cooldown", 300),
        "quiet_hours": None,
        "created_by": "owner",
        "proposed": False,
    }


def _domain_from(stored: dict[str, Any], domain_id: str, name: str) -> dict[str, Any]:
    """A domain's settings with defaults filled in (stored = the domain dict, or the old flat config)."""
    d = deepcopy(DOMAIN_DEFAULTS)
    master = dict(stored.get("master", {}))
    if "worker" in master:  # 0.4 and older
        master.setdefault(CONF_MASTER_MODEL, master.pop("worker"))
    d["master"].update({k: v for k, v in master.items() if k in MASTER_KEYS})
    d["heartbeat"].update(stored.get("heartbeat", {}))
    d["triggers"] = [_trigger_v1(t) for t in stored.get("triggers", [])]
    d["autonomy"].update(stored.get("autonomy", {}))
    d["charter"] = stored.get("charter", "")
    d["memory"].update({k: v for k, v in stored.get("memory", {}).items() if k != "embed_model"})
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
    def heartbeat(self) -> dict[str, Any]:
        """Heartbeat settings, with the effective instructions filled in."""
        hb = self.data["heartbeat"]
        return {**hb, "instructions": hb["instructions"] or DEFAULT_HEARTBEAT}

    async def async_update_heartbeat(self, changes: dict[str, Any]) -> None:
        """Change heartbeat settings."""
        hb = self.data["heartbeat"]
        hb.update({k: v for k, v in changes.items() if k in hb})
        if hb["instructions"].strip() == DEFAULT_HEARTBEAT.strip():
            hb["instructions"] = ""
        await self.async_save()

    @property
    def triggers(self) -> list[dict[str, Any]]:
        """Event triggers."""
        return self.data["triggers"]

    async def async_save_trigger(self, trigger: dict[str, Any]) -> dict[str, Any]:
        """Add or update a trigger (contract format)."""
        existing = next((t for t in self.triggers if t["id"] == trigger.get("id")), None)
        if existing is None:
            existing = {"id": f"t_{uuid.uuid4().hex[:8]}"}
            self.triggers.append(existing)
        for key in ("name", "enabled", "when", "do", "cooldown", "quiet_hours"):
            existing[key] = trigger.get(key)
        existing["created_by"] = trigger.get("created_by", existing.get("created_by", "owner"))
        existing["proposed"] = bool(trigger.get("proposed", False))
        await self.async_save()
        return existing

    async def async_delete_trigger(self, trigger_id: str) -> None:
        """Remove an event trigger."""
        self.data["triggers"] = [t for t in self.triggers if t["id"] != trigger_id]
        await self.async_save()

    @property
    def autonomy(self) -> dict[str, Any]:
        """What AAC may do on its own."""
        return self.data["autonomy"]

    async def async_set_autonomy(self, changes: dict[str, Any]) -> None:
        """Owner-only."""
        self.autonomy.update({k: v for k, v in changes.items() if k in self.autonomy})
        await self.async_save()

    @property
    def charter(self) -> str:
        """The effective charter."""
        return self.data["charter"] or DEFAULT_CHARTER

    async def async_set_charter(self, text: str) -> None:
        """Change the charter (the built-in draft is stored as "")."""
        self.data["charter"] = "" if text.strip() == DEFAULT_CHARTER.strip() else text.strip()
        await self.async_save()

    @property
    def memory(self) -> dict[str, Any]:
        """Memory settings."""
        return self.data["memory"]

    async def async_update_memory(self, changes: dict[str, Any]) -> None:
        """Change memory settings."""
        self.memory.update({k: v for k, v in changes.items() if k in self.memory})
        await self.async_save()


class AACStore:
    """Loads, holds and saves the AAC settings."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
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
        data["traits"] = dict(stored.get("traits", {}))
        data["ui"].update(stored.get("ui", {}))
        # Up to 0.14 there was one implicit domain: its settings were at the top level
        domains = stored.get("domains") or [_domain_from(stored, DEFAULT_DOMAIN_ID, "Assistant")]
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
        self.data = data
        self.domains = {d["id"]: DomainSettings(self, d) for d in data["domains"]}

    async def async_save(self) -> None:
        """Write to disk."""
        await self._store.async_save(self.data)

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
        data["triggers"] = [t for t in data["triggers"] if t.get("created_by") != "agent"]
        self.data["domains"].append(data)
        self.domains[domain_id] = DomainSettings(self, data)
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

    @property
    def heartbeat(self) -> dict[str, Any]:
        """First domain."""
        return self.default_domain.heartbeat

    async def async_update_heartbeat(self, changes: dict[str, Any]) -> None:
        """First domain."""
        await self.default_domain.async_update_heartbeat(changes)

    @property
    def triggers(self) -> list[dict[str, Any]]:
        """First domain."""
        return self.default_domain.triggers

    async def async_save_trigger(self, trigger: dict[str, Any]) -> dict[str, Any]:
        """First domain."""
        return await self.default_domain.async_save_trigger(trigger)

    async def async_delete_trigger(self, trigger_id: str) -> None:
        """First domain."""
        await self.default_domain.async_delete_trigger(trigger_id)

    @property
    def autonomy(self) -> dict[str, Any]:
        """First domain."""
        return self.default_domain.autonomy

    async def async_set_autonomy(self, changes: dict[str, Any]) -> None:
        """First domain."""
        await self.default_domain.async_set_autonomy(changes)

    @property
    def charter(self) -> str:
        """First domain."""
        return self.default_domain.charter

    async def async_set_charter(self, text: str) -> None:
        """First domain."""
        await self.default_domain.async_set_charter(text)

    @property
    def memory(self) -> dict[str, Any]:
        """First domain."""
        return self.default_domain.memory

    async def async_update_memory(self, changes: dict[str, Any]) -> None:
        """First domain."""
        await self.default_domain.async_update_memory(changes)

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

    async def async_prefer(self, role: str, resource: str) -> None:
        """Make a resource the first choice of a role."""
        await self.async_set_pool(role, [resource, *self.pools.get(role, [])])

    @property
    def audio(self) -> dict[str, Any]:
        """Audio pipeline settings."""
        return self.data["audio"]

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
