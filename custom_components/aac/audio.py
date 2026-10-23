"""AAC's own audio pipeline (separate from HA's Assist pipelines): overview for the panel.

Design: docs (framework section 4a). One lightweight pipeline per audio
source, the heavy models are shared. Built: AAC satellites stream in
(`audio_view.py`), the hub (`audio_hub.py`) buffers, detects speech, turns it
into text with an HA STT engine and answers by voice through an HA TTS engine.
Planned: arbitration between mics, speaker ID, HA devices as sources.
Nothing here touches HA's own voice setup.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)

from .audio_hub import tts_voices
from .languages import NAMES

STAGES = [
    ("source", "Sources", "AAC satellites stream their mic (a PC, or an HA app next to a USB/Bluetooth headset).", "built"),
    ("buffer", "Ring buffer", "Last 30 s of raw audio, RAM only, never written to disk.", "built"),
    ("cards", "Audio cards", "Microphone → Noise gate → Pause cut → Speech to text, wired in Nodes (Add node → Audio): each "
     "spoken piece becomes a message that runs through your cards.", "built"),
    ("gather", "Gather", "The same sentence from several satellites: one picked (a model or a rule).", "planned"),
    ("tts", "Voice", "The answer is spoken on the same source by an HA text-to-speech engine (own voices later).", "built"),
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
    return sources


def audio_overview(hass: HomeAssistant, hub=None, settings: dict | None = None) -> dict[str, Any]:
    """Stages, live sources, settings and engines for the panel."""
    live = hub.overview() if hub else {"connected": [], "stt": None, "tts": None}
    return {
        "built": True,
        "stages": [{"id": sid, "name": name, "desc": desc, "state": state} for sid, name, desc, state in STAGES],
        "connected": live["connected"],
        "settings": settings or {},
        "languages": hub.languages() if hub else [],  # the home's (global, SETTINGS → Languages)
        "engines": {
            "stt": live["stt"],
            "tts": live["tts"],
            "stt_options": sorted(hass.states.async_entity_ids("stt")),
            "tts_options": sorted(hass.states.async_entity_ids("tts")),
            "tts_voices": (voices := tts_voices(hass, live["tts"])),
            "language_names": {code: NAMES.get(code, code) for code in voices},
        },
        # HA devices with a mic; they become sources later (planned)
        "candidates": _sources(hass),
    }
