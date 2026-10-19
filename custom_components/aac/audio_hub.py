"""AAC's audio hub: sources stream in, speech becomes transcript lines, AAC answers by voice.

Framework section 4a. A source (the AAC satellite program on a PC, or as an HA
app next to a USB/Bluetooth headset) streams raw 16 kHz mono PCM over a
WebSocket. Per source: a RAM ring buffer (never written to disk), voice
activity detection that cuts speech segments, speech to text with an HA STT
engine (used as a tool, HA's own pipelines stay untouched), and a rolling
transcript. When a line addresses AAC (one of its names, or a follow-up right
after it spoke) the master answers and the answer is spoken back through an
HA TTS engine to the same source.
"""

from __future__ import annotations

from array import array
import io
import wave
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components import stt, tts
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import LOGGER
from .models import resolve_model
from .pools import is_entity
from .provider import OpenAICompatProvider, ProviderConnectionError, ProviderError
from .runs import async_notify

if TYPE_CHECKING:
    from . import AACConfigEntry

SAMPLE_RATE = 16000
FRAME_BYTES = 320  # 10 ms of 16-bit mono
RING_SECONDS = 30
PREROLL_FRAMES = 50  # 500 ms before speech starts (micro VAD reacts ~250 ms late)
START_FRAMES = 3  # speech this long opens a segment
END_SILENCE_FRAMES = 80  # 800 ms of silence closes it
MAX_SEGMENT_FRAMES = 2000  # 20 s
MIN_SPEECH_FRAMES = 30  # shorter blips are dropped
TRANSCRIPT_LINES = 200
# Integrations whose speech engines run locally (preferred when nothing is chosen)
LOCAL_ENGINES = {"wyoming", "piper", "whisper", "faster_whisper"}

VOICE_PROMPT = (
    "This message came in by voice from the microphone '{name}'{area}. Your answer is spoken "
    "aloud: one or two short sentences, plain words, no lists or markdown."
)

# Whisper invents these on noise and silence
_HALLUCINATIONS = re.compile(
    r"^\W*(thank you( for watching)?|thanks for watching|bye|you|untertitel.*|vielen dank\W*|"
    r"subtitles by.*|\[.*\]|\(.*\))\W*$",
    re.IGNORECASE,
)


