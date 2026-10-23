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

from ...streams import Node
from ...const import LOGGER
from ...provider import OpenAICompatProvider, ProviderError
from .. import ModuleContext

ATTENTION_LINES = 6  # earlier lines the attention check sees
ATTENTION_FORMAT = {"type": "json_schema", "json_schema": {"name": "attention", "schema": {
    "type": "object", "properties": {"to_assistant": {"type": "boolean"}}, "required": ["to_assistant"]}}}


def describe(line: dict[str, Any]) -> str:
    """What the other stages found about a line (voice, sounds), in words for the attention check."""
    parts = []
    if sp := line.get("speaker"):
        who = sp["name"] + (" (new voice)" if sp.get("new") else "")
        if sp.get("kind") == "media":
            who += f", a voice from a video/TV{': ' + sp['note'] if sp.get('note') else ''}"
        elif sp.get("kind") == "person":
            who += f", a person{': ' + sp['note'] if sp.get('note') else ''}"
        parts.append(f"voice: {who}")
    if sounds := line.get("sounds"):
        parts.append("sounds: " + ", ".join(f"{n} {round(p * 100)} %" for n, p in sounds[:4]))
    return "; ".join(parts)


async def async_attention(ctx: ModuleContext, text: str, earlier: list[str], just_said: str | None = None,
                          about: str = "") -> bool:
    """Is someone talking to AAC, or is the name just part of other speech (TV, a video, a talk)?"""
    pool = ctx.entry.runtime_data.pool
    if (picked := pool.endpoint_for("fast")) is None:
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
              f"these lines before:\n{heard}\n{said}Now it heard: \"{text}\"{f' ({about})' if about else ''}\n"
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
    pool.mark_up(model_id)
    return bool(answer.get("to_assistant", True))


async def async_setup_module(ctx: ModuleContext) -> None:
    """Listen for AAC's name."""

    async def heard(source, line: dict[str, Any], earlier: list[dict[str, Any]]) -> None:
        if line.get("kind", "speech") != "speech" or not line.get("text"):
            return  # sounds and untranscribed speech do not carry a name
        if (line.get("speaker") or {}).get("kind") == "media":
            return  # the owner said this voice is from a video or the TV
        if line.get("part", 1) < line.get("parts", 1):
            return  # one utterance split into sentences: answer once, on its last part, with all of it
        together = [x for x in earlier if line.get("utterance") and x.get("utterance") == line["utterance"]]
        text = " ".join([*(x["text"] for x in together), line["text"]])
        hub = ctx.audio
        domain_id = ctx.entry.runtime_data.voice_domain.id
        follow_up = float(ctx.setting(domain_id, "follow_up") or 0)
        spoke = hub.spoke.get(source.id)
        # measured to when the sentence STARTED (a long sentence takes a while to be recognized)
        after = (datetime.fromisoformat(line["time"]) - spoke["at"]).total_seconds() if spoke else None
        is_follow_up = after is not None and -5 <= after < follow_up  # the mic is muted while AAC speaks; -5 s = pre-roll and timing slack
        if not (hub.mentions(text) or is_follow_up):
            return
        for x in [*together, line]:
            x["addressed"] = True
        if ctx.setting(domain_id, "attention") and not await async_attention(
            ctx, text, [x["text"] for x in earlier if x.get("kind", "speech") == "speech"][-ATTENTION_LINES:],
            spoke["text"] if is_follow_up else None, describe(line),
        ):
            for x in [*together, line]:
                x["addressed"], x["gate"] = False, "not meant for AAC"
            hub.changed()
            return
        hub.changed()
        seconds = float(ctx.setting(domain_id, "look_back") or 0)
        since = datetime.fromisoformat(line["time"]) - timedelta(seconds=seconds)
        voice = (line.get("speaker") or {}).get("id")
        before = [x["text"] for x in earlier if seconds and x.get("text") and x not in together
                  and datetime.fromisoformat(x["time"]) >= since and x.get("kind") == "speech"
                  # with voices known: only the same person (not a video or others in the room)
                  and (not voice or (x.get("speaker") or {}).get("id") == voice)]
        await hub.async_answer(source, text, before, line.get("speaker"), via="wakeword")

    async def on_line(domain_id: str, port: str, line: dict[str, Any]) -> None:
        if source := ctx.audio.source_of(line):
            await heard(source, line, ctx.audio.earlier(line))

    # A line with its name leaves at its output, through the line into the model; the answer goes to Voice
    ctx.add_node(Node(id="wakeword", title="Wake word", inputs={"lines": "text.line"}, outputs={"out": "text"},
                      role="source", run=on_line,
                      description="Listens to the heard lines and answers when one of AAC's names is said (or right after "
                                  "it spoke); an attention check filters out a video saying the name."))
