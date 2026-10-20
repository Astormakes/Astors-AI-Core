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
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import difflib
import io
import math
import re
import sys
import time
import unicodedata
from typing import TYPE_CHECKING, Any
import wave

from homeassistant.components import stt, tts
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import LOGGER
from .chats import strip_marks
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
METER_FRAMES = 10  # one meter point = 100 ms
METER_POINTS = 100  # the live view shows the last 10 s
PARTIAL_EVERY = 120  # frames (1.2 s) of new speech before the open segment is transcribed again
TRANSCRIPT_LINES = 200
# Integrations whose speech engines run locally (preferred when nothing is chosen)
LOCAL_ENGINES = {"wyoming", "piper", "whisper", "faster_whisper"}

# A voice chat ends after this much quiet; the next spoken request starts a new one
VOICE_SESSION_GAP = timedelta(minutes=20)
# How many earlier lines of the same source a listener gets with each new one
EARLIER_LINES = 20
VOICE_FAILED = "Sorry, that did not work. The details are in the AAC panel."
VOICE_PROMPT = (
    "This message came in by voice from the microphone '{name}'{area}. You DO speak: your answer is "
    "read out aloud through the speaker there, in your own voice. One or two short sentences, plain words, "
    "no lists or markdown. Your speaking volume is set_voice_volume (not Home Assistant's media players). "
    "When you are asked to do something you can do, call the tool NOW, in this turn, and then say what you did; "
    "never only announce it (\"I'll turn it off\") without calling the tool."
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


def _fold(text: str) -> str:
    """Letters only, accents and umlauts folded (Kompüter → komputer)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if c.isalpha())


NAME_SIMILARITY = 0.8  # Komputer / Kompjuter count as computer; Kopie or Komponente do not


def name_in(text: str, name: str) -> bool:
    """A name in a sentence as whole words, or written a little differently (owner, 2026-10-20: the
    recognizer writes "computer" said in a German sentence in all sorts of ways). Short names must match."""
    said, wanted = _norm(text).split(), _norm(name).split()
    if not wanted:
        return False
    n = len(wanted)
    windows = [" ".join(said[i:i + n]) for i in range(len(said) - n + 1)]
    if " ".join(wanted) in windows:
        return True
    target = _fold(name)
    return len(target) >= 5 and any(
        not (heard := _fold(w)).startswith(target)  # computers, computerspiel: another word
        and difflib.SequenceMatcher(None, heard, target).ratio() >= NAME_SIMILARITY for w in windows)


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

    def so_far(self) -> bytes:
        """The open segment's audio so far (for a live, partial transcript)."""
        return b"".join(self._segment) if self._segment is not None else b""

    def _measure(self, frame: bytes, speech: bool) -> None:
        samples = array("h", frame)
        peak = max(abs(min(samples)), max(samples)) if samples else 0
        acc = self._acc
        acc[0], acc[1], acc[2] = max(acc[0], peak), acc[1] + speech, acc[2] + 1
        if acc[2] >= METER_FRAMES:
            # loudness on a log scale: 0 = digital silence, 100 = full scale (about 60 dB of range)
            level = 0 if acc[0] <= 0 else max(0, min(100, int(100 + 100 / 60 * 20 * math.log10(acc[0] / 32767))))
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
    ring: deque[bytes] = field(default_factory=lambda: deque(maxlen=RING_SECONDS * 100))
    playing: bool = False
    last_answer: float = 0.0
    saying: str = ""  # what AAC is speaking right now
    heard: int = 0  # segments
    transcribing: int = 0  # segments at speech to text right now
    partial: str = ""  # what the open segment said so far (live transcript)
    partial_at: int = 0  # segment frames when the last partial was started
    partial_busy: bool = False
    windows: dict[str, bytearray] = field(default_factory=dict)  # audio collected per window stage

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
        # per source id: when AAC last finished speaking there, and what it said
        self.spoke: dict[str, dict[str, Any]] = {}
        self.voice_chats: dict[str, str] = {}  # per source id: the voice chat of the current session

    # ------------------------------------------------------------ sources
    @callback
    def connect(self, source_id: str, name: str, area: str | None, send: Callable[[Any], Awaitable[None]]) -> Source:
        """A source came online (a reconnect replaces the old one)."""
        source = Source(source_id, name, area, send, Segmenter(make_vad()))
        self.apply_settings(source)
        self.sources[source_id] = source
        self.entry.async_create_background_task(
            self.hass, self.entry.runtime_data.store.async_add_known_source(source_id, name), "aac audio known"
        )
        async_notify(self.hass, "audio")
        return source

    def apply_settings(self, source: Source | None = None) -> None:
        """Owner settings that live in the sources (the pause that ends a line)."""
        frames = max(20, min(300, int(self.entry.runtime_data.store.audio.get("pause_ms", 800)) // 10))
        for s in [source] if source else self.sources.values():
            s.segmenter.end_silence = frames

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
        seg = source.segmenter
        stages = self.entry.runtime_data.capabilities
        domain_id = self.entry.runtime_data.voice_domain.id
        if seg.active and not source.partial_busy and seg.frames - source.partial_at >= PARTIAL_EVERY:
            if live := next((st for st in stages.active_stages(domain_id, "segment") if st.partial), None):
                source.partial_busy, source.partial_at = True, seg.frames
                self.entry.async_create_background_task(self.hass, self._async_partial(source, live), f"aac partial {source.id}")
        for stage in stages.active_stages(domain_id, "window"):  # e.g. sound captions, on all audio
            buf = source.windows.setdefault(stage.id, bytearray())
            buf += chunk
            if len(buf) >= int(stage.window * SAMPLE_RATE * 2):
                pcm, source.windows[stage.id] = bytes(buf), bytearray()
                self.entry.async_create_background_task(self.hass, self._async_window(source, stage, pcm), f"aac {stage.id}")
        for segment in seg.feed(chunk):
            source.partial, source.partial_at = "", 0
            source.heard += 1
            self.entry.async_create_background_task(
                self.hass, self._async_segment(source, segment), f"aac audio {source.id}"
            )

    @callback
    def played(self, source: Source) -> None:
        """The source finished playing AAC's answer: the follow-up window starts now."""
        source.playing = False
        source.last_answer = time.monotonic()
        # kept by source id, so a reconnect does not lose it: the follow-up window counts from here
        self.spoke[source.id] = {"at": dt_util.utcnow(), "text": source.saying}
        source.segmenter.reset()
        async_notify(self.hass, "audio")

    # ------------------------------------------------------------ hearing
    def mentions(self, text: str) -> bool:
        """One of AAC's names in a sentence (whole words, case and punctuation ignored)."""
        return any(name_in(text, name) for name in self.entry.runtime_data.store.audio["names"])

    async def _async_partial(self, source: Source, stage) -> None:
        """The open segment so far, by a stage that can (speech to text): only for the live view."""
        try:
            if len(pcm := source.segmenter.so_far()) >= PARTIAL_EVERY * FRAME_BYTES:
                text = await stage.partial(source, pcm)
                if source.segmenter.active and text:
                    source.partial = text
        except Exception:  # noqa: BLE001 - the live view must not break hearing
            LOGGER.exception("AAC audio: partial of %s failed", stage.id)
        finally:
            source.partial_busy = False

    async def _async_segment(self, source: Source, pcm: bytes) -> None:
        """A speech segment: measured here, then each segment stage adds what it finds (text, speaker, mood, …).
        A stage may split it into parts (speech to text: the model's sentences); the later stages then work
        on each part, so every sentence gets its own voice and sounds."""
        ended = dt_util.utcnow()
        seconds = len(pcm) / (SAMPLE_RATE * 2)
        start = ended - timedelta(seconds=seconds)
        stages = self.entry.runtime_data.capabilities.active_stages(self.entry.runtime_data.voice_domain.id, "segment")
        source.transcribing += 1
        try:
            lines = await self._async_stages(source, pcm, self._segment_line(source, pcm, start), stages)
        finally:
            source.transcribing -= 1
        for line in lines:
            self._add_line(source, line)

    def _segment_line(self, source: Source, pcm: bytes, start) -> dict[str, Any]:
        return {"time": start.isoformat(), "source": source.id, "name": source.name, "kind": "speech", "text": "",
                "addressed": False, "seconds": round(len(pcm) / (SAMPLE_RATE * 2), 1),
                "loudness_db": loudness_db(pcm), "highs": highs(pcm)}

    async def _async_stages(self, source: Source, pcm: bytes, line: dict[str, Any], stages: list) -> list[dict[str, Any]]:
        for i, stage in enumerate(stages):
            try:
                found = await stage.run(source, pcm, line)
            except Exception:  # noqa: BLE001 - one broken stage must not stop hearing
                LOGGER.exception("AAC audio: stage %s failed", stage.id)
                continue
            if not found:
                continue
            if found.get("drop"):
                return []
            if parts := found.get("split"):
                start = datetime.fromisoformat(line["time"])
                lines = []
                utterance = f"{source.id}-{line['time']}"  # the parts belong together (one breath, one request)
                for n, part in enumerate(parts):
                    a = max(0, int(part["start"] * SAMPLE_RATE)) * 2
                    b = min(len(pcm), int(part["end"] * SAMPLE_RATE) * 2)
                    if b - a < FRAME_BYTES:
                        continue
                    piece = pcm[a:b]
                    sub = {**self._segment_line(source, piece, start + timedelta(seconds=part["start"])),
                           **{k: v for k, v in found.items() if k != "split"},
                           **{k: v for k, v in part.items() if k not in ("start", "end")},
                           "utterance": utterance, "part": n + 1, "parts": len(parts)}
                    lines += await self._async_stages(source, piece, sub, stages[i + 1:])
                return lines
            line.update(found)
        return [line]

    async def _async_window(self, source: Source, stage, pcm: bytes) -> None:
        """A window of all audio for a window stage (sound captions): its findings become lines of their own."""
        if source.playing:
            return  # AAC's own voice
        try:
            found = await stage.run(source, pcm) or []
        except Exception:  # noqa: BLE001
            LOGGER.exception("AAC audio: stage %s failed", stage.id)
            return
        seconds = len(pcm) / (SAMPLE_RATE * 2)
        start = (dt_util.utcnow() - timedelta(seconds=seconds)).isoformat()
        for item in found:
            self._add_line(source, {"time": start, "source": source.id, "name": source.name, "kind": "sound",
                                    "addressed": False, "seconds": round(seconds, 1), "loudness_db": loudness_db(pcm),
                                    "stage": stage.id, **item})

    def _add_line(self, source: Source, line: dict[str, Any]) -> None:
        """Into the buffer, and to the hearing modules (the core only hears; they decide what to do)."""
        earlier = [x for x in self.transcript if x["source"] == source.id][-EARLIER_LINES:]
        self.transcript.append(line)
        async_notify(self.hass, "audio")
        for listener in self.entry.runtime_data.capabilities.listeners(self.entry.runtime_data.voice_domain.id):
            self.entry.async_create_background_task(
                self.hass, self._async_tell(listener, source, line, earlier), f"aac hearing {listener.id}"
            )

    async def _async_tell(self, listener, source: Source, line: dict[str, Any], earlier: list[dict[str, Any]]) -> None:
        try:
            await listener.heard(source, line, earlier)
        except Exception:  # noqa: BLE001 - one broken listener must not stop hearing
            LOGGER.exception("AAC audio: listener %s failed", listener.id)

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

    async def async_stt(self, pcm: bytes, prompt: str | None = None) -> dict[str, Any] | None:
        """Speech to text through the voice pool: models (any OpenAI-compatible server) or HA engines,
        in the owner's order; a machine that does not answer is skipped, HA's local engine is last.

        Returns {"text", "language", "confidence", "words", "engine"} (HA engines give only the text)."""
        broker = self.entry.runtime_data.broker
        tried: set[str] = set()
        while (resource := broker.pick("stt")) is not None and resource not in tried:
            tried.add(resource)
            if is_entity(resource):
                return await self._async_stt_entity(resource, pcm)
            heard = await self._async_stt_model(resource, pcm, prompt)
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

    async def _async_stt_model(self, model_id: str, pcm: bytes, prompt: str | None = None) -> dict[str, Any] | None:
        broker = self.entry.runtime_data.broker
        if (endpoint := resolve_model(self.hass, self.entry.runtime_data.store, model_id)) is None:
            return None
        provider = OpenAICompatProvider(
            async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key, timeout=60, connect_timeout=3
        )
        try:
            # No language = the model detects it per sentence (German and English alike)
            heard = await provider.transcribe(
                endpoint.model, pcm_to_wav(pcm), self.entry.runtime_data.store.audio["language"] or None, prompt
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

    async def async_senses(self, role: str, path: str, pcm: bytes) -> dict[str, Any] | None:
        """Ask the service of a voice-pool role (sounds / voices / mood) about a piece of audio; None when none answers."""
        broker = self.entry.runtime_data.broker
        if (resource := broker.pick(role)) is None or is_entity(resource):
            return None
        if (endpoint := resolve_model(self.hass, self.entry.runtime_data.store, resource)) is None:
            return None
        provider = OpenAICompatProvider(async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key)
        try:
            result = await provider.audio_post(path, pcm_to_wav(pcm))
        except ProviderConnectionError as err:
            broker.mark_down(resource, str(err))
            return None
        except ProviderError as err:
            LOGGER.info("AAC audio: %s failed: %s", role, err)
            return None
        broker.mark_up(resource)
        return result

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
        # what AAC says belongs to the conversation too (the Companion reads both sides)
        self._add_line(source, {"time": dt_util.utcnow().isoformat(), "source": source.id, "name": source.name,
                                "kind": "aac", "text": text, "addressed": False, "seconds": 0})
        return True

    async def async_answer(self, source: Source, text: str, before: list[str] | None = None,
                           speaker: dict[str, Any] | None = None, before_title: str = "The same person said just before",
                           mood: str | None = None) -> None:
        """The voice domain's master answers a spoken line; the answer goes back by voice.
        before = what was said just before it in the room (the wake word module's look-back);
        speaker = the line's voice (Voices module), so the model knows who talks ("that's me");
        mood = how it sounded (Mood module, mood_word), e.g. "sounds happy"."""
        from .master import async_run_master  # noqa: PLC0415 - avoid an import cycle

        data = self.entry.runtime_data.voice_domain  # one domain owns the voice
        chat_id = self._voice_chat(data, source)
        chat = data.chats.get(chat_id) or data.chats.create(f"Voice · {source.name}", chat_id)
        who = voice_label(speaker)
        data.chats.add_message(chat_id, "user", text, via="voice", mic=source.name, voice=who, mood=mood)
        how = ", ".join(x for x in (f"by {who}" if who else None, mood) if x)
        where = f"spoken at the microphone '{source.name}'{f' {how}' if how else ''}"
        said = f"[{where}; your answer is spoken aloud there] {text}"
        if before:  # the room's last lines go with it, so "…, remind me of that, computer" works
            said = (f"{before_title}:\n" + "\n".join(f"- {b}" for b in before)
                    + f"\n\nNow to you, {where} (your answer is spoken aloud there): {text}")
        run, result = await async_run_master(
            self.hass, self.entry, said,
            kind="chat", trigger=f"voice: {source.name}", title=chat["title"], chat_id=chat_id,
            conversation_id=chat["conversation_id"],
            extra_system_prompt=VOICE_PROMPT.format(
                name=source.name, area=f" in the {source.area}" if source.area else ""
            ),
            # Anyone can talk into a microphone: voice is anonymous until there is voice ID
            user_id=None,
            domain=data,
            history=data.chats.history(chat_id),
            own_words=text,  # memory learns from this line only, not from the room's lines around it
        )
        if result is not None:
            data.chats.set_conversation(chat_id, result.conversation_id)
        run.result = strip_marks(run.result or "")
        data.chats.add_message(chat_id, "aac", run.result or "(no answer)", via="voice", mic=source.name,
                               error=run.status == "failed", run_id=run.id, steps=run.steps)
        if run.result and self.sources.get(source.id) is source:
            # An error is not read out (long, technical, and loud in the room): one short sentence, details in the panel
            await self.async_say(source, VOICE_FAILED if run.status == "failed" else run.result)

    def _voice_chat(self, data, source: Source) -> str:
        """The voice chat of a source: a new one after a quiet pause (a voice session), so old turns
        and old mistakes do not pile up as examples in the model's context."""
        current = self.voice_chats.get(source.id) or f"voice_{source.id}"
        chat = data.chats.get(current)
        if chat and chat["messages"] and (chat.get("archived")  # put away by the owner: a new session
                                          or (dt_util.utcnow() - datetime.fromisoformat(chat["updated"])) > VOICE_SESSION_GAP):
            current = f"voice_{source.id}_{dt_util.utcnow().strftime('%Y%m%d%H%M')}"
        self.voice_chats[source.id] = current
        return current

    # ------------------------------------------------------------ panel
    def live(self) -> list[dict[str, Any]]:
        """The live view per source: the last seconds of loudness and speech, and what happens now."""
        return [{"id": s.id, "name": s.name, "meter": list(s.segmenter.meter), "partial": s.partial,
                 "speech_seconds": round(s.segmenter.frames / 100, 1),
                 "state": "speaking" if s.playing else "transcribing" if s.transcribing else
                 "hearing speech" if s.segmenter.active else "listening"} for s in self.sources.values()]

    @callback
    def changed(self) -> None:
        """A listener marked a line (addressed, a note): the panel shows it."""
        async_notify(self.hass, "audio")

    def overview(self) -> dict[str, Any]:
        """Live state for the panel and the setup tools."""
        data = self.entry.runtime_data
        hearing = [h.title for h in data.capabilities.listeners(data.voice_domain.id)] if data.voice_domain else []
        return {
            "hearing": hearing,
            "connected": [s.summary() for s in self.sources.values()],
            "transcript": list(self.transcript)[-30:],
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


def loudness_db(pcm: bytes) -> float | None:
    """How loud a piece of audio is (dBFS: 0 = full scale, about -90 = silence)."""
    samples = array("h", pcm)[::4]
    rms = math.sqrt(sum(v * v for v in samples) / max(1, len(samples)))
    return round(20 * math.log10(rms / 32767), 1) if rms > 0 else None


def highs(pcm: bytes) -> float | None:
    """Share of the energy above 4 kHz, in percent. Speech has a few percent up there; a Bluetooth
    speaker in narrowband (phone quality, 8 kHz) has almost nothing, which makes speech to text worse."""
    import numpy as np  # noqa: PLC0415 - HA ships numpy; only needed here

    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)[: 4 * SAMPLE_RATE]  # 4 s are enough (~3 ms)
    if len(samples) < SAMPLE_RATE // 4:
        return None
    power = np.abs(np.fft.rfft(samples * np.hanning(len(samples)))) ** 2
    freqs = np.fft.rfftfreq(len(samples), 1 / SAMPLE_RATE)
    total = float(power[freqs > 80].sum())
    return round(100 * float(power[freqs > 4000].sum()) / total, 2) if total > 0 else None


def voice_label(speaker: dict[str, Any] | None) -> str | None:
    """A line's voice in words for the model: "voice #8", "probably voice #8", "Astor (person)"."""
    if not speaker:
        return None
    label = speaker["name"] + (f" ({speaker['kind']})" if speaker.get("kind") not in (None, "unknown") else "")
    return ("probably " if speaker.get("sure") is False else "") + label


def mood_word(line: dict[str, Any]) -> str | None:
    """A line's mood for the model when the Mood module is sure: "sounds happy" (neutral says nothing)."""
    mood = line.get("mood") or {}
    return f"sounds {mood['label']}" if mood.get("sure") else None
