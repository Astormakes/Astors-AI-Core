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
STORAGE_KEY = f"{DOMAIN}.config"

CONF_MASTER_MODEL = "model"

DEFAULT_CONFIG: dict[str, Any] = {
    # Manual models: {id, name, base_url, api_key, model}
    "models": [],
    "master": {
        CONF_MASTER_MODEL: None,
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
    },
    # Event triggers: {id, name, entity_id, to, instruction, cooldown, enabled}
    "triggers": [],
    # Long-term memory; embed_model = a model id from Models (None: keywords only)
    "memory": {"enabled": True, "embed_model": None},
}

MASTER_KEYS = set(DEFAULT_CONFIG["master"])


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
        data["triggers"] = stored.get("triggers", [])
        data["memory"].update(stored.get("memory", {}))
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
        """Remove a manual model (and unset it as master)."""
        self.data["models"] = [w for w in self.models if w["id"] != model_id]
        if self.master[CONF_MASTER_MODEL] == model_id:
            self.master[CONF_MASTER_MODEL] = None
        await self.async_save()

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
        """Add or update an event trigger."""
        existing = next((t for t in self.triggers if t["id"] == trigger.get("id")), None)
        if existing is None:
            existing = {"id": f"t_{uuid.uuid4().hex[:8]}"}
            self.triggers.append(existing)
        for key in ("name", "entity_id", "to", "instruction", "cooldown", "enabled"):
            existing[key] = trigger[key]
        await self.async_save()
        return existing

    async def async_delete_trigger(self, trigger_id: str) -> None:
        """Remove an event trigger."""
        self.data["triggers"] = [t for t in self.triggers if t["id"] != trigger_id]
        await self.async_save()

    @property
    def memory(self) -> dict[str, Any]:
        """Memory settings."""
        return self.data["memory"]

    async def async_update_memory(self, changes: dict[str, Any]) -> None:
        """Change memory settings."""
        self.memory.update({k: v for k, v in changes.items() if k in self.memory})
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
