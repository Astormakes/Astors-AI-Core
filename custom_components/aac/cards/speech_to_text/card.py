"""The Speech to text card (docs/v2 page 2): Turns each audio clip into text: a context of its own (the text, the
satellite, the time), tagged 'heard'. The engines and their order are on the Audio page."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import SAMPLE_RATE, Card, Context, register, resample

VERSION = "2.10.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class SpeechToText(Card):
    """A clip becomes text."""

    type = "speech_to_text"
    title = "Speech to text"
    category = "Audio"
    help = ("Turns each audio clip into text: a context of its own (the text, the satellite, the time), tagged "
            "'heard'. The engines and their order are on the Audio page.")
    inputs = {"in": "clip"}
    outputs = {"out": "text"}
    ports_help = {"in": "audio clips (e.g. speech from an Audio clipper)",
                  "out": "round: every wire gets each heard line as a new context (data: mic = the satellite, mic_name, "
                         "language, aac_spoke = it began while AAC spoke there: mostly AAC's own voice), e.g. into several "
                         "Relays side by side (Stop word, Wake word)"}
    tags = {"heard": "what was said (data: the satellite)"}
    settings_schema = {"language": {"type": "text", "title": "Language (empty: the home's)", "default": "",
                                    "help": "A code like de. Empty: the home's languages from the Audio page (one: that one; "
                                            "several: detected among them)."},
                       "models": {"type": "modellist", "tag": "speech to text", "title": "Models (in order)", "default": [],
                                  "help": "The list is run through from the top: a free model takes it; a busy one queues it if its Queue is ticked, then the next is tried. Empty: every model with the tag, queued. HA's engines on the Audio page come after."}}

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if not clip.get("pcm"):  # a stop clip
            return
        entries = self.core.pool.entries(self.settings.get("models"), "speech to text")
        pcm = resample(clip["pcm"], int(clip.get("rate") or SAMPLE_RATE))
        heard = await self.core.audio.stt(pcm, self.settings.get("language") or None, entries)
        text = ((heard or {}).get("text") or "").strip()
        if not text:
            return
        if not clip.get("utterance"):  # a phrase's text is not the turn's yet (the Phrase joiner says when it is)
            self.core.audio.mark(clip.get("source"), "text ready")
        context = Context()
        context.add(self.id, "said", text, ["heard"], mic=clip.get("source"), mic_name=clip.get("name"),
                    utterance=clip.get("utterance"), part=clip.get("part"), aac_spoke=bool(clip.get("aac_spoke")),
                    area=clip.get("area"), seconds=clip.get("seconds"), language=(heard or {}).get("language"))
        await self.domain.flow.send(context, self.id, "out")

