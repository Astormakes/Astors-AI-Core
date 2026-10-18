"""AAC's own audio pipeline (separate from HA's Assist pipelines).

Design: docs (framework section 4a). One lightweight pipeline per audio
source, results become events; the heavy models are shared. Not built yet:
this module describes the stages and finds candidate audio sources so the
panel can show them. Nothing here touches HA's own voice setup.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

STAGES = [
    ("source", "Sources", "Mics: satellites, cameras, this browser. One pipeline per source."),
    ("buffer", "Ring buffer", "Last 30–60 s of raw audio, RAM only, never written to disk."),
    ("vad", "Voice activity", "Cheap, always on. Marks speech segments with a quality score."),
    ("arbitration", "Arbitration", "Same sentence on several mics: the best one wins (also tells where you are)."),
    ("stt", "Speech to text", "Only on the winning audio. Pluggable engine (Whisper, …)."),
    ("speaker", "Speaker ID", "Who said it (voice print). For personalization, not security."),
    ("transcript", "Transcript buffer", "Rolling text with timestamps, mic, speaker. Lifetime configurable."),
    ("master", "Master agent", "Decides: answer, act, remember or ignore."),
]

# Domains whose entities can deliver audio
_SOURCE_DOMAINS = {
    "assist_satellite": "voice satellite",
    "camera": "camera (if it has a mic)",
}


def _sources(hass: HomeAssistant) -> list[dict[str, Any]]:
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)
    sources: list[dict[str, Any]] = []
    for domain, kind in _SOURCE_DOMAINS.items():
        for state in hass.states.async_all(domain):
            entry = ent_reg.async_get(state.entity_id)
            area_id = entry.area_id if entry else None
            if entry and not area_id and entry.device_id:
                device = dr.async_get(hass).async_get(entry.device_id)
                area_id = device.area_id if device else None
            area = area_reg.async_get_area(area_id) if area_id else None
            sources.append(
                {
                    "id": state.entity_id,
                    "name": state.attributes.get("friendly_name", state.entity_id),
                    "kind": kind,
                    "integration": entry.platform if entry else None,
                    "area": area.name if area else None,
                    "available": state.state != "unavailable",
                    "connected": False,
                }
            )
    sources.append(
        {
            "id": "browser",
            "name": "This browser",
            "kind": "browser mic",
            "integration": None,
            "area": None,
            "available": True,
            "connected": False,
        }
    )
    return sources


def audio_overview(hass: HomeAssistant) -> dict[str, Any]:
    """Stages + candidate sources for the panel."""
    return {
        "built": False,
        "stages": [
            {"id": sid, "name": name, "desc": desc, "state": "planned"}
            for sid, name, desc in STAGES
        ],
        "sources": _sources(hass),
    }
