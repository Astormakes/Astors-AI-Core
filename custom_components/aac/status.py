"""Setup steps and voice pipeline overview for the panel."""

from __future__ import annotations

from typing import Any

from homeassistant.components import assist_pipeline
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .store import CONF_WORKER, AACStore
from .workers import known_servers, resolve_worker

HA_AGENT = "conversation.home_assistant"


def aac_agent_entity_id(hass: HomeAssistant, entry_id: str) -> str | None:
    """Entity id of AAC's conversation agent."""
    return er.async_get(hass).async_get_entity_id("conversation", DOMAIN, entry_id)


def _pipelines_routed(hass: HomeAssistant, agent: str | None) -> bool:
    if not agent or "assist_pipeline" not in hass.config.components:
        return False
    return any(
        p.conversation_engine == agent for p in assist_pipeline.async_get_pipelines(hass)
    )


def setup_steps(hass: HomeAssistant, store: AACStore, entry_id: str) -> list[dict]:
    """The SETUP workflow. A step is closed when done or skipped."""
    skipped = set(store.data["setup"]["skipped"])
    steps = [
        {
            "id": "worker",
            "title": "Connect a model",
            "hint": "Add a model server, or let AAC find the ones HA already knows.",
            "target": "workers",
            "optional": False,
            "done": bool(known_servers(hass, store)),
        },
        {
            "id": "master",
            "title": "Choose the master model",
            "hint": "The model that thinks and answers for AAC.",
            "target": "settings/master",
            "optional": False,
            "done": resolve_worker(hass, store, store.master[CONF_WORKER]) is not None,
        },
        {
            "id": "pipeline",
            "title": "Use AAC for voice",
            "hint": "Route a voice pipeline through AAC.",
            "target": "settings/pipelines",
            "optional": True,
            "done": _pipelines_routed(hass, aac_agent_entity_id(hass, entry_id)),
        },
    ]
    for step in steps:
        step["skipped"] = step["id"] in skipped and not step["done"]
    return steps


def _stage(hass: HomeAssistant, kind: str, engine: str | None) -> dict[str, Any]:
    if not engine:
        return {"kind": kind, "engine": None, "label": "—", "state": "off"}
    if "." not in engine:
        # Legacy agent id (config entry id), can't check further
        return {"kind": kind, "engine": engine, "label": engine, "state": "ok"}
    state = hass.states.get(engine)
    ok = state is not None and state.state != STATE_UNAVAILABLE
    label = state.attributes.get("friendly_name", engine) if state else engine
    return {"kind": kind, "engine": engine, "label": label, "state": "ok" if ok else "missing"}


def pipelines_overview(hass: HomeAssistant, entry_id: str) -> list[dict[str, Any]]:
    """Every HA voice pipeline as a chain of stages with states."""
    if "assist_pipeline" not in hass.config.components:
        return []
    agent = aac_agent_entity_id(hass, entry_id)
    preferred = assist_pipeline.async_get_pipeline(hass).id
    return [
        {
            "id": p.id,
            "name": p.name,
            "language": p.language,
            "preferred": p.id == preferred,
            "routed": bool(agent) and p.conversation_engine == agent,
            "stages": [
                _stage(hass, "wake word", p.wake_word_entity),
                _stage(hass, "speech to text", p.stt_engine),
                _stage(hass, "agent", p.conversation_engine),
                _stage(hass, "text to speech", p.tts_engine),
            ],
        }
        for p in assist_pipeline.async_get_pipelines(hass)
    ]


async def async_route_pipeline(
    hass: HomeAssistant, store: AACStore, entry_id: str, pipeline_id: str, enable: bool
) -> None:
    """Point a pipeline's agent at AAC, or back to what it used before."""
    agent = aac_agent_entity_id(hass, entry_id)
    if agent is None:
        raise ValueError("AAC agent entity not found")
    pipeline = assist_pipeline.async_get_pipeline(hass, pipeline_id)
    previous: dict[str, list] = store.data["setup"].setdefault("pipeline_prev", {})
    if enable:
        if pipeline.conversation_engine != agent:
            previous[pipeline_id] = [
                pipeline.conversation_engine,
                pipeline.conversation_language,
            ]
        await assist_pipeline.async_update_pipeline(
            hass, pipeline, conversation_engine=agent, conversation_language="*"
        )
    else:
        engine, language = previous.pop(pipeline_id, [HA_AGENT, pipeline.language])
        await assist_pipeline.async_update_pipeline(
            hass, pipeline, conversation_engine=engine, conversation_language=language
        )
    await store.async_save()
