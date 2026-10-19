"""Pools and the broker: which model or engine serves which role, with fallback.

Framework section 7 + owner decision 2026-10-19: three pools (thinking, voice,
vision) that stand above domains. Each role holds an ordered list of resources
(model ids from Models, or HA speech engines). The broker hands out the first
one that is usable; a resource whose machine did not answer (e.g. a desktop PC
that is off) is skipped for a minute and the next one is used. Fallbacks are
visible in the panel and to the setup tools.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

from .models import Endpoint, resolve_model
from .runs import async_notify
from .store import AACStore

if TYPE_CHECKING:
    from . import AACConfigEntry

DOWN_SECONDS = 60

# role → (pool, title, what may be in the list, help)
ROLES: dict[str, dict[str, Any]] = {
    "master": {"pool": "thinking", "title": "Master", "takes": "chat",
               "help": "Thinks and answers. The first reachable model is used, e.g. a big model on a PC that is not always on, then a small one on the server."},
    "memory": {"pool": "thinking", "title": "Memory (embeddings)", "takes": "embedding", "single": True,
               "help": "Finds memories by meaning. One model only: vectors of different models do not mix (when it is unreachable, memory uses keywords)."},
    "stt": {"pool": "voice", "title": "Speech to text", "takes": "speech",
            "help": "A speech model from Models or an HA engine. When none in the list is reachable, HA's local engine is used."},
    "tts": {"pool": "voice", "title": "Voice", "takes": "tts",
            "help": "HA text-to-speech engines (own voice models later). Without a list: local first."},
}
POOLS = {
    "thinking": "Chat models and embeddings",
    "voice": "Speech to text, voices (later speaker ID, wake word)",
    "vision": "Detectors and vision models (planned)",
}


def is_entity(resource: str) -> bool:
    """HA engines are entity ids (stt.*, tts.*), everything else is a model id."""
    return resource.startswith(("stt.", "tts."))


def role_configured(hass: HomeAssistant, store: AACStore, role: str) -> bool:
    """At least one resource of the role exists (reachable or not)."""
    return any(
        (hass.states.get(r) is not None) if is_entity(r) else resolve_model(hass, store, r) is not None
        for r in store.pools.get(role, [])
    )


class Broker:
    """Hands out resources per role and remembers which machines did not answer."""

    def __init__(self, hass: HomeAssistant, store: AACStore) -> None:
        """Initialize."""
        self.hass = hass
        self.store = store
        self._down: dict[str, dict[str, Any]] = {}  # resource → {until, since, error}

    def candidates(self, role: str) -> list[str]:
        """The owner's ordered list for a role."""
        return list(self.store.pools.get(role, []))

    def usable(self, resource: str) -> bool:
        """Known and not marked down."""
        if (down := self._down.get(resource)) and down["until"] > time.monotonic():
            return False
        if is_entity(resource):
            return self.hass.states.get(resource) is not None
        return resolve_model(self.hass, self.store, resource) is not None

    def pick(self, role: str) -> str | None:
        """The first usable resource of a role (memory: only the first, it never switches models)."""
        items = self.candidates(role)
        if ROLES.get(role, {}).get("single"):
            items = items[:1]
        return next((r for r in items if self.usable(r)), None)

    def endpoint(self, role: str) -> tuple[str, Endpoint] | None:
        """The first usable model of a role, resolved."""
        if (resource := self.pick(role)) is None or is_entity(resource):
            return None
        endpoint = resolve_model(self.hass, self.store, resource)
        return (resource, endpoint) if endpoint else None

    def configured(self, role: str) -> bool:
        """At least one resource of the role exists (reachable or not)."""
        return role_configured(self.hass, self.store, role)

    def mark_down(self, resource: str, error: str) -> None:
        """The machine did not answer: skip it for a while."""
        first = resource not in self._down
        self._down[resource] = {
            "until": time.monotonic() + DOWN_SECONDS,
            "since": self._down.get(resource, {}).get("since") or time.time(),
            "error": error,
        }
        if first:
            async_notify(self.hass, "pools")

    def mark_up(self, resource: str) -> None:
        """It answered again."""
        if self._down.pop(resource, None) is not None:
            async_notify(self.hass, "pools")

    def forget(self, resource: str) -> None:
        """Drop a resource from every role (e.g. a deleted model)."""
        self._down.pop(resource, None)

    def status(self) -> dict[str, Any]:
        """Per role: the list with live state and the one in use (for the panel and setup tools)."""
        roles = {}
        for role, meta in ROLES.items():
            active = self.pick(role)
            items = []
            for i, resource in enumerate(self.candidates(role)):
                down = self._down.get(resource)
                state = (
                    "unused" if meta.get("single") and i > 0
                    else "down" if down and down["until"] > time.monotonic()
                    else "ok" if self.usable(resource)
                    else "missing"
                )
                items.append({"id": resource, "state": state, "active": resource == active,
                              "error": down["error"] if down and state == "down" else None})
            roles[role] = {**meta, "items": items, "active": active,
                           "fallback": bool(items) and active != items[0]["id"]}
        return {"pools": POOLS, "roles": roles}


async def async_set_role(hass: HomeAssistant, entry: AACConfigEntry, role: str, items: list[str]) -> None:
    """Change a role's list; a new memory model re-embeds everything in the background."""
    store = entry.runtime_data.store
    before = (store.pools.get(role) or [None])[0]
    await store.async_set_pool(role, items)
    after = (store.pools.get(role) or [None])[0]
    if role == "memory" and after and after != before and entry.runtime_data.memory:
        entry.async_create_background_task(hass, entry.runtime_data.memory.async_reembed(), "aac memory re-embed")
    async_notify(hass, "pools")
