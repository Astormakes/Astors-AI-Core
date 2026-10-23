"""AAC's audio hub: satellites stream in, the audio cards hear, AAC speaks back.

Framework section 4a. A source (the AAC satellite program on a PC, or as an HA app next to a USB/Bluetooth
headset) streams raw 16 kHz mono PCM over a WebSocket. The hub hands each source's audio to the Microphone cards
(audio_nodes.py: Microphone → Noise gate → Pause cut → Speech to text, wired by the owner), offers speech to text
and text to speech through the voice pool, and speaks answers at a source. It does not listen while AAC speaks
there. What used to be hidden in here (speech detection for the hearing modules, the transcript, the wake word
and companion answers) are cards now (owner, 2026-10-23: "remove everything that isn't used in the new system").
"""

from __future__ import annotations

from array import array
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
import io
import math
import sys
import time
from typing import TYPE_CHECKING, Any
import wave

from homeassistant.components import stt, tts
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import LOGGER
from .languages import code_of, detect, home_languages
from .models import resolve_model
from .pools import is_entity
from .provider import OpenAICompatProvider, ProviderConnectionError, ProviderError
from .runs import async_notify

if TYPE_CHECKING:
    from . import AACConfigEntry

SAMPLE_RATE = 16000
FRAME_BYTES = 320  # 10 ms of 16-bit mono
PREROLL_FRAMES = 50  # 500 ms before speech starts (micro VAD reacts ~250 ms late)
START_FRAMES = 3  # speech this long opens a segment
END_SILENCE_FRAMES = 80  # 800 ms of silence closes it
MAX_SEGMENT_FRAMES = 2000  # 20 s
MIN_SPEECH_FRAMES = 30  # shorter blips are dropped
METER_FRAMES = 10  # one meter point = 100 ms
METER_POINTS = 100  # the live view shows the last 10 s
# Integrations whose speech engines run locally (preferred when nothing is chosen)
LOCAL_ENGINES = {"wyoming", "piper", "whisper", "faster_whisper"}



