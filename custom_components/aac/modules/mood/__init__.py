"""Mood: how a voice sounds (owner, 2026-10-19: "the talker id and some simple moods"). Every spoken
line gets the mood a speech emotion model hears (the voice pool's "mood" role, e.g. the AAC senses
service): neutral, happy, angry, sad. From the sound of the voice, not the words, so a laugh at a
video and a calm request differ even when the text doesn't. Clear moods go to the model ("sure").
"""

from __future__ import annotations

from typing import Any

from ...capabilities import Stage
from .. import ModuleContext

MAX_SECONDS = 15  # the end of a long line is enough


async def async_setup_module(ctx: ModuleContext) -> None:
    """Register the stage."""

    def setting(key: str) -> Any:
        return ctx.setting(ctx.entry.runtime_data.voice_domain.id, key)

    async def run(source, pcm: bytes, line: dict[str, Any]) -> dict[str, Any] | None:
        if line.get("kind", "speech") != "speech" or line["seconds"] < float(setting("min_seconds") or 1):
            return None
        found = await ctx.audio.async_senses("mood", "/audio/mood", pcm[-MAX_SECONDS * 32000:])
        if not found or not (moods := found.get("moods")):
            return None
        label, score = moods[0]
        sure = label != "neutral" and score * 100 >= float(setting("threshold") or 60)
        return {"mood": {"label": label, "score": round(float(score), 2), "sure": sure}}

    ctx.add_stage(Stage(id="mood", title="Mood", kind="segment", run=run, order=25))
