"""Audio cards (docs/v2): Microphone → (Noise gate) → Pause cut → Speech to text → … → Voice out (speaks at the
satellite). Raw audio flows on `audio` wires as a stream (never a context); a Pause cut makes clips (`clip` wires);
Speech to text turns a clip into a context. When AAC last spoke is the core's (templates: {{ audio.… }})."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import LOGGER, SAMPLE_RATE, Card, Context, level, make_vad, register, Segmenter

VERSION = "1.1.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

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
    title = "Pause cut"
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
    ports_help = {"in": "audio clips (e.g. speech from a Pause cut)",
                  "out": "round: every wire gets each heard line as a new context (data: mic = the satellite, mic_name, "
                         "language), e.g. into several Conditionals side by side"}
    tags = {"heard": "what was said (data: the satellite)"}
    settings_schema = {"language": {"type": "text", "title": "Language (empty: detect)", "default": ""},
                       "models": {"type": "modellist", "tag": "speech to text", "title": "Models (in order)", "default": [],
                                  "help": "The list is run through from the top: a free model takes it; a busy one queues it if its Queue is ticked, then the next is tried. Empty: every model with the tag, queued. HA's engines on the Audio page come after."}}

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        entries = self.core.pool.entries(self.settings.get("models"), "speech to text")
        heard = await self.core.audio.stt(clip["pcm"], self.settings.get("language") or None, entries)
        text = ((heard or {}).get("text") or "").strip()
        if not text:
            return
        context = Context(origin=self.id)
        context.add(self.id, "said", text, ["heard"], mic=clip.get("source"), mic_name=clip.get("name"),
                    area=clip.get("area"), seconds=clip.get("seconds"), language=(heard or {}).get("language"))
        await self.domain.flow.send(context, self.id, "out")


@register
class VoiceOut(Card):
    """Speaks."""

    type = "voice_out"
    title = "Voice out"
    category = "Audio"
    help = ("Speaks the newest answer: at the satellite the spoken line came from, or on any Home Assistant media "
            "player (the voice and volume are on the Audio page). An answer 'nothing' stays silent; a stop cuts it off.")
    inputs = {"in": "text"}
    outputs: dict[str, str] = {}
    ports_help = {"in": "a context: its newest answer is spoken (a stop arriving here cuts off what it is saying)"}
    settings_schema = {"source": {"type": "player", "title": "Speaks at", "default": "",
                                  "help": "Empty: the satellite the spoken line came from. Or any media player."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._spoke_at: set[str] = set()  # satellites and media players it spoke on (a stop cuts them)

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        text = context.answer.strip()
        said = next((e for e in reversed(context.entries) if e["kind"] == "said" and e["data"].get("mic")), None)
        where = self.settings.get("source") or (said["data"].get("mic") if said else None)
        language = (said or {}).get("data", {}).get("language")
        if not where or not text or text.lower() in SILENT:
            return []
        self._spoke_at.add(where)
        if where.startswith("media_player."):
            await self._speak_on(where, text, language)
        else:
            await self.core.audio.say(where, text, language)
        return []

    async def _speak_on(self, player: str, text: str, language: str | None) -> None:
        """Speak on a Home Assistant media player with AAC's voice engine (HA's tts.speak)."""
        audio = self.core.audio.settings
        voice = (audio.get("voices") or {}).get((language or self.hass.config.language or "en")[:2])
        data = {"entity_id": audio.get("tts"), "media_player_entity_id": player, "message": text}
        if voice:
            data |= {"language": voice["language"], "options": {"voice": voice["voice"]}}
        try:
            await self.hass.services.async_call("tts", "speak", data, blocking=True)
        except Exception as err:  # noqa: BLE001 - a speaker that fails must not break the chain
            LOGGER.warning("AAC: speaking on %s failed: %s", player, err)

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        busy = False
        for where in list(self._spoke_at):
            if where.startswith("media_player."):
                if (state := self.hass.states.get(where)) and state.state == "playing":
                    await self.hass.services.async_call("media_player", "media_stop", {"entity_id": where}, blocking=True)
                    busy = True
            elif await self.core.audio.stop(where):
                busy = True
        self._spoke_at.clear()
        return busy
