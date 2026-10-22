"""Sounds: the room as subtitles (owner, 2026-10-19: "transcribe noises into text, like subtitles for a game").

A sound tagger (AudioSet classes, the voice pool's "sounds" role, e.g. the AAC senses service) names
what it hears: between spoken lines in pieces of a few seconds → lines like "[laughter, music]"; on
spoken lines the sounds behind them (music, a TV …: a hint that the speech comes from a video).
"""

from __future__ import annotations

import time
from typing import Any

from ...streams import Node
from .. import ModuleContext

# Speech and room tone are not news on their own: the speech to text stage covers speech
SPEECHY = {"Speech", "Male speech, man speaking", "Female speech, woman speaking", "Child speech, kid speaking",
           "Conversation", "Narration, monologue", "Speech synthesizer", "Silence", "Inside, small room",
           "Inside, large room or hall", "Inside, public space", "Outside, urban or manmade", "Babbling",
           "Hubbub, speech noise, speech babble"}
MAX_SECONDS = 10  # the tagger looks at 10 s at most
REPEAT_SECONDS = 10  # the same sounds again within this time are not written twice


async def async_setup_module(ctx: ModuleContext) -> None:
    """Register both stages."""
    last: dict[str, tuple[float, frozenset[str]]] = {}

    def setting(key: str) -> Any:
        return ctx.setting(ctx.entry.runtime_data.voice_domain.id, key)

    async def tags(pcm: bytes) -> list[list] | None:
        found = await ctx.audio.async_senses("sounds", "/audio/tags", pcm[-MAX_SECONDS * 32000:])
        return (found or {}).get("labels")

    async def on_speech(source, pcm: bytes, line: dict[str, Any]) -> dict[str, Any] | None:
        if not setting("speech_tags") or not (labels := await tags(pcm)):
            return None
        return {"sounds": [[n, p] for n, p in labels if p >= 0.1][:5]}

    async def between(source, pcm: bytes) -> list[dict[str, Any]]:
        if source.segmenter.active or not (labels := await tags(pcm)):
            return []  # spoken lines get their tags above
        threshold = float(setting("threshold") or 30) / 100
        names = [n for n, p in labels if p >= threshold and n not in SPEECHY]
        if not names:
            return []
        key, now = frozenset(names), time.monotonic()
        if (prev := last.get(source.id)) and prev[1] == key and now - prev[0] < REPEAT_SECONDS:
            return []
        last[source.id] = (now, key)
        return [{"text": f"[{', '.join(n.lower() for n in names[:3])}]", "labels": [[n, p] for n, p in labels[:5]]}]

    window = min(10.0, max(1.0, float(ctx.setting(ctx.entry.runtime_data.voice_domain.id, "window") or 2)))
    ctx.add_node(Node(id="sounds_speech", title="Sounds behind speech", description="Notes the sounds behind a spoken line "
                      "(music, laughter, a door), so the line is read in its situation.", inputs={"speech": "audio.segment"}, outputs={"speech": "audio.segment"},
                      adds=["sounds"], order=15, annotate=on_speech))
    ctx.add_node(Node(id="sounds", title="Sounds", description="Names the sounds in the room like subtitles ([doorbell], "
                      "[music]) between spoken lines.", inputs={"audio": "audio.pcm"}, outputs={"events": "sound.event"},
                      listen=between, window=window))