def pcm_to_wav(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit PCM as a WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


def tts_voices(hass: HomeAssistant, engine: str | None) -> dict[str, list[dict[str, str]]]:
    """The engine's voices per language (de → every de_* locale), for choosing one in the panel."""
    found: dict[str, list[dict[str, str]]] = {}
    if not engine or (instance := tts.get_engine_instance(hass, engine)) is None:
        return found
    for code in instance.supported_languages or []:
        if lang := code_of(code):
            for v in instance.async_get_supported_voices(code) or []:
                found.setdefault(lang, []).append({"language": code, "voice": v.voice_id, "name": v.name})
    return found


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
        self.end_silence = END_SILENCE_FRAMES  # the pause that ends a segment (owner setting pause_ms)
        # The live view: per 100 ms (loudness 0–100, share of speech frames, inside a segment)
        self.meter: deque[tuple[int, float, bool]] = deque(maxlen=METER_POINTS)
        self._acc = [0, 0, 0]  # peak loudness, speech frames, frames

    @property
    def active(self) -> bool:
        """Inside a speech segment right now."""
        return self._segment is not None

    @property
    def frames(self) -> int:
        """Frames in the open segment (0 when none is open)."""
        return len(self._segment) if self._segment is not None else 0

    def _measure(self, frame: bytes, speech: bool) -> None:
        samples = array("h", frame)
        peak = max(abs(min(samples)), max(samples)) if samples else 0
        acc = self._acc
        acc[0], acc[1], acc[2] = max(acc[0], peak), acc[1] + speech, acc[2] + 1
        if acc[2] >= METER_FRAMES:
            # loudness on a log scale: 0 = digital silence, 100 = full scale (about 60 dB of range)
            level = 0 if acc[0] <= 0 else max(0, min(100, int(100 + 100 / 60 * 20 * math.log10(acc[0] / 32767))))
            # the real peak (dBFS) without the meter's floor: digital silence (None) vs. only very quiet
            self.peak_db = round(20 * math.log10(acc[0] / 32767), 1) if acc[0] > 0 else None
            self.meter.append((level, round(acc[1] / acc[2], 2), self._segment is not None))
            self._acc = [0, 0, 0]

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add audio; returns finished segments."""
        done: list[bytes] = []
        data = self._pending + chunk
        usable = len(data) - len(data) % FRAME_BYTES
        self._pending = data[usable:]
        for i in range(0, usable, FRAME_BYTES):
            frame = data[i : i + FRAME_BYTES]
            speech = self._vad(frame) >= 0.5
            self._measure(frame, speech)
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
            if self._silence >= self.end_silence or len(self._segment) >= MAX_SEGMENT_FRAMES:
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
    playing: bool = False
    last_answer: float = 0.0
    saying: str = ""  # what AAC is speaking right now

    def summary(self) -> dict[str, Any]:
        """For the panel."""
        return {"id": self.id, "name": self.name, "area": self.area, "connected": self.connected,
                "playing": self.playing}


class AudioHub:
    """All live sources of one AAC entry."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.sources: dict[str, Source] = {}
        self._mics: tuple[float, list] = (float("-inf"), [])  # the Microphone cards (audio_nodes.py), cached briefly

    @property
    def _bus(self):
        return self.entry.runtime_data.bus

    @property
    def _domain_id(self) -> str:
        return self.entry.runtime_data.voice_domain.id

    # ------------------------------------------------------------ sources
    @callback
    def connect(self, source_id: str, name: str, area: str | None, send: Callable[[Any], Awaitable[None]]) -> Source:
        """A source came online (a reconnect replaces the old one)."""
        source = Source(source_id, name, area, send, Segmenter(make_vad()))  # its segmenter: the live meter only
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
        # the audio cards (audio_nodes.py): every Microphone card that takes this source starts its chain
        domain_id, now = self._domain_id, time.monotonic()
        if now - self._mics[0] > 1:  # looked up at most once a second (audio comes about 50 times a second)
            self._mics = (now, [n for n in self._bus.nodes_in(domain_id).values() if n.type == "microphone"])
        for node in self._mics[1]:
            if node.listens_to in ("any", source.id):
                self._bus.stream(domain_id, node.id, "out", chunk, {"source": source.id, "name": source.name, "area": source.area})
        source.segmenter.feed(chunk)  # the live meter (the Pause cut cards cut on their own)

    @callback
    def played(self, source: Source) -> None:
        """The source finished playing AAC's answer."""
        source.playing = False
        source.last_answer = time.monotonic()
        source.segmenter.reset()
        async_notify(self.hass, "audio")

    # ------------------------------------------------------------ engines
    def saw_language(self, value: str | None) -> None:
        """Count a language heard or spoken (the panel proposes adding heard ones to the home's list)."""
        if not (code := code_of(value)):
            return
        seen = self.entry.runtime_data.store.audio.setdefault("languages_seen", {})
        seen[code] = seen.get(code, 0) + 1
        if seen[code] == 1:
            self.entry.async_create_background_task(
                self.hass, self.entry.runtime_data.store.async_save(), "aac language seen")

    def engine(self, kind: str) -> str | None:
        """What would serve STT/TTS now: the first usable resource of the pool role, else local first."""
        return self.entry.runtime_data.pool.pick(kind) or self.ha_engine(kind)

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

    def languages(self) -> list[str]:
        """The home's languages, first = primary (global, languages.py)."""
        return home_languages(self.hass, self.entry.runtime_data.store)

    def _language(self, supported: list[str], wanted: str | None = None) -> str:
        wanted = wanted or self.languages()[0]  # HA engines need one language: the primary
        for lang in supported:
            if lang == wanted or lang.split("-")[0] == wanted.split("-")[0]:
                return lang
        return supported[0] if supported else wanted

    async def async_stt(self, pcm: bytes, prompt: str | None = None, language: str | None = None) -> dict[str, Any] | None:
        """Speech to text through the voice pool: models (any OpenAI-compatible server) or HA engines,
        in the owner's order; a machine that does not answer is skipped, HA's local engine is last.
        language = transcribe in this one (default: the home's only language, or detected among all).

        Returns {"text", "language", "confidence", "words", "engine"} (HA engines give only the text)."""
        pool = self.entry.runtime_data.pool
        tried: set[str] = set()
        while (resource := pool.pick("stt")) is not None and resource not in tried:
            tried.add(resource)
            if is_entity(resource):
                return await self._async_stt_entity(resource, pcm, language)
            heard = await self._async_stt_model(resource, pcm, prompt, language)
            if heard is not None or pool.usable(resource):
                return heard  # answered (or failed on this segment only)
        if (fallback := self.ha_engine("stt")) is None:
            LOGGER.warning("AAC audio: no speech-to-text engine")
            return None
        return await self._async_stt_entity(fallback, pcm, language)

    async def _async_stt_entity(self, entity_id: str, pcm: bytes, language: str | None = None) -> dict[str, Any] | None:
        if (entity := stt.async_get_speech_to_text_entity(self.hass, entity_id)) is None:
            return None
        metadata = stt.SpeechMetadata(
            language=self._language(list(entity.supported_languages), language),
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

    async def _async_stt_model(self, model_id: str, pcm: bytes, prompt: str | None = None,
                               language: str | None = None) -> dict[str, Any] | None:
        pool = self.entry.runtime_data.pool
        if (endpoint := resolve_model(self.hass, self.entry.runtime_data.store, model_id)) is None:
            return None
        provider = OpenAICompatProvider(
            async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key, timeout=60, connect_timeout=3
        )
        try:
            # One home language: say so; several: the model detects it per sentence (the modules check it)
            home = self.languages()
            heard = await provider.transcribe(
                endpoint.model, pcm_to_wav(pcm), language or (home[0] if len(home) == 1 else None), prompt
            )
        except ProviderConnectionError as err:
            # The machine may simply be off (e.g. a desktop PC): the pool skips it for a minute
            LOGGER.info("AAC audio: speech model %s unreachable (%s)", model_id, err)
            pool.mark_down(model_id, str(err))
            return None
        except ProviderError as err:
            LOGGER.warning("AAC audio: speech-to-text failed: %s", err)
            return None
        pool.mark_up(model_id)
        return heard | {"engine": model_id}

    # ------------------------------------------------------------ speaking
    async def async_tts(self, text: str) -> bytes | None:
        """Text to speech (WAV) with the chosen HA engine, in the owner's voice for the text's language
        (owner, 2026-10-20: German answers were read by an English voice; any language, not only de/en)."""
        engine = self.engine("tts")
        if not engine:
            LOGGER.warning("AAC audio: no text-to-speech engine")
            return None
        voices = self.entry.runtime_data.store.audio.get("voices") or {}
        lang = detect(text, self.languages())
        self.saw_language(lang)
        picked = voices.get(lang)
        options = {tts.ATTR_PREFERRED_FORMAT: "wav", tts.ATTR_PREFERRED_SAMPLE_RATE: 22050,
                   tts.ATTR_PREFERRED_SAMPLE_CHANNELS: 1, tts.ATTR_PREFERRED_SAMPLE_BYTES: 2}
        tries = [(picked["language"], {**options, tts.ATTR_VOICE: picked["voice"]})] if picked else []
        for language, opts in [*tries, (None, options)]:  # the engine's default if the voice is not there
            try:
                stream = tts.async_create_stream(self.hass, engine, language, opts)
                stream.async_set_message(text)
                return b"".join([chunk async for chunk in stream.async_stream_result()])
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("AAC audio: text-to-speech failed (%s): %s", opts.get(tts.ATTR_VOICE, "default voice"), err)
        return None

    async def async_say(self, source: Source, text: str) -> bool:
        """Speak text on a source, at the owner's volume (0 = AAC does not speak aloud)."""
        volume = int(self.entry.runtime_data.store.audio.get("volume", 40))
        if volume <= 0 or not (audio := await self.async_tts(text)):
            return False
        if volume < 100:
            audio = await self.hass.async_add_executor_job(scale_wav, audio, volume / 100)
        source.playing, source.saying = True, text
        async_notify(self.hass, "audio")
        await source.send({"type": "play", "format": "wav", "bytes": len(audio), "text": text})
        await source.send(audio)
        return True

    # ------------------------------------------------------------ panel
    def live(self) -> list[dict[str, Any]]:
        """The live view per source: the last seconds of loudness and speech, and what happens now."""
        return [{"id": s.id, "name": s.name, "meter": list(s.segmenter.meter), "partial": "",
                 "speech_seconds": round(s.segmenter.frames / 100, 1), "peak_db": getattr(s.segmenter, "peak_db", None),
                 "state": "speaking" if s.playing else "hearing speech" if s.segmenter.active else "listening"}
                for s in self.sources.values()]

    def overview(self) -> dict[str, Any]:
        """Live state for the panel and the setup tools."""
        return {
            "connected": [s.summary() for s in self.sources.values()],
            "stt": self.engine("stt"),
            "tts": self.engine("tts"),
        }


def scale_wav(audio: bytes, gain: float) -> bytes:
    """A 16-bit WAV, quieter (the speaker's own volume stays as it is)."""
    try:
        with wave.open(io.BytesIO(audio)) as src:
            params, frames = src.getparams(), src.readframes(src.getnframes())
    except (wave.Error, EOFError):
        return audio
    if params.sampwidth != 2:
        return audio
    samples = array("h", frames)
    if sys.byteorder == "big":
        samples.byteswap()
    for i, v in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(v * gain)))
    if sys.byteorder == "big":
        samples.byteswap()
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        # not setparams: a streamed WAV (Piper) has "unknown length" (0xFFFFFFFF) as its frame count
        dst.setnchannels(params.nchannels)
        dst.setsampwidth(params.sampwidth)
        dst.setframerate(params.framerate)
        dst.writeframes(samples.tobytes())
    return out.getvalue()