def pcm_to_wav(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit PCM as a WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


class EnergyVad:
    """Fallback detector (loudness only) when the speech model is not available."""

    def __init__(self, threshold: float = 500) -> None:
        """Initialize."""
        self.threshold = threshold

    def __call__(self, frame: bytes) -> float:
        """Speech probability of one 10 ms frame."""
        samples = array("h", frame)
        rms = (sum(s * s for s in samples) / len(samples)) ** 0.5
        return 1.0 if rms >= self.threshold else 0.0


def make_vad() -> Callable[[bytes], float]:
    """The speech detector HA itself uses (micro VAD), else loudness."""
    try:
        from pymicro_vad import MicroVad  # noqa: PLC0415 - optional
    except ImportError:
        LOGGER.warning("pymicro-vad not available, using a loudness detector")
        return EnergyVad()
    vad = MicroVad()
    return lambda frame: vad.Process10ms(frame)


class Segmenter:
    """Cuts a PCM stream into speech segments."""

    def __init__(self, vad: Callable[[bytes], float]) -> None:
        """Initialize."""
        self._vad = vad
        self._pending = b""
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._segment: list[bytes] | None = None
        self._run = 0  # consecutive speech frames before a segment
        self._silence = 0
        self._speech = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add audio; returns finished segments."""
        done: list[bytes] = []
        data = self._pending + chunk
        usable = len(data) - len(data) % FRAME_BYTES
        self._pending = data[usable:]
        for i in range(0, usable, FRAME_BYTES):
            frame = data[i : i + FRAME_BYTES]
            speech = self._vad(frame) >= 0.5
            if self._segment is None:
                self._preroll.append(frame)
                self._run = self._run + 1 if speech else 0
                if self._run >= START_FRAMES:
                    self._segment = list(self._preroll)
                    self._speech, self._silence = self._run, 0
                    self._preroll.clear()
                continue
            self._segment.append(frame)
            if speech:
                self._speech += 1
                self._silence = 0
            else:
                self._silence += 1
            if self._silence >= END_SILENCE_FRAMES or len(self._segment) >= MAX_SEGMENT_FRAMES:
                if self._speech >= MIN_SPEECH_FRAMES:
                    done.append(b"".join(self._segment))
                self._segment, self._run = None, 0
        return done

    def reset(self) -> None:
        """Forget a half-heard segment (e.g. while AAC itself is speaking)."""
        self._pending, self._segment, self._run = b"", None, 0
        self._preroll.clear()


@dataclass
class Source:
    """A connected audio source."""

    id: str
    name: str
    area: str | None
    send: Callable[[Any], Awaitable[None]]
    segmenter: Segmenter
    connected: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    ring: deque[bytes] = field(default_factory=lambda: deque(maxlen=RING_SECONDS * 100))
    playing: bool = False
    last_answer: float = 0.0
    heard: int = 0  # segments

    def summary(self) -> dict[str, Any]:
        """For the panel."""
        return {"id": self.id, "name": self.name, "area": self.area, "connected": self.connected,
                "playing": self.playing, "heard": self.heard}


class AudioHub:
    """All live sources of one AAC entry."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.sources: dict[str, Source] = {}
        self.transcript: deque[dict[str, Any]] = deque(maxlen=TRANSCRIPT_LINES)

    # ------------------------------------------------------------ sources
    @callback
    def connect(self, source_id: str, name: str, area: str | None, send: Callable[[Any], Awaitable[None]]) -> Source:
        """A source came online (a reconnect replaces the old one)."""
        source = Source(source_id, name, area, send, Segmenter(make_vad()))
        self.sources[source_id] = source
        self.entry.async_create_background_task(
            self.hass, self.entry.runtime_data.store.async_add_known_source(source_id, name), "aac audio known"
        )
        async_notify(self.hass, "audio")
        return source

    @callback
    def disconnect(self, source: Source) -> None:
        """A source went away."""
        if self.sources.get(source.id) is source:
            del self.sources[source.id]
            async_notify(self.hass, "audio")

    @callback
    def feed(self, source: Source, chunk: bytes) -> None:
        """Raw audio from a source."""
        if source.playing:
            return  # AAC is speaking there; don't listen to itself
        source.ring.append(chunk)
        for segment in source.segmenter.feed(chunk):
            source.heard += 1
            self.entry.async_create_background_task(
                self.hass, self._async_segment(source, segment), f"aac audio {source.id}"
            )

    @callback
    def played(self, source: Source) -> None:
        """The source finished playing AAC's answer: the follow-up window starts now."""
        source.playing = False
        source.last_answer = time.monotonic()
        source.segmenter.reset()
        async_notify(self.hass, "audio")

    # ------------------------------------------------------------ hearing
    def addressed(self, source: Source, text: str) -> bool:
        """One of AAC's names in the sentence, or a follow-up right after it spoke."""
        cfg = self.entry.runtime_data.store.audio
        if source.last_answer and time.monotonic() - source.last_answer < float(cfg["follow_up"]):
            return True
        said = f" {_norm(text)} "
        return any(f" {_norm(name)} " in said for name in cfg["names"] if _norm(name))

    async def _async_segment(self, source: Source, pcm: bytes) -> None:
        heard = await self.async_stt(pcm)
        text = (heard or {}).get("text", "").strip()
        if not text or _HALLUCINATIONS.match(text):
            return
        line = {"time": dt_util.utcnow().isoformat(), "source": source.id, "name": source.name,
                "text": text, "addressed": self.addressed(source, text),
                "language": heard.get("language"), "confidence": heard.get("confidence"),
                "engine": heard.get("engine")}
        self.transcript.append(line)
        async_notify(self.hass, "audio")
        if line["addressed"]:
            await self.async_answer(source, text)

    def engine(self, kind: str) -> str | None:
        """What would serve STT/TTS now: the first usable resource of the pool role, else local first."""
        return self.entry.runtime_data.broker.pick(kind) or self.ha_engine(kind)

    def ha_engine(self, kind: str) -> str | None:
        """An HA engine, local first, else HA's default (None if HA has none)."""
        # Local first: HA's default may be a cloud service (e.g. Google Translate)
        ent_reg = er.async_get(self.hass)
        for entity_id in sorted(self.hass.states.async_entity_ids(kind)):
            if (reg := ent_reg.async_get(entity_id)) and reg.platform in LOCAL_ENGINES:
                return entity_id
        try:
            return (stt if kind == "stt" else tts).async_default_engine(self.hass)
        except KeyError:  # the stt/tts component is not loaded
            return None

    def _language(self, supported: list[str]) -> str:
        wanted = self.entry.runtime_data.store.audio["language"] or self.hass.config.language
        for lang in supported:
            if lang == wanted or lang.split("-")[0] == wanted.split("-")[0]:
                return lang
        return supported[0] if supported else wanted

    async def async_stt(self, pcm: bytes) -> dict[str, Any] | None:
        """Speech to text through the voice pool: models (any OpenAI-compatible server) or HA engines,
        in the owner's order; a machine that does not answer is skipped, HA's local engine is last.

        Returns {"text", "language", "confidence", "words", "engine"} (HA engines give only the text)."""
        broker = self.entry.runtime_data.broker
        tried: set[str] = set()
        while (resource := broker.pick("stt")) is not None and resource not in tried:
            tried.add(resource)
            if is_entity(resource):
                return await self._async_stt_entity(resource, pcm)
            heard = await self._async_stt_model(resource, pcm)
            if heard is not None or broker.usable(resource):
                return heard  # answered (or failed on this segment only)
        if (fallback := self.ha_engine("stt")) is None:
            LOGGER.warning("AAC audio: no speech-to-text engine")
            return None
        return await self._async_stt_entity(fallback, pcm)

    async def _async_stt_entity(self, entity_id: str, pcm: bytes) -> dict[str, Any] | None:
        if (entity := stt.async_get_speech_to_text_entity(self.hass, entity_id)) is None:
            return None
        metadata = stt.SpeechMetadata(
            language=self._language(list(entity.supported_languages)),
            format=stt.AudioFormats.WAV,
            codec=stt.AudioCodecs.PCM,
            bit_rate=stt.AudioBitRates.BITRATE_16,
            sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
            channel=stt.AudioChannels.CHANNEL_MONO,
        )

        async def chunks() -> AsyncIterator[bytes]:
            for i in range(0, len(pcm), 3200):
                yield pcm[i : i + 3200]

        try:
            result = await entity.internal_async_process_audio_stream(metadata, chunks())
        except Exception as err:  # noqa: BLE001 - one bad segment must not stop the hub
            LOGGER.warning("AAC audio: speech-to-text failed: %s", err)
            return None
        if result.result != stt.SpeechResultState.SUCCESS:
            return None
        return {"text": result.text or "", "language": metadata.language, "confidence": None, "words": [],
                "engine": entity_id}

    async def _async_stt_model(self, model_id: str, pcm: bytes) -> dict[str, Any] | None:
        broker = self.entry.runtime_data.broker
        if (endpoint := resolve_model(self.hass, self.entry.runtime_data.store, model_id)) is None:
            return None
        provider = OpenAICompatProvider(
            async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key, timeout=60, connect_timeout=3
        )
        try:
            # No language = the model detects it per sentence (German and English alike)
            heard = await provider.transcribe(
                endpoint.model, pcm_to_wav(pcm), self.entry.runtime_data.store.audio["language"] or None
            )
        except ProviderConnectionError as err:
            # The machine may simply be off (e.g. a desktop PC): the broker skips it for a minute
            LOGGER.info("AAC audio: speech model %s unreachable (%s)", model_id, err)
            broker.mark_down(model_id, str(err))
            return None
        except ProviderError as err:
            LOGGER.warning("AAC audio: speech-to-text failed: %s", err)
            return None
        broker.mark_up(model_id)
        return heard | {"engine": model_id}

    # ------------------------------------------------------------ speaking
    async def async_tts(self, text: str) -> bytes | None:
        """Text to speech (WAV) with the chosen HA engine."""
        engine = self.engine("tts")
        if not engine:
            LOGGER.warning("AAC audio: no text-to-speech engine")
            return None
        try:
            stream = tts.async_create_stream(
                self.hass, engine, None,
                {tts.ATTR_PREFERRED_FORMAT: "wav", tts.ATTR_PREFERRED_SAMPLE_RATE: 22050,
                 tts.ATTR_PREFERRED_SAMPLE_CHANNELS: 1, tts.ATTR_PREFERRED_SAMPLE_BYTES: 2},
            )
            stream.async_set_message(text)
            return b"".join([chunk async for chunk in stream.async_stream_result()])
        except Exception as err:  # noqa: BLE001
            LOGGER.warning("AAC audio: text-to-speech failed: %s", err)
            return None

    async def async_say(self, source: Source, text: str) -> bool:
        """Speak text on a source."""
        if not (audio := await self.async_tts(text)):
            return False
        source.playing = True
        async_notify(self.hass, "audio")
        await source.send({"type": "play", "format": "wav", "bytes": len(audio), "text": text})
        await source.send(audio)
        return True

    async def async_answer(self, source: Source, text: str) -> None:
        """The voice domain's master answers a spoken line; the answer goes back by voice."""
        from .master import async_run_master  # noqa: PLC0415 - avoid an import cycle

        data = self.entry.runtime_data.voice_domain  # one domain owns the voice
        chat_id = f"voice_{source.id}"
        chat = data.chats.get(chat_id) or data.chats.create(f"Voice · {source.name}", chat_id)
        data.chats.add_message(chat_id, "user", text)
        run, result = await async_run_master(
            self.hass, self.entry, text,
            kind="chat", trigger=f"voice: {source.name}", title=chat["title"], chat_id=chat_id,
            conversation_id=chat["conversation_id"],
            extra_system_prompt=VOICE_PROMPT.format(
                name=source.name, area=f" in the {source.area}" if source.area else ""
            ),
            # Anyone can talk into a microphone: voice is anonymous until there is voice ID
            user_id=None,
            domain=data,
            history=data.chats.history(chat_id),
        )
        if result is not None:
            data.chats.set_conversation(chat_id, result.conversation_id)
        data.chats.add_message(chat_id, "aac", run.result or "(no answer)",
                               error=run.status == "failed", run_id=run.id, steps=run.steps)
        if run.result and self.sources.get(source.id) is source:
            await self.async_say(source, run.result)

    # ------------------------------------------------------------ panel
    def overview(self) -> dict[str, Any]:
        """Live state for the panel and the setup tools."""
        return {
            "connected": [s.summary() for s in self.sources.values()],
            "transcript": list(self.transcript)[-30:],
            "stt": self.engine("stt"),
            "tts": self.engine("tts"),
        }
