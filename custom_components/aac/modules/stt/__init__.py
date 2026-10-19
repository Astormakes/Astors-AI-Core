"""Speech to text as an audio stage: every speech segment gets its text (owner, 2026-10-19:
speech to text is a module like the rest). The engines stay the voice pool's (core); this
module decides that and how segments are transcribed.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ...capabilities import Stage
from ...pools import is_entity
from .. import ModuleContext

# Whisper invents these on noise and silence
HALLUCINATIONS = re.compile(
    r"^\W*(thank you( for watching)?|thanks for watching|bye|you|untertitel.*|vielen dank\W*|"
    r"subtitles by.*|\[.*\]|\(.*\))\W*$",
    re.IGNORECASE,
)


async def async_setup_module(ctx: ModuleContext) -> None:
    """Register the stage."""

    def domain_id() -> str:
        return ctx.entry.runtime_data.voice_domain.id

    async def run(source, pcm: bytes, line: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        heard = await ctx.audio.async_stt(pcm) or {}
        text = (heard.get("text") or "").strip()
        if not text or (ctx.setting(domain_id(), "filter") and HALLUCINATIONS.match(text)):
            return {"drop": True}
        return {"text": text, "language": heard.get("language"), "confidence": heard.get("confidence"),
                "engine": heard.get("engine"), "words": heard.get("words") or [],
                "stt_ms": int((time.monotonic() - started) * 1000)}

    async def partial(source, pcm: bytes) -> str | None:
        resource = ctx.entry.runtime_data.broker.pick("stt")
        if not ctx.setting(domain_id(), "live") or resource is None or is_entity(resource):
            return None  # HA's CPU engines are too slow to transcribe every second
        return ((await ctx.audio.async_stt(pcm)) or {}).get("text", "").strip() or None

    ctx.add_stage(Stage(id="stt", title="Speech to text", kind="segment", run=run, partial=partial, order=10))
