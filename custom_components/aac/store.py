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
STORAGE_KEY = f"{DOMAIN}.config"

CONF_WORKER = "worker"

DEFAULT_CONFIG: dict[str, Any] = {
    # Manual workers: {id, name, base_url, api_key, model}
    "workers": [],
    "master": {
        CONF_WORKER: None,
        CONF_PROMPT: DEFAULT_PROMPT,
        CONF_LLM_HASS_API: [llm.LLM_API_ASSIST],
        CONF_TEMPERATURE: DEFAULT_TEMPERATURE,
        CONF_MAX_TOKENS: DEFAULT_MAX_TOKENS,
        CONF_THINKING: DEFAULT_THINKING,
    },
    # Setup steps the user chose to skip
    "setup": {"skipped": []},
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
        data["workers"] = stored.get("workers", [])
        data["master"].update(stored.get("master", {}))
        data["setup"].update(stored.get("setup", {}))
        self.data = data

    async def async_save(self) -> None:
        """Write to disk."""
        await self._store.async_save(self.data)

    @property
    def master(self) -> dict[str, Any]:
        """Master agent settings."""
        return self.data["master"]

    @property
    def workers(self) -> list[dict[str, Any]]:
        """Manual workers."""
        return self.data["workers"]

    def get_worker(self, worker_id: str) -> dict[str, Any] | None:
        """Return a manual worker by id."""
        return next((w for w in self.workers if w["id"] == worker_id), None)

    async def async_update_master(self, changes: dict[str, Any]) -> None:
        """Change master settings (unknown keys are ignored)."""
        self.master.update({k: v for k, v in changes.items() if k in MASTER_KEYS})
        await self.async_save()

    async def async_save_worker(self, worker: dict[str, Any]) -> dict[str, Any]:
        """Add or update a manual worker. A missing api_key keeps the old one."""
        existing = self.get_worker(worker["id"]) if worker.get("id") else None
        if existing is None:
            existing = {"id": f"m_{uuid.uuid4().hex[:8]}", CONF_API_KEY: None}
            self.workers.append(existing)
        for key in ("name", CONF_BASE_URL, CONF_MODEL):
            existing[key] = worker[key]
        if CONF_API_KEY in worker:
            existing[CONF_API_KEY] = worker[CONF_API_KEY] or None
        await self.async_save()
        return existing

    async def async_delete_worker(self, worker_id: str) -> None:
        """Remove a manual worker (and unset it as master)."""
        self.data["workers"] = [w for w in self.workers if w["id"] != worker_id]
        if self.master[CONF_WORKER] == worker_id:
            self.master[CONF_WORKER] = None
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
