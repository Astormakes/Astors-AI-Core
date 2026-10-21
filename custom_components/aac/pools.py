"""The pool: which model or engine serves which role, with fallback.

Framework section 7 + owner decision 2026-10-19: three pools (thinking, voice,
vision) that stand above domains. Each role holds an ordered list of resources
(model ids from Models, or HA speech engines). The pool hands out the first
one that is usable; a resource whose machine did not answer (e.g. a desktop PC
that is off) is skipped for a minute and the next one is used. Fallbacks are
visible in the panel and to the setup tools.

Owner, 2026-10-21: this was called the broker; the broker is now the step in front of it that takes
requests, builds their context and records the run (broker.py), and the pool only picks.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

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
    "sounds": {"pool": "voice", "title": "Sound tagger", "takes": "senses",
               "help": "Names the sounds in the room (AudioSet: music, laughter, a door, a motorbike …), for the Sounds module. An AAC senses service (services/aac_senses)."},
    "voices": {"pool": "voice", "title": "Voice prints", "takes": "senses",
               "help": "Tells voices apart (who is talking), for the Voices module. An AAC senses service (services/aac_senses)."},
}
# Requests by trait, over the master role's models (the owner's chat models)
TIERS = {
    "master": "the owner's order",
    "fast": "the fastest reachable model (measured tokens/s)",
    "smart": "the biggest reachable model (parameters)",
    "small": "the smallest reachable model",
}
_PARAMS_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z])")

POOLS = {
    "thinking": "Chat models and embeddings",
    "voice": "Speech to text, voices, sounds and voice prints",
    "vision": "Detectors and vision models (planned)",
}


def is_entity(resource: str) -> bool:
    """HA engines are entity ids (stt.*, tts.*), everything else is a model id."""
    return resource.startswith(("stt.", "tts."))


def params_from_name(name: str) -> float | None:
    """'Qwen3.6-35B-A3B' → 35, 'qwen3-8b' → 8 (the first size in the name: MoE total)."""
    match = _PARAMS_RE.search(name)
    return float(match.group(1)) if match else None


def role_configured(hass: HomeAssistant, store: AACStore, role: str) -> bool:
    """At least one resource of the role exists (reachable or not)."""
    return any(
        (hass.states.get(r) is not None) if is_entity(r) else resolve_model(hass, store, r) is not None
        for r in store.pools.get(role, [])
    )


class Pool:
    """Hands out resources per role and remembers which machines did not answer."""

    def __init__(self, hass: HomeAssistant, store: AACStore) -> None:
        """Initialize."""
        self.hass = hass
        self.store = store
        self._down: dict[str, dict[str, Any]] = {}  # resource → {until, since, error}

    def candidates(self, role: str) -> list[str]:
        """The owner's ordered list for a role."""
        return list(self.store.pools.get(role, []))

    def usable(self, resource: str, background: bool = False) -> bool:
        """Known, not marked down, and allowed by the owner's availability rule right now."""
        if (down := self._down.get(resource)) and down["until"] > time.monotonic():
            return False
        if not self.allowed(resource, background):
            return False
        if is_entity(resource):
            return self.hass.states.get(resource) is not None
        return resolve_model(self.hass, self.store, resource) is not None

    def pick(self, role: str, background: bool = False) -> str | None:
        """The first usable resource of a role (memory: only the first, it never switches models)."""
        items = self.candidates(role)
        if ROLES.get(role, {}).get("single"):
            items = items[:1]
        return next((r for r in items if self.usable(r, background)), None)

    def endpoint(self, role: str, background: bool = False) -> tuple[str, Endpoint] | None:
        """The first usable model of a role, resolved."""
        if (resource := self.pick(role, background)) is None or is_entity(resource):
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

    # ------------------------------------------------------------ availability rules
    def rule(self, resource: str) -> dict[str, Any] | None:
        """The owner's availability rule of a resource (None = always)."""
        return self.store.data["traits"].get(resource, {}).get("rule")

    def allowed(self, resource: str, background: bool = False) -> bool:
        """Does the owner's rule allow using it now? A rule for background jobs never blocks
        the owner's own chats; an entity that is unknown or unavailable counts as not met."""
        if not (rule := self.rule(resource)):
            return True
        if rule.get("applies_to", "background") == "background" and not background:
            return True
        return all(self._condition(c) for c in rule.get("when", []))

    def _condition(self, cond: dict[str, Any]) -> bool:
        if cond.get("type") == "state":
            state = self.hass.states.get(cond.get("entity_id", ""))
            wanted = cond.get("state")
            wanted = wanted if isinstance(wanted, list) else [wanted]
            # unknown/unavailable count as "not met" unless the rule names them (e.g. "now playing" is unknown
            # while nothing runs); "not": true turns it around (then unknown/unavailable count as met)
            if state is None or (state.state in ("unknown", "unavailable") and state.state not in wanted):
                return bool(cond.get("not"))
            return (state.state in wanted) != bool(cond.get("not"))
        if cond.get("type") == "time":
            now = dt_util.now()
            if (days := cond.get("weekdays")) and now.weekday() not in days:
                return False
            hm = now.strftime("%H:%M")
            after, before = cond.get("after", "00:00"), cond.get("before", "24:00")
            return after <= hm < before if after <= before else (hm >= after or hm < before)
        return False

    async def async_set_rule(self, resource: str, rule: dict[str, Any] | None) -> None:
        """Set or clear an availability rule (the owner's decision; AAC writes it only for them)."""
        entry = self._traits_entry(resource)
        if rule:
            entry["rule"] = rule
        else:
            entry.pop("rule", None)
        await self.store.async_save()
        async_notify(self.hass, "pools")

    # ------------------------------------------------------------ traits
    def traits(self, resource: str) -> dict[str, Any]:
        """Measured speed, size and the owner's tags of a resource."""
        t = dict(self.store.data["traits"].get(resource, {}))
        if t.get("params_b") is None and not is_entity(resource):
            endpoint = resolve_model(self.hass, self.store, resource)
            t["params_b"] = params_from_name(endpoint.model) if endpoint else None
        return {"speed": t.get("speed"), "samples": t.get("samples", 0), "params_b": t.get("params_b"),
                "tags": list(t.get("tags", [])), "n_ctx": t.get("n_ctx"), "token_factor": t.get("token_factor", 1.0),
                "tool_mode": t.get("tool_mode", "menu")}

    def learn_context(self, resource: str, n_ctx: int | None = None, factor: float | None = None) -> None:
        """What the server said about its context: its size, and real tokens / AAC's estimate."""
        t = self._traits_entry(resource)
        changed = False
        if n_ctx and t.get("n_ctx") != n_ctx:
            t["n_ctx"], changed = int(n_ctx), True
        if factor and 0.3 < factor < 4:
            t["token_factor"], changed = round(factor, 2), True
        if changed:
            self.hass.async_create_task(self.store.async_save())

    def _traits_entry(self, resource: str) -> dict[str, Any]:
        return self.store.data["traits"].setdefault(resource, {})

    def record_speed(self, resource: str, tokens_per_second: float | None) -> None:
        """A finished call: update the moving average (the newest counts 30 %)."""
        if not tokens_per_second or tokens_per_second <= 0:
            return
        t = self._traits_entry(resource)
        old = t.get("speed")
        t["speed"] = round(tokens_per_second if old is None else 0.7 * old + 0.3 * tokens_per_second, 1)
        t["samples"] = t.get("samples", 0) + 1
        self.hass.async_create_task(self.store.async_save())

    def learn_params(self, resource: str, n_params: int | float | None) -> None:
        """The server told the real size (llama.cpp meta n_params)."""
        if n_params:
            params_b = round(float(n_params) / 1e9, 1)
            t = self._traits_entry(resource)
            if t.get("params_b") != params_b:
                t["params_b"] = params_b
                self.hass.async_create_task(self.store.async_save())

    async def async_set_tags(self, resource: str, tags: list[str]) -> None:
        """The owner's own tags (e.g. efficient)."""
        self._traits_entry(resource)["tags"] = sorted({t.strip().lower() for t in tags if t.strip()})
        await self.store.async_save()
        async_notify(self.hass, "pools")

    async def async_set_tool_mode(self, resource: str, mode: str) -> None:
        """How a model gets its tools: menu (areas, opened on demand) or all (every tool up front)."""
        self._traits_entry(resource)["tool_mode"] = mode
        await self.store.async_save()
        async_notify(self.hass, "pools")

    def learn_from_overview(self, overview: dict[str, Any]) -> None:
        """Sizes the servers reported (Models page scan)."""
        for m in overview.get("manual", []):
            self.learn_params(m["id"], m.get("n_params"))
        for srv in overview.get("auto", []):
            for m in srv.get("models", []):
                self.learn_params(m["id"], m.get("n_params"))

    async def async_measure(self, resource: str) -> float | None:
        """One short generation to learn the speed of a model (e.g. one that was never used)."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415

        from .provider import OpenAICompatProvider, ProviderConnectionError, ProviderError  # noqa: PLC0415

        if (endpoint := resolve_model(self.hass, self.store, resource)) is None:
            return None
        provider = OpenAICompatProvider(
            async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key, timeout=120, connect_timeout=3
        )
        try:
            reply = await provider.chat(
                endpoint.model,
                [{"role": "user", "content": "Write the numbers from 1 to 60, separated by spaces."}],
                max_tokens=200, temperature=0, chat_template_kwargs={"enable_thinking": False},
            )
        except ProviderConnectionError as err:
            self.mark_down(resource, str(err))
            return None
        except ProviderError:
            return None
        self.mark_up(resource)
        speed = (reply.get("_stats") or {}).get("tokens_per_second")
        self.record_speed(resource, speed)
        return speed

    def derived_tags(self, background: bool = False) -> dict[str, list[str]]:
        """fast / smart / small among the master role's usable models, plus the owner's tags."""
        models = [r for r in self.candidates("master") if not is_entity(r) and self.usable(r, background)]
        tags = {r: list(self.traits(r)["tags"]) for r in models}
        timed = [r for r in models if self.traits(r)["speed"]]
        sized = [r for r in models if self.traits(r)["params_b"]]
        if timed:
            tags[max(timed, key=lambda r: self.traits(r)["speed"])].append("fast")
        if sized:
            tags[max(sized, key=lambda r: self.traits(r)["params_b"])].append("smart")
            if len(sized) > 1:
                tags[min(sized, key=lambda r: self.traits(r)["params_b"])].append("small")
        return tags

    def pick_tier(self, tier: str, background: bool = False) -> str | None:
        """A model for a request: master = the owner's order; fast/smart/small/own tag = among the
        usable master models; nothing fits → the master's first usable. Background jobs also
        respect the owner's availability rules."""
        if tier and tier != "master":
            for resource, tags in self.derived_tags(background).items():
                if tier in tags:
                    return resource
            tagged = [r for r in self.candidates("master") if tier in self.traits(r)["tags"] and self.usable(r, background)]
            if tagged:
                return tagged[0]
        return self.pick("master", background)

    def endpoint_for(self, tier: str, background: bool = False) -> tuple[str, Endpoint] | None:
        """pick_tier, resolved."""
        if (resource := self.pick_tier(tier, background)) is None:
            return None
        endpoint = resolve_model(self.hass, self.store, resource)
        return (resource, endpoint) if endpoint else None

    def status(self) -> dict[str, Any]:
        """Per role: the list with live state and the one in use (for the panel and setup tools)."""
        roles = {}
        derived = self.derived_tags()
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
                item = {"id": resource, "state": state, "active": resource == active,
                        "error": down["error"] if down and state == "down" else None}
                if role == "master":
                    item |= {"traits": self.traits(resource), "tags": derived.get(resource, []),
                             "rule": self.rule(resource), "background_now": self.usable(resource, True)}
                items.append(item)
            roles[role] = {**meta, "items": items, "active": active,
                           "fallback": bool(items) and active != items[0]["id"]}
        return {"pools": POOLS, "roles": roles, "tiers": TIERS,
                "by_tier": {tier: self.pick_tier(tier) for tier in TIERS},
                "by_tier_background": {tier: self.pick_tier(tier, True) for tier in TIERS}}


async def async_set_role(hass: HomeAssistant, entry: AACConfigEntry, role: str, items: list[str]) -> None:
    """Change a role's list; a new memory model re-embeds everything in the background."""
    store = entry.runtime_data.store
    before = (store.pools.get(role) or [None])[0]
    await store.async_set_pool(role, items)
    after = (store.pools.get(role) or [None])[0]
    if role == "memory" and after and after != before:
        for dom in entry.runtime_data.domains.values():  # every domain's memory uses the shared model
            if dom.memory:
                entry.async_create_background_task(hass, dom.memory.async_reembed(), f"aac memory re-embed {dom.id}")
    async_notify(hass, "pools")
