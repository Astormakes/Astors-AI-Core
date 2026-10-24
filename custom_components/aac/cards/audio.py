"""Audio cards (docs/v2): Microphone → (Noise gate) → Audio clipper → Speech to text → … → Text to speech → Speaker (at the
satellite). Raw audio flows on `audio` wires as a stream (never a context); an Audio clipper makes clips (`clip` wires);
Speech to text turns a clip into a context. When AAC last spoke is the core's (templates: {{ audio.… }})."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import (LOGGER, SAMPLE_RATE, Card, Context, Segmenter, level, make_vad, pcm_to_wav, register,
                                      resample, wav_to_pcm)

VERSION = "2.3.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

SILENT = ("nothing", "nothing.", "")
SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written
STREAM_PIECE = 2  # seconds of a live stream the Speaker plays at a time


@register
class Microphone(Card):
    """A satellite's audio."""

    type = "microphone"
    title = "Microphone"
    category = "Audio"
    help = "The audio of one satellite (or any): the start of an audio chain. Wire it into a Noise gate or an Audio clipper."
    inputs: dict[str, str] = {}
    outputs = {"out": "audio"}
    ports_help = {"out": "the satellite's live audio (a stream; the wire swells with the loudness)"}
    settings_schema = {"source": {"type": "source", "title": "Listens to", "default": "any",
                                  "help": "One satellite (each its own chain), or any of them."}}

    async def start(self) -> None:
        self.core.subscribe("audio", self, self.hear)

    def hear(self, chunk: bytes, meta: dict[str, Any]) -> None:
        """Audio from the core's "audio" channel (only the satellite it listens to)."""
        if self.settings.get("source") in ("any", meta["source"], None, ""):
            self.domain.flow.stream(self.id, "out", chunk, meta)


@register
class NoiseGate(Card):
    """Only sound above a level."""

    type = "noise_gate"
    title = "Noise gate"
    category = "Audio"
    help = "Lets audio through only above a loudness level (fan hum, a TV far away stay out); below it, silence."
    inputs = {"in": "audio"}
    outputs = {"out": "audio"}
    ports_help = {"in": "live audio", "out": "the same audio, silence below the level"}
    settings_schema = {"level": {"type": "number", "title": "Opens at (0–100)", "default": 25,
                                 "help": "The Audio page shows each satellite's live level."},
                       "hold": {"type": "number", "title": "Stays open (ms)", "default": 300}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._open: dict[str, datetime] = {}

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        now, key = dt_util.utcnow(), meta.get("source", "")
        if level(chunk) >= float(self.settings.get("level") or 25):
            self._open[key] = now + timedelta(milliseconds=int(self.settings.get("hold") or 300))
        passes = now <= self._open.get(key, now - timedelta(seconds=1))
        self.domain.flow.stream(self.id, "out", chunk if passes else bytes(len(chunk)), meta)


@register
class PauseCut(Card):
    """Speech detection."""

    type = "pause_cut"
    title = "Audio clipper"
    category = "Audio"
    help = "Speech detection: a stretch of speech that ends with a pause leaves as one clip. Wire it into Speech to text."
    inputs = {"in": "audio"}
    outputs = {"out": "clip"}
    ports_help = {"in": "live audio", "out": "one clip per stretch of speech that ends with a pause"}
    settings_schema = {"pause": {"type": "number", "title": "Pause that ends a clip (ms)", "default": 800,
                                 "help": "Shorter: quicker, but a pause mid-sentence cuts it. Longer: whole sentences."},
                       "longest": {"type": "number", "title": "Longest clip (s)", "default": 30}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._cuts: dict[str, Segmenter] = {}

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        key = meta.get("source", "")
        seg = self._cuts.get(key)
        if seg is None:
            seg = self._cuts[key] = Segmenter(make_vad(), int(self.settings.get("pause") or 800), int(self.settings.get("longest") or 30))
        for pcm in seg.feed(chunk):
            self.core.audio.mark(meta.get("source"), "speech ended")
            seconds = round(len(pcm) / (SAMPLE_RATE * 2), 1)
            self.domain.flow.clip(self.id, "out", {**meta, "pcm": pcm, "seconds": seconds, "end": dt_util.utcnow().isoformat()})


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
                         "language), e.g. into several Conditionals side by side"}
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
        self.core.audio.mark(clip.get("source"), "text ready")
        context = Context(origin=self.id)
        context.add(self.id, "said", text, ["heard"], mic=clip.get("source"), mic_name=clip.get("name"),
                    area=clip.get("area"), seconds=clip.get("seconds"), language=(heard or {}).get("language"))
        await self.domain.flow.send(context, self.id, "out")


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
        said = max((i for i, e in enumerate(context.entries) if e["kind"] == "said"), default=-1)
        heard = (context.entries[said]["data"] if said >= 0 else {})
        for e in context.entries[said + 1:]:
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


@register
class Speaker(Card):
    """Plays sound."""

    type = "speaker"
    title = "Speaker"
    category = "Audio"
    help = ("Plays audio clips (a spoken answer, a sound) or a live audio stream: at the satellite a clip comes from, or "
            "at a satellite or Home Assistant media player you pick. A stop clip, or Stop, cuts it off. The volume is on "
            "the Audio page.")
    inputs = {"in": "audio|clip"}
    outputs: dict[str, str] = {}
    ports_help = {"in": "clips or a live stream (half green, half purple: it takes both)"}
    settings_schema = {"source": {"type": "player", "title": "Plays at", "default": "",
                                  "help": "Empty: the satellite the clip came from. Or a satellite or any media player."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._played_at: set[str] = set()  # where it played (a stop cuts them)
        self._buffers: dict[str, bytearray] = {}  # a live stream, gathered into short clips per satellite

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if clip.get("stop"):
            await self.stop()
            return
        where = self.settings.get("source") or clip.get("source")
        if not where or not clip.get("pcm"):
            return
        await self._play(where, pcm_to_wav(clip["pcm"], int(clip.get("rate") or SAMPLE_RATE)), clip.get("text", ""), queue=True)

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """A live stream: played in short pieces (satellites take whole clips)."""
        where = self.settings.get("source") or meta.get("source")
        if not where:
            return
        buf = self._buffers.setdefault(where, bytearray())
        buf.extend(chunk)
        if len(buf) >= SAMPLE_RATE * 2 * STREAM_PIECE:
            wav, buf[:] = pcm_to_wav(bytes(buf)), b""
            self.hass.async_create_background_task(self._play(where, wav, ""), f"aac {self.id} stream")

    async def _play(self, where: str, wav: bytes, text: str, queue: bool = False) -> None:
        self._played_at.add(where)
        if not where.startswith("media_player."):
            await self.core.audio.play(where, wav, text, queue=queue)  # clips one after another, a stream at once
            return
        try:
            await self.hass.services.async_call("media_player", "play_media", {
                "entity_id": where, "media_content_id": self.core.audio.clip_url(wav), "media_content_type": "music",
                "announce": True}, blocking=True)
        except Exception as err:  # noqa: BLE001 - a speaker that fails must not break the chain
            LOGGER.warning("AAC: playing on %s failed: %s", where, err)

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        busy = False
        for where in list(self._played_at):
            if where.startswith("media_player."):
                if (state := self.hass.states.get(where)) and state.state == "playing":
                    await self.hass.services.async_call("media_player", "media_stop", {"entity_id": where}, blocking=True)
                    busy = True
            elif await self.core.audio.stop(where):
                busy = True
        self._played_at.clear()
        self._buffers.clear()
        return busy