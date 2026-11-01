"""The Text to speech card (docs/v2 page 5): speaks the entries its Speaks setting names that came after the last entry
it does not speak, each once, sentence by sentence while they are written, in a voice per language. A clip carries
the satellite of the newest entry that has one (data mic: a heard line); wire it into a Speaker. Stop: what is still
being made is dropped and a stop clip goes to the Speaker."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from custom_components.aac.api import Card, Context, matches, register, wav_to_pcm

VERSION = "3.0.0"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written


@register
class TextToSpeech(Card):
    """An answer becomes a clip."""

    type = "text_to_speech"
    title = "Text to speech"
    category = "Audio"
    help = ("Speaks the entries its Speaks setting names that came after the last entry it does not speak, each once, "
            "sentence by sentence while they are written, in a voice per language (the language it was spoken to in). "
            "A clip carries the satellite of the newest heard line; wire it into a Speaker.")
    inputs = {"in": "text"}
    outputs = {"out": "clip"}
    ports_help = {"in": "a context: the entries Speaks names after the last one it does not speak (while they are "
                        "written: each finished sentence)",
                  "out": "each spoken sentence as a clip (the satellite it was heard at, the text, the language); Stop "
                         "sends a stop clip"}
    settings_schema = {"speaks": {"type": "text", "title": "Speaks", "default": "model.said, model.error",
                                  "help": "The entries it speaks (tags, comma separated; * stands for anything)."},
                       "engine": {"type": "tts_engine", "title": "Voice engine", "default": "",
                                  "help": "A Home Assistant text-to-speech engine. Empty: the default on the Audio page."},
                       "voices": {"type": "voices", "title": "Voice per language", "default": {},
                                  "help": "For each home language (Audio page). Not set: the engine's default voice."}}
    functions = {
        "voices": {"help": "The voices it can use for each home language, and the one set."},
        "set_voice": {"help": "Speak a language in another voice.",
                      "args": {"language": {"type": "string", "help": "A language code, e.g. de.", "required": True},
                               "voice": {"type": "string", "help": "A voice's name or id (from voices); empty: the engine's default.",
                                         "required": True}}},
    }

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._spoken: dict[str, int] = {}  # entry → how much of its text is spoken
        self._last: asyncio.Future | None = None  # the newest sentence on its way (they go out in order)
        self._round = 0  # a stop starts a new round: sentences of the old one are dropped

    @property
    def busy(self) -> bool:
        return self._last is not None and not self._last.done()

    async def handle(self, context: Context, port: str) -> None:
        self._speak(context)
        if self._last and not context.streaming:
            await self._last

    def _speaks(self, e: dict[str, Any]) -> bool:
        return any(matches(e, p.strip()) for p in str(self.settings.get("speaks") or "").split(",") if p.strip())

    def _speak(self, context: Context) -> None:
        """Queue what is newly finished in the entries it speaks after the last one it does not (an entry still being
        written: up to its last finished sentence)."""
        entries = context.conversation
        last = max((i for i, e in enumerate(entries) if not self._speaks(e)), default=-1)
        heard = next((e["data"] for e in reversed(entries) if e["data"].get("mic")), {})
        for e in entries[last + 1:]:
            if e["state"] == "cut":
                continue
            text, upto = e["text"], self._spoken.get(e["id"], 0)
            if e["state"] == "streaming":
                ends = [m.end() for m in SENTENCE.finditer(text)]
                end = ends[-1] if ends else 0
            else:
                end = len(text)
            if end <= upto:
                continue
            self._spoken[e["id"]] = end
            if part := text[upto:end].strip():
                self.core.audio.mark(heard.get("mic"), "first sentence")
                self._queue(part, heard)
        while len(self._spoken) > 50:  # only recent turns matter
            self._spoken.pop(next(iter(self._spoken)))

    def _queue(self, text: str, heard: dict[str, Any]) -> None:
        before, round_ = self._last, self._round

        async def in_order() -> None:
            wav = await self.core.audio.tts(text, heard.get("language"), self.settings.get("engine") or None, self.settings.get("voices"))
            if before:
                await before
            if not wav or round_ != self._round:
                return
            self.core.audio.mark(heard.get("mic"), "voice ready")
            pcm, rate = wav_to_pcm(wav)
            self.clip_out("out", {"pcm": pcm, "rate": rate, "seconds": round(len(pcm) / 2 / rate, 1), "text": text,
                                                   "source": heard.get("mic"), "name": heard.get("mic_name"), "language": heard.get("language")})
        self._last = asyncio.ensure_future(in_order())

    async def stop(self) -> None:
        self._round += 1  # sentences still being made are dropped
        self.clip_out("out", {"stop": True})  # the Speaker stops what it plays

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "voices":
            mine = self.settings.get("voices") or {}
            return {code: {"voices": [v["name"] for v in got["voices"]], "set": next((v["name"] for v in got["voices"]
                    if v["id"] == mine.get(code, {}).get("voice")), "the engine's default")}
                    for code, got in self.core.audio.voices(self.settings.get("engine") or None).items()}
        if function == "set_voice":
            code, wanted = str(args.get("language") or "").lower()[:3], str(args.get("voice") or "").strip()
            got = self.core.audio.voices(self.settings.get("engine") or None).get(code)
            if got is None:
                return {"error": f"{code} is not a home language"}
            voices = dict(self.settings.get("voices") or {})
            if not wanted:
                voices.pop(code, None)
            else:
                voice = next((v for v in got["voices"] if wanted.lower() in (v["id"].lower(), v["name"].lower())), None)
                if voice is None:
                    return {"error": f"no voice {wanted} for {code}", "voices": [v["name"] for v in got["voices"]]}
                voices[code] = {"language": voice["locale"], "voice": voice["id"]}
            await self.domain.update_card(self.id, settings={"voices": voices})  # the card is made again with it
            return {"done": "set_voice", "language": code, "voice": wanted or "the engine's default"}
        return await super().call(function, args, context)

