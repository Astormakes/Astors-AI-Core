"""AAC settings, stored in .storage/aac.config (edited only through the panel)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
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
    "master": {
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
        CONF_THINKING: DEFAULT_THINKING,
    },
    # Setup steps the user chose to skip
    "setup": {"skipped": []},
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

MASTER_KEYS = set(DEFAULT_CONFIG["master"])


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


class AACStore:
    """Loads, holds and saves the AAC settings."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self.data: dict[str, Any] = deepcopy(DEFAULT_CONFIG)

    async def async_load(self) -> None:
        """Load from disk, filling in defaults for missing keys."""
        stored = await self._store.async_load() or {}
        data = deepcopy(DEFAULT_CONFIG)
        # 0.4 and older called models "workers" and the master's model "worker"
        data["models"] = stored.get("models", stored.get("workers", []))
        master = dict(stored.get("master", {}))
        if "worker" in master:
            master.setdefault(CONF_MASTER_MODEL, master.pop("worker"))
        data["master"].update(master)
        data["setup"].update(stored.get("setup", {}))
        data["heartbeat"].update(stored.get("heartbeat", {}))
        data["triggers"] = [_trigger_v1(t) for t in stored.get("triggers", [])]
        data["memory"].update(stored.get("memory", {}))
        data["audio"].update(stored.get("audio", {}))
        data["traits"] = dict(stored.get("traits", {}))
        data["ui"].update(stored.get("ui", {}))
        data["charter"] = stored.get("charter", "")
        data["autonomy"].update(stored.get("autonomy", {}))
        # Up to 0.11 every role had one model: master.model, memory.embed_model, audio.stt/tts
        pools = stored.get("pools") or {
            "master": [master.get(CONF_MASTER_MODEL)],
            "memory": [stored.get("memory", {}).get("embed_model")],
            "stt": [stored.get("audio", {}).get("stt")],
            "tts": [stored.get("audio", {}).get("tts")],
        }
        data["pools"].update({role: [r for r in items if r] for role, items in pools.items()})
        data["master"].pop(CONF_MASTER_MODEL, None)
        data["memory"].pop("embed_model", None)
        for key in ("stt", "tts"):
            data["audio"].pop(key, None)
        self.data = data

    async def async_save(self) -> None:
        """Write to disk."""
        await self._store.async_save(self.data)

    @property
    def master(self) -> dict[str, Any]:
        """Master agent settings."""
        return self.data["master"]

    @property
    def models(self) -> list[dict[str, Any]]:
        """Manual models."""
        return self.data["models"]

    def get_model(self, model_id: str) -> dict[str, Any] | None:
        """Return a manual model by id."""
        return next((w for w in self.models if w["id"] == model_id), None)

    async def async_update_master(self, changes: dict[str, Any]) -> None:
        """Change master settings (unknown keys are ignored)."""
        self.master.update({k: v for k, v in changes.items() if k in MASTER_KEYS})
        await self.async_save()

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
    def heartbeat(self) -> dict[str, Any]:
        """Heartbeat settings, with the effective instructions filled in."""
        hb = self.data["heartbeat"]
        return {**hb, "instructions": hb["instructions"] or DEFAULT_HEARTBEAT}

    @property
    def triggers(self) -> list[dict[str, Any]]:
        """Event triggers."""
        return self.data["triggers"]

    async def async_update_heartbeat(self, changes: dict[str, Any]) -> None:
        """Change heartbeat settings."""
        hb = self.data["heartbeat"]
        hb.update({k: v for k, v in changes.items() if k in hb})
        if hb["instructions"].strip() == DEFAULT_HEARTBEAT.strip():
            hb["instructions"] = ""
        await self.async_save()

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
