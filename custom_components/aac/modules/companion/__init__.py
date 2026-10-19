"""Companion: AAC follows the conversation instead of waiting for its name (owner, 2026-10-19:
"a classifier instead of rules, it vibes it out; it needs to follow bigger parts of a conversation,
see text in and out, and decide for each new input").

For every new spoken line a quick model (fast tier, a JSON decision) reads the last minutes of the
buffer: who said what (voices), AAC's own spoken lines, sounds. It decides for the newest line whether
AAC should answer. One decision at a time per source; lines that arrive meanwhile are judged together.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from ...audio_hub import voice_label
from ...capabilities import Hearing
from ...const import LOGGER
from ...provider import OpenAICompatProvider, ProviderError
from .. import ModuleContext

DECISION = {"type": "json_schema", "json_schema": {"name": "companion", "schema": {
    "type": "object", "properties": {"respond": {"type": "boolean"}, "why": {"type": "string"}},
    "required": ["respond", "why"]}}}

RULES = {
    "spoken_to": ("Answer yes only when the NEWEST line is meant for the assistant: it is called by name, the line "
                  "replies to or follows up on what the assistant just said, or it is a request or question that is "
                  "clearly for the assistant (no other person is being addressed). Answer no when people talk to each "
                  "other, think aloud, or when the line comes from a video, the TV or music."),
    "join": ("Answer yes when the NEWEST line is meant for the assistant (by name, a reply to it, a request clearly "
             "for it), OR when the assistant can clearly help the people right now with something short and useful "
             "and this is a natural moment (a pause, an open question to the room). Never interrupt a private or "
             "heated moment, never comment on videos or the TV, and stay quiet when unsure: speaking too often is "
             "worse than missing a chance."),
}


SAME_VOICE_SECONDS = 60  # what the same person said this shortly before goes along with the request


def same_voice(lines: list[dict[str, Any]], line: dict[str, Any], seconds: float = SAME_VOICE_SECONDS) -> list[str]:
    """What the person talking to AAC said just before (their voice only: not a video or other people;
    the master got the whole room once and mixed a video's talk into its answer)."""
    voice = (line.get("speaker") or {}).get("id")
    if not voice:
        return []
    since = datetime.fromisoformat(line["time"]) - timedelta(seconds=seconds)
    return [x["text"] for x in lines if x.get("kind") == "speech" and (x.get("speaker") or {}).get("id") == voice
            and datetime.fromisoformat(x["time"]) >= since][-4:]


def room_line(line: dict[str, Any], start: datetime) -> str:
    """One buffer line for the decision: time offset, who, text."""
    t = (datetime.fromisoformat(line["time"]) - start).total_seconds()
    if line.get("kind") == "aac":
        who = "ASSISTANT (you)"
    elif line.get("kind") == "sound":
        return f"[{t:5.0f}s] (sound) {line['text']}"
    else:
        who = voice_label(line.get("speaker")) or "someone"
    return f"[{t:5.0f}s] {who}: {line['text']}"


async def async_setup_module(ctx: ModuleContext) -> None:
    """Listen to the room."""
    busy: dict[str, bool] = {}
    waiting: dict[str, tuple] = {}

    def setting(key: str) -> Any:
        return ctx.setting(ctx.entry.runtime_data.voice_domain.id, key)

    def wakeword_on() -> bool:
        return any(h.module == "wakeword" for h in ctx.entry.runtime_data.capabilities.listeners(
            ctx.entry.runtime_data.voice_domain.id))

    async def decide(source, line: dict[str, Any]) -> dict[str, Any] | None:
        broker = ctx.entry.runtime_data.broker
        if (picked := broker.endpoint_for("fast")) is None:
            return None
        model_id, endpoint = picked
        minutes = min(30.0, max(1.0, float(setting("minutes") or 5)))
        since = dt_util.utcnow() - timedelta(minutes=minutes)
        recent = [x for x in ctx.audio.transcript if x["source"] == source.id and x.get("text")
                  and datetime.fromisoformat(x["time"]) >= since][-int(setting("max_lines") or 40):]
        if line not in recent:
            recent.append(line)
        start = datetime.fromisoformat(recent[0]["time"])
        names = ", ".join(ctx.entry.runtime_data.store.audio["names"])
        system = (f"You are the ears of a home assistant called {names}. It hears the room through a microphone "
                  f"('{source.name}'). You decide whether the assistant should answer the NEWEST line now. "
                  f"{RULES.get(setting('mode'), RULES['spoken_to'])} Voices marked (media) are from a video or TV. "
                  'Reply only with JSON: {"respond": true or false, "why": "a few words"}')
        room = "\n".join(room_line(x, start) for x in recent[:-1]) or "(nothing before)"
        user = f"The room, oldest first:\n{room}\n\nNEWEST line:\n{room_line(line, start)}"
        provider = OpenAICompatProvider(async_get_clientsession(ctx.hass), endpoint.base_url, endpoint.api_key,
                                        timeout=30, connect_timeout=3)
        try:
            reply = await provider.chat(endpoint.model, [{"role": "system", "content": system},
                                                         {"role": "user", "content": user}], None,
                                        temperature=0, max_tokens=60, response_format=DECISION,
                                        chat_template_kwargs={"enable_thinking": False})
            answer = json.loads(reply.get("content") or "{}")
        except (ProviderError, ValueError) as err:
            LOGGER.info("AAC companion: decision failed: %s", err)
            return None
        broker.mark_up(model_id)
        return {"respond": bool(answer.get("respond")), "why": str(answer.get("why", ""))[:120],
                "before": same_voice(recent[:-1], line)}

    async def heard(source, line: dict[str, Any], earlier: list[dict[str, Any]]) -> None:
        if line.get("kind", "speech") != "speech" or not line.get("text") or wakeword_on():
            return  # only people's speech; with the Wake word module on, it decides (no double answers)
        if (line.get("speaker") or {}).get("kind") == "media":
            return
        if busy.get(source.id):
            waiting[source.id] = (source, line)  # judged after the current decision, with everything said meanwhile
            return
        busy[source.id] = True
        try:
            while True:
                found = await decide(source, line)
                if found is not None:
                    line["judged"] = found["why"]
                    if found["respond"]:
                        line["addressed"] = True
                    ctx.audio.changed()
                    if found["respond"]:
                        await ctx.audio.async_answer(source, line["text"], found["before"], line.get("speaker"))
                if (nxt := waiting.pop(source.id, None)) is None:
                    break
                source, line = nxt
        finally:
            busy[source.id] = False

    ctx.add_hearing(Hearing(id="companion", title="Companion", heard=heard))
