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
    r"subtitles by.*)\W*$",
    re.IGNORECASE,
)
# Whisper writes some sounds as *crunch*, (laughs), [music]: those become sound lines (subtitles)
SOUND = re.compile(r"^\s*[\*\[\(]+\s*([^\*\]\)]+?)\s*[\*\]\)]+\W*$")


async def async_setup_module(ctx: ModuleContext) -> None:
    """Register the stage."""

    def domain_id() -> str:
        return ctx.entry.runtime_data.voice_domain.id

    last: dict[str, str] = {}  # per source: the last line said, as context for the next

    async def run(source, pcm: bytes, line: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        prompt = last.get(source.id) if ctx.setting(domain_id(), "context") else None
        heard = await ctx.audio.async_stt(pcm, prompt) or {}
        text = (heard.get("text") or "").strip()
        if not any(c.isalpha() for c in text) or (ctx.setting(domain_id(), "filter") and HALLUCINATIONS.match(text)):
            return {"drop": True}
        if sound := SOUND.match(text):
            return {"kind": "sound", "text": f"[{sound.group(1).lower()}]", "engine": heard.get("engine"),
                    "stt_ms": int((time.monotonic() - started) * 1000)}
        last[source.id] = text
        found = {"text": text, "language": heard.get("language"), "confidence": heard.get("confidence"),
                 "engine": heard.get("engine"), "words": heard.get("words") or [],
                 "stt_ms": int((time.monotonic() - started) * 1000)}
        if ctx.setting(domain_id(), "split") and (parts := sentences(heard.get("segments") or [], line["seconds"])):
            found["split"] = parts  # one line per sentence: each gets its own voice and sounds
        return found

    async def partial(source, pcm: bytes) -> str | None:
        resource = ctx.entry.runtime_data.broker.pick("stt")
        if not ctx.setting(domain_id(), "live") or resource is None or is_entity(resource):
            return None  # HA's CPU engines are too slow to transcribe every second
        return ((await ctx.audio.async_stt(pcm)) or {}).get("text", "").strip() or None

    ctx.add_stage(Stage(id="stt", title="Speech to text", kind="segment", run=run, partial=partial, order=10))


MIN_PART = 1.2  # seconds: shorter sentences join their neighbour (too short for a voice print)


def sentences(segments: list[dict[str, Any]], seconds: float) -> list[dict[str, Any]] | None:
    """The model's sentences as parts {start, end, text}, short ones joined; None when there is nothing to split."""
    parts: list[dict[str, Any]] = []
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        if parts and (seg["end"] - seg["start"] < MIN_PART or parts[-1]["end"] - parts[-1]["start"] < MIN_PART):
            parts[-1] = {"start": parts[-1]["start"], "end": seg["end"], "text": f"{parts[-1]['text']} {text}"}
        else:
            parts.append({"start": seg["start"], "end": seg["end"], "text": text})
    if len(parts) < 2:
        return None
    parts[0]["start"], parts[-1]["end"] = 0, seconds  # keep the edges (the speech detector's pre-roll and tail)
    for a, b in zip(parts, parts[1:], strict=False):  # no gaps: cut in the middle of a pause
        a["end"] = b["start"] = (a["end"] + b["start"]) / 2
    return parts
