"""Wake word: AAC answers when its name is said, the classic way (owner, 2026-10-19).

The core only hears (audio_hub → hearing listeners); this module decides: one of AAC's
names in the line (core audio setting) or a follow-up right after it spoke → optionally a
quick attention check (a video saying the name is ignored) → the master answers by voice,
with the lines said just before as context (look back).
"""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...capabilities import Hearing
from ...const import LOGGER
from ...provider import OpenAICompatProvider, ProviderError
from .. import ModuleContext

ATTENTION_LINES = 6  # earlier lines the attention check sees
ATTENTION_FORMAT = {"type": "json_schema", "json_schema": {"name": "attention", "schema": {
    "type": "object", "properties": {"to_assistant": {"type": "boolean"}}, "required": ["to_assistant"]}}}


async def async_attention(ctx: ModuleContext, text: str, earlier: list[str], just_said: str | None = None) -> bool:
    """Is someone talking to AAC, or is the name just part of other speech (TV, a video, a talk)?"""
    broker = ctx.entry.runtime_data.broker
    if (picked := broker.endpoint_for("fast")) is None:
        return True  # nothing to judge with: the name decides
    model_id, endpoint = picked
    provider = OpenAICompatProvider(
        async_get_clientsession(ctx.hass), endpoint.base_url, endpoint.api_key, timeout=20, connect_timeout=3
    )
    names = ", ".join(ctx.entry.runtime_data.store.audio["names"])
    heard = "\n".join(f"- {t}" for t in earlier) or "- (nothing)"
    said = (f"The assistant itself just finished saying: \"{just_said}\". The new line came right after it, "
            "so it is likely a reply or a follow-up to the assistant.\n") if just_said else ""
    prompt = (f"You are the attention of a home assistant called {names}. A microphone in the room heard "
              f"these lines before:\n{heard}\n{said}Now it heard: \"{text}\"\n"
              "Is a person speaking TO the assistant now (a request, a question, a greeting to it)? Or is the "
              "name only part of other speech: a video, TV, music, people talking about something else? "
              'Answer only with JSON: {"to_assistant": true or false}')
    try:
        reply = await provider.chat(endpoint.model, [{"role": "user", "content": prompt}], None,
                                    temperature=0, max_tokens=20, response_format=ATTENTION_FORMAT,
                                    chat_template_kwargs={"enable_thinking": False})
        answer = json.loads(reply.get("content") or "{}")
    except (ProviderError, ValueError) as err:
        LOGGER.info("AAC wake word: attention check failed (%s), the name decides", err)
        return True
    broker.mark_up(model_id)
    return bool(answer.get("to_assistant", True))


async def async_setup_module(ctx: ModuleContext) -> None:
    """Listen for AAC's name."""

    async def heard(source, line: dict[str, Any], earlier: list[dict[str, Any]]) -> None:
        if line.get("kind", "speech") != "speech" or not line.get("text"):
            return  # sounds and untranscribed speech do not carry a name
        hub = ctx.audio
        domain_id = ctx.entry.runtime_data.voice_domain.id
        follow_up = float(ctx.setting(domain_id, "follow_up") or 0)
        spoke = hub.spoke.get(source.id)
        # measured to when the sentence STARTED (a long sentence takes a while to be recognized)
        after = (datetime.fromisoformat(line["time"]) - spoke["at"]).total_seconds() if spoke else None
        is_follow_up = after is not None and -5 <= after < follow_up  # the mic is muted while AAC speaks; -5 s = pre-roll and timing slack
        if not (hub.mentions(line["text"]) or is_follow_up):
            return
        line["addressed"] = True
        if ctx.setting(domain_id, "attention") and not await async_attention(
            ctx, line["text"], [x["text"] for x in earlier if x.get("kind", "speech") == "speech"][-ATTENTION_LINES:],
            spoke["text"] if is_follow_up else None,
        ):
            line["addressed"], line["gate"] = False, "not meant for AAC"
            hub.changed()
            return
        hub.changed()
        seconds = float(ctx.setting(domain_id, "look_back") or 0)
        since = datetime.fromisoformat(line["time"]) - timedelta(seconds=seconds)
        before = [x["text"] for x in earlier if seconds and x.get("text") and datetime.fromisoformat(x["time"]) >= since]
        await hub.async_answer(source, line["text"], before)

    ctx.add_hearing(Hearing(id="wakeword", title="Wake word", heard=heard))
