"""Audio cards (docs/v2): Microphone → (Noise gate) → Pause cut → Speech to text → … → Voice out (speaks at the
satellite). Raw audio flows on `audio` wires as a stream (never a context); a Pause cut makes pieces (`piece` wires);
Speech to text turns a piece into a context. When AAC last spoke is the core's (templates: {{ audio.… }})."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..audio import SAMPLE_RATE, Segmenter, level, make_vad
from ..message import Message
from . import Card, register

SILENT = ("nothing", "nothing.", "")


@register
class Microphone(Card):
    """A satellite's audio."""

    type = "microphone"
    title = "Microphone"
    category = "Audio"
    help = "The audio of one satellite (or any): the start of an audio chain. Wire it into a Noise gate or a Pause cut."
    inputs: dict[str, str] = {}
    outputs = {"out": "audio"}
    ports_help = {"out": "the satellite's live audio (a stream; the wire swells with the loudness)"}
    settings_schema = {"source": {"type": "source", "title": "Listens to", "default": "any",
                                  "help": "One satellite (each its own chain), or any of them."}}

    def hear(self, chunk: bytes, meta: dict[str, Any]) -> None:
        """Audio from the hub."""
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
    title = "Pause cut"
    category = "Audio"
    help = "Speech detection: a stretch of speech that ends with a pause leaves as one piece. Wire it into Speech to text."
    inputs = {"in": "audio"}
    outputs = {"out": "piece"}
    ports_help = {"in": "live audio", "out": "one piece per stretch of speech that ends with a pause"}
    settings_schema = {"pause": {"type": "number", "title": "Pause that ends a piece (ms)", "default": 800,
                                 "help": "Shorter: quicker, but a pause mid-sentence cuts it. Longer: whole sentences."},
                       "longest": {"type": "number", "title": "Longest piece (s)", "default": 30}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._cuts: dict[str, Segmenter] = {}

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        key = meta.get("source", "")
        seg = self._cuts.get(key)
        if seg is None:
            seg = self._cuts[key] = Segmenter(make_vad(), int(self.settings.get("pause") or 800), int(self.settings.get("longest") or 30))
        for pcm in seg.feed(chunk):
            seconds = round(len(pcm) / (SAMPLE_RATE * 2), 1)
            self.domain.flow.piece(self.id, "out", {**meta, "pcm": pcm, "seconds": seconds, "end": dt_util.utcnow().isoformat()})


@register
class SpeechToText(Card):
    """A piece becomes text."""

    type = "speech_to_text"
    title = "Speech to text"
    category = "Audio"
    help = ("Turns each piece of speech into text: a context of its own (the text, the satellite, the time), tagged "
            "'heard'. The engines and their order are on the Audio page.")
    inputs = {"in": "piece"}
    outputs = {"out": "text"}
    ports_help = {"in": "pieces of speech (from a Pause cut)",
                  "out": "round: every wire gets each heard line as a new context (data: mic = the satellite, mic_name, "
                         "language), e.g. into several Conditionals side by side"}
    tags = {"heard": "what was said (data: the satellite)"}
    settings_schema = {"language": {"type": "text", "title": "Language (empty: detect)", "default": ""}}

    async def piece(self, port: str, piece: dict[str, Any]) -> None:
        heard = await self.core.audio.stt(piece["pcm"], self.settings.get("language") or None)
        text = ((heard or {}).get("text") or "").strip()
        if not text:
            return
        message = Message(origin=self.id)
        message.add(self.id, "said", text, ["heard"], mic=piece.get("source"), mic_name=piece.get("name"),
                    area=piece.get("area"), seconds=piece.get("seconds"), language=(heard or {}).get("language"))
        await self.domain.flow.send(message, self.id, "out")


@register
class VoiceOut(Card):
    """Speaks."""

    type = "voice_out"
    title = "Voice out"
    category = "Audio"
    help = ("Speaks the newest answer at the satellite the spoken line came from (the voice and volume are on the Audio "
            "page). An answer 'nothing' stays silent.")
    inputs = {"in": "text"}
    outputs: dict[str, str] = {}
    ports_help = {"in": "a context: its newest answer is spoken where its newest spoken line came from (or at the set satellite)"}
    settings_schema = {"source": {"type": "source", "title": "Speaks at", "default": "",
                                  "help": "Empty: where the spoken line came from."}}

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        text = message.answer.strip()
        said = next((e for e in reversed(message.entries) if e["kind"] == "said" and e["data"].get("mic")), None)
        mic = self.settings.get("source") or (said["data"].get("mic") if said else None)
        if mic and text and text.lower() not in SILENT:
            await self.core.audio.say(mic, text, (said or {}).get("data", {}).get("language"))
        return []
