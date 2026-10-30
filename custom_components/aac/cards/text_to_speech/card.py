"""The Text to speech card (docs/v2 page 2): Turns the answers of a turn into audio clips, sentence by sentence as they
are written, in a voice per language (the language it was spoken to in). A clip remembers the satellite the spoken
line came from; wire it into a Speaker. An answer 'nothing' stays silent; a stop arriving here goes on to the Speaker
as a stop."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from custom_components.aac.api import Card, Context, register, wav_to_pcm

VERSION = "2.10.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

SILENT = ("nothing", "nothing.", "")

SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written


@register
class TextToSpeech(Card):
    """An answer becomes a clip."""

    type = "text_to_speech"
    title = "Text to speech"
    category = "Audio"
    help = ("Turns the answers of a turn into audio clips, sentence by sentence as they are written, in a voice per "
            "language (the language it was spoken to in). A clip remembers the satellite the spoken line came from; wire "
            "it into a Speaker. An answer 'nothing' stays silent; a stop arriving here goes on to the Speaker as a stop.")
    inputs = {"in": "text"}
    outputs = {"out": "clip"}
    ports_help = {"in": "a context: the answers of its newest turn are spoken (while they are written: each finished sentence)",
                  "out": "each spoken sentence as a clip (data: the satellite it was heard at, the text, the language); a stop "
                         "goes out as a stop clip"}
    settings_schema = {"engine": {"type": "tts_engine", "title": "Voice engine", "default": "",
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
        self._spoken: dict[str, int] = {}  # answer entry → how much of its text is spoken
        self._last: asyncio.Future | None = None  # the newest sentence on its way (they go out in order)
        self._round = 0  # a stop starts a new round: sentences of the old one are dropped

    @property
    def busy(self) -> bool:
        return self._last is not None and not self._last.done()

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        self._speak(context, finished=False)
        return []

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        self._speak(context, finished=True)
        if self._last:
            await self._last
        return []

    def _speak(self, context: Context, finished: bool) -> None:
        """Queue what is newly finished in the answers of the newest turn (all of it once the turn is finished)."""
        entries = context.entries
        said = max((i for i, e in enumerate(entries) if e["kind"] == "said"), default=-1)
        heard = (entries[said]["data"] if said >= 0 else {})
        for e in entries[said + 1:]:
            if e["kind"] not in ("answer", "error") or e.get("state") == "cut":  # an error is said too (nothing came)
                continue
            text, upto = e["text"], self._spoken.get(e["id"], 0)
            if finished or e.get("state") == "closed":
                end = len(text)
            else:
                ends = [m.end() for m in SENTENCE.finditer(text)]
                end = ends[-1] if ends else 0
            if end <= upto:
                continue
            self._spoken[e["id"]] = end
            if text.strip().lower() not in SILENT and (part := text[upto:end].strip()):
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
            self.domain.flow.clip(self.id, "out", {"pcm": pcm, "rate": rate, "seconds": round(len(pcm) / 2 / rate, 1), "text": text,
                                                   "source": heard.get("mic"), "name": heard.get("mic_name"), "language": heard.get("language")})
        self._last = asyncio.ensure_future(in_order())

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        self._round += 1  # sentences still being made are dropped
        self.domain.flow.clip(self.id, "out", {"stop": True})  # the Speaker stops what it plays
        return False

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

