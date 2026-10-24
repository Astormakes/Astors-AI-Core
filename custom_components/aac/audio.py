"""Audio (core, docs/v2): satellites stream in, the audio cards hear, AAC speaks back.

A satellite (the AAC satellite add-on next to a mic/speaker, or the PC program) streams raw 16 kHz mono PCM over a
WebSocket. The hub hands each source's audio to the running domain's Microphone cards and offers speech to text
(the order in the audio settings: Whisper-like model servers, HA's STT engines) and text to speech (an HA TTS
engine) at a source. It does not listen while AAC speaks there. Raw audio stays in RAM.

Protocol: satellite → {"type": "hello", "id", "name", "area", "rate": 16000}; AAC → {"type": "welcome",
"source_id"}; satellite → binary PCM; AAC → {"type": "play", "format": "wav", "bytes", "text"} + the WAV;
satellite → {"type": "played"}; AAC → {"type": "stop"} cuts what it plays (it answers "played"). Each satellite is
also an HA media player (`media_player.py`): anything in HA can play on it. Auth: an HA access token (the add-on's comes from `<config>/aac/satellite.json`).
"""

from __future__ import annotations

from array import array
import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
import io
import json
import math
from pathlib import Path
import re
import secrets
import sys
import time
from typing import TYPE_CHECKING, Any
import wave

import aiohttp
from aiohttp import WSMsgType, web

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.components import stt, tts
from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER
from .languages import NAMES, code_of, describe, from_ha
from .models import ModelError, ModelUnreachable, run

if TYPE_CHECKING:
    from .core import Core

SAMPLE_RATE = 16000
CLIP_SECONDS = 300  # how long a clip for a media player stays fetchable
QUEUE_WAIT = 120  # seconds a queued clip waits for the one before it at most
TIMING_START, TIMING_KEEP = "speech ended", 8  # the step that starts a spoken turn; turns kept for the Audio page
FRAME_BYTES = 320  # 10 ms of 16-bit mono
PREROLL_FRAMES = 50  # 500 ms before speech starts (the detector reacts late)
START_FRAMES = 3
MIN_SPEECH_FRAMES = 30
METER_FRAMES = 10  # one meter point = 100 ms
METER_POINTS = 100


def pcm_to_wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    """16-bit mono PCM as WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """A 16-bit WAV as mono PCM and its sample rate (a clip: {"pcm", "rate"})."""
    with wave.open(io.BytesIO(data)) as src:
        rate, channels, frames = src.getframerate(), src.getnchannels(), src.readframes(src.getnframes())
    if channels > 1:  # keep the first channel
        samples = array("h", frames)
        frames = array("h", samples[::channels]).tobytes()
    return frames, rate


def resample(pcm: bytes, rate: int, to: int = SAMPLE_RATE) -> bytes:
    """16-bit mono PCM at another sample rate (linear; good enough for speech)."""
    if rate == to or not pcm:
        return pcm
    import numpy as np  # noqa: PLC0415

    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = max(1, round(len(x) * to / rate))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.int16).tobytes()


def level(chunk: bytes) -> int:
    """Loudness 0 (silence) … 100 (full scale, about 60 dB of range)."""
    samples = array("h", chunk[: len(chunk) - len(chunk) % 2])
    if not samples:
        return 0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return int(max(0.0, min(100.0, (20 * math.log10(rms / 32768) + 60) / 60 * 100))) if rms > 0 else 0


class EnergyVad:
    """Loudness as speech (when HA's speech detector is missing)."""

    def __init__(self, threshold: float = 500) -> None:
        """Initialize."""
        self.threshold = threshold

    def __call__(self, frame: bytes) -> float:
        """Speech probability of one 10 ms frame."""
        samples = array("h", frame)
        return 1.0 if (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5 >= self.threshold else 0.0


def make_vad() -> Callable[[bytes], float]:
    """HA's own speech detector (micro VAD), else loudness."""
    try:
        from pymicro_vad import MicroVad  # noqa: PLC0415 - optional
    except ImportError:
        return EnergyVad()
    vad = MicroVad()
    return lambda frame: vad.Process10ms(frame)


class Segmenter:
    """Cuts a PCM stream into pieces of speech that end with a pause; keeps a live meter."""

    def __init__(self, vad: Callable[[bytes], float], pause_ms: int = 800, longest_s: int = 30) -> None:
        """Initialize."""
        self._vad = vad
        self._pending = b""
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._segment: list[bytes] | None = None
        self._run = self._silence = self._speech = 0
        self.end_silence = max(10, pause_ms // 10)
        self.longest = max(100, longest_s * 100)
        self.meter: deque[tuple[int, float, bool]] = deque(maxlen=METER_POINTS)
        self._acc = [0, 0, 0]
        self.peak_db: float | None = None

    @property
    def active(self) -> bool:
        """Inside a piece of speech."""
        return self._segment is not None

    def _measure(self, frame: bytes, speech: bool) -> None:
        samples = array("h", frame)
        peak = max(abs(min(samples)), max(samples)) if samples else 0
        acc = self._acc
        acc[0], acc[1], acc[2] = max(acc[0], peak), acc[1] + speech, acc[2] + 1
        if acc[2] >= METER_FRAMES:
            lvl = 0 if acc[0] <= 0 else max(0, min(100, int(100 + 100 / 60 * 20 * math.log10(acc[0] / 32767))))
            self.peak_db = round(20 * math.log10(acc[0] / 32767), 1) if acc[0] > 0 else None
            self.meter.append((lvl, round(acc[1] / acc[2], 2), self._segment is not None))
            self._acc = [0, 0, 0]

    def feed(self, chunk: bytes) -> list[bytes]:
        """Add audio; returns finished pieces."""
        done: list[bytes] = []
        data = self._pending + chunk
        usable = len(data) - len(data) % FRAME_BYTES
        self._pending = data[usable:]
        for i in range(0, usable, FRAME_BYTES):
            frame = data[i:i + FRAME_BYTES]
            speech = self._vad(frame) >= 0.5
            self._measure(frame, speech)
            if self._segment is None:
                self._preroll.append(frame)
                self._run = self._run + 1 if speech else 0
                if self._run >= START_FRAMES:
                    self._segment, self._speech, self._silence = list(self._preroll), self._run, 0
                    self._preroll.clear()
                continue
            self._segment.append(frame)
            if speech:
                self._speech, self._silence = self._speech + 1, 0
            else:
                self._silence += 1
            if self._silence >= self.end_silence or len(self._segment) >= self.longest:
                if self._speech >= MIN_SPEECH_FRAMES:
                    done.append(b"".join(self._segment))
                self._segment, self._run = None, 0
        return done

    def reset(self) -> None:
        """Forget a half-heard piece (AAC itself spoke)."""
        self._pending, self._segment, self._run = b"", None, 0
        self._preroll.clear()


@dataclass
class Source:
    """A connected satellite."""

    id: str
    name: str
    area: str | None
    send: Callable[[Any], Awaitable[None]]
    meter: Segmenter = field(default_factory=lambda: Segmenter(EnergyVad()))
    connected: str = field(default_factory=lambda: dt_util.utcnow().isoformat())
    playing: bool = False

    def summary(self) -> dict[str, Any]:
        """For the panel."""
        return {"id": self.id, "name": self.name, "area": self.area, "connected": self.connected, "playing": self.playing,
                "meter": list(self.meter.meter)[-40:], "peak_db": self.meter.peak_db}


class AudioHub:
    """The satellites, speech to text and text to speech (core: shared by the domains)."""

    def __init__(self, core: Core) -> None:
        """Initialize."""
        self.core, self.hass = core, core.hass
        self.sources: dict[str, Source] = {}
        self._spoke: dict[str, float] = {}  # satellite → when AAC last finished speaking there (time.time())
        self._queues: dict[str, asyncio.Lock] = {}  # clips waiting to play at a satellite, one after another
        self._idle: dict[str, asyncio.Event] = {}  # set when a satellite finished playing (or was stopped)
        self._turns: dict[str, int] = {}  # counts the stops at a satellite (a queued clip of an older turn is dropped)
        self._timing: list[dict[str, Any]] = []  # spoken turns: when each step happened (mark)

    @property
    def settings(self) -> dict[str, Any]:
        """{"stt": [model ids / stt entities, in order], "tts": entity, "voices": {lang: {language, voice}}, "volume"}."""
        return self.core.data.setdefault("audio", {"stt": [], "tts": None, "voices": {}, "volume": 40, "known": []})

    # ------------------------------------------------------------------ sources
    @callback
    def connect(self, source_id: str, name: str, area: str | None, send) -> Source:
        """A satellite came online."""
        source = Source(source_id, name, area, send)
        self.sources[source_id] = source
        known = [k for k in self.settings.setdefault("known", []) if k["id"] != source_id]
        self.settings["known"] = [*known, {"id": source_id, "name": name}]
        self.core.save()
        self.core.notify("audio")
        return source

    @callback
    def forget(self, source_id: str) -> None:
        """A satellite that is gone for good: out of the known ones (its media player goes too)."""
        self.settings["known"] = [k for k in self.settings.get("known", []) if k["id"] != source_id]
        self._spoke.pop(source_id, None)
        self.core.save()
        self.core.notify("audio", forgot=source_id)

    @callback
    def disconnect(self, source: Source) -> None:
        """A satellite went away."""
        if self.sources.get(source.id) is source:
            del self.sources[source.id]
            self.core.notify("audio")

    @callback
    def feed(self, source: Source, chunk: bytes) -> None:
        """Raw audio: to every card on the core's "audio" channel (not while AAC speaks there)."""
        source.meter.feed(chunk)
        if source.playing:
            return
        meta = {"source": source.id, "name": source.name, "area": source.area}
        for _, handler in self.core.subscribers("audio"):
            handler(chunk, meta)

    def mark(self, source_id: str | None, stage: str) -> None:
        """A step of a spoken turn at a satellite, for the Audio page's timing table: "speech ended" starts a turn,
        later stages count from it (each only once per turn)."""
        if not source_id:
            return
        now = time.monotonic()
        if stage == TIMING_START:
            self._timing.append({"source": source_id, "at": time.time(), "start": now, "stages": {}})
            del self._timing[:-TIMING_KEEP * 4]
            return
        turn = next((t for t in reversed(self._timing) if t["source"] == source_id), None)
        if turn is not None and stage not in turn["stages"] and now - turn["start"] < 120:
            turn["stages"][stage] = round(now - turn["start"], 2)

    def timing(self) -> list[dict[str, Any]]:
        """The last spoken turns that got an answer: when, where, and each step in seconds after the speech ended."""
        done = [t for t in self._timing if "playing" in t["stages"]]
        return [{"at": t["at"], "source": t["source"], "stages": t["stages"]} for t in done[-TIMING_KEEP:]][::-1]

    def spoken(self) -> dict[str, Any]:
        """When AAC last spoke, for templates ({{ audio.last.seconds }}, {{ audio.satellites[id].seconds }}): seconds
        since it stopped speaking (0 while it speaks, a huge number if never), overall and per satellite."""
        now, never = time.time(), 10**9
        sats = {}
        for k in self.settings.get("known", []):
            speaking = bool((s := self.sources.get(k["id"])) and s.playing)
            at = self._spoke.get(k["id"])
            sats[k["id"]] = {"name": k["name"], "speaking": speaking, "seconds": 0 if speaking else round(now - at, 1) if at else never}
        last_id = max(sats, key=lambda i: (sats[i]["speaking"], self._spoke.get(i, 0)), default=None)
        last = {"satellite": last_id, **sats[last_id]} if last_id else {"satellite": None, "name": None, "speaking": False, "seconds": never}
        return {"last": last, "satellites": sats}

    @callback
    def played(self, source: Source) -> None:
        """The satellite finished playing AAC's voice."""
        self._spoke[source.id] = time.time()
        source.playing = False
        if idle := self._idle.get(source.id):
            idle.set()
        source.meter.reset()
        self.core.notify("audio")

    # ------------------------------------------------------------------ speech to text
    async def stt(self, pcm: bytes, language: str | None = None, entries: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """Speech to text: a card's models first (free ones, else their queues), then HA's engines from the Audio page.
        No language given: the home's (one: that one; several: detected, and heard again in the main language when the
        model heard one outside them, e.g. a short word taken for Welsh)."""
        home = self.core.languages()
        if not language and len(home) == 1:
            language = home[0]
        found = await self._stt(pcm, language or None, entries)
        if found and not language and code_of(found.get("language")) not in home and not found.get("engine", "").startswith("stt."):  # a model: ask again
            found = await self._stt(pcm, home[0], entries) or found
        if found:
            found["language"] = code_of(found.get("language")) or found.get("language")
        return found

    async def _stt(self, pcm: bytes, language: str | None, entries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        async def transcribe(model):
            fd = aiohttp.FormData()
            fd.add_field("file", pcm_to_wav(pcm), filename="speech.wav", content_type="audio/wav")
            fd.add_field("model", model["model"])
            fd.add_field("response_format", "json")
            if language:
                fd.add_field("language", language)
            headers = {"Authorization": f"Bearer {model['api_key']}"} if model.get("api_key") else {}
            try:
                async with async_get_clientsession(self.hass).post(
                        f"{model['base_url'].rstrip('/')}/audio/transcriptions", data=fd, headers=headers,
                        timeout=aiohttp.ClientTimeout(total=60, sock_connect=3)) as resp:
                    resp.raise_for_status()
                    data = await resp.json(content_type=None)
            except (aiohttp.ClientConnectionError, TimeoutError) as err:
                raise ModelUnreachable(str(err)) from err
            except (aiohttp.ClientError, ValueError) as err:
                raise ModelError(str(err)) from err
            return {"text": str(data.get("text") or "").strip(), "language": data.get("language"), "engine": model["id"]}
        if entries is None:
            entries = self.core.pool.entries(None, "speech to text")
        found, _ = await run(self.core.pool, entries, transcribe)
        if found is not None:
            return found
        for engine in [e for e in self.settings.get("stt") or [] if e.startswith("stt.")]:
            if (found := await self._stt_entity(engine, pcm, language)) is not None:
                return found
        return None

    async def _stt_entity(self, entity_id: str, pcm: bytes, language: str | None) -> dict[str, Any] | None:
        if (entity := stt.async_get_speech_to_text_entity(self.hass, entity_id)) is None:
            return None
        langs = list(entity.supported_languages)
        lang = next((x for x in langs if language and x.split("-")[0] == language.split("-")[0]), None) or \
            next((x for x in langs if x.split("-")[0] == self.hass.config.language.split("-")[0]), langs[0] if langs else "en")
        meta = stt.SpeechMetadata(language=lang, format=stt.AudioFormats.WAV, codec=stt.AudioCodecs.PCM,
                                  bit_rate=stt.AudioBitRates.BITRATE_16, sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
                                  channel=stt.AudioChannels.CHANNEL_MONO)

        async def chunks() -> AsyncIterator[bytes]:
            for i in range(0, len(pcm), 3200):
                yield pcm[i:i + 3200]

        try:
            result = await entity.internal_async_process_audio_stream(meta, chunks())
        except Exception as err:  # noqa: BLE001 - one bad piece must not stop the hub
            LOGGER.warning("AAC audio: %s failed: %s", entity_id, err)
            return None
        if result.result != stt.SpeechResultState.SUCCESS:
            return None
        return {"text": result.text or "", "language": lang, "engine": entity_id}

    # ------------------------------------------------------------------ speaking
    async def tts(self, text: str, language: str | None = None, engine: str | None = None,
                  voices: dict[str, dict[str, str]] | None = None) -> bytes | None:
        """Text to speech (WAV) with an HA engine (a card's, else the Audio page's default), in the voice a card set for
        the language ({code: {language: locale, voice}}), else the engine's default."""
        engine = engine or self.settings.get("tts")
        if not engine:
            LOGGER.warning("AAC audio: no voice engine set (Audio in the sidebar)")
            return None
        options = {tts.ATTR_PREFERRED_FORMAT: "wav", tts.ATTR_PREFERRED_SAMPLE_RATE: 22050,
                   tts.ATTR_PREFERRED_SAMPLE_CHANNELS: 1, tts.ATTR_PREFERRED_SAMPLE_BYTES: 2}
        voice = (voices or {}).get(code_of(language) or self.core.languages()[0])
        tries = [(voice["language"], {**options, tts.ATTR_VOICE: voice["voice"]})] if voice else []
        for lang, opts in [*tries, (None, options)]:
            try:
                stream = tts.async_create_stream(self.hass, engine, lang, opts)
                stream.async_set_message(text)
                return b"".join([chunk async for chunk in stream.async_stream_result()])
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("AAC audio: text to speech failed: %s", err)
        return None

    def voices(self, engine: str | None = None) -> dict[str, dict[str, Any]]:
        """An engine's voices for each home language, from all its variants (en_GB, en_US …):
        {code: {locale, voices: [{id, name, locale}]}} (locale None: the engine has none)."""
        from homeassistant.components.tts.helper import get_engine_instance  # noqa: PLC0415

        entity = get_engine_instance(self.hass, engine or self.settings.get("tts") or "")
        if entity is None:
            return {}
        langs, found = list(entity.supported_languages or []), {}
        for code in self.core.languages():
            locales = [x for x in langs if code_of(x) == code]
            voices = [{"id": v.voice_id, "name": v.name + (f" · {loc}" if len(locales) > 1 else ""), "locale": loc}
                      for loc in locales for v in entity.async_get_supported_voices(loc) or []]
            found[code] = {"locale": locales[0] if locales else None, "voices": voices}
        return found

    async def say(self, source_id: str, text: str, language: str | None = None, engine: str | None = None,
                  voices: dict[str, dict[str, str]] | None = None) -> bool:
        """Speak at a satellite, at the set volume (0 = never aloud)."""
        if source_id not in self.sources or int(self.settings.get("volume", 40)) <= 0 or \
                not (audio := await self.tts(text, language, engine, voices)):
            return False
        return await self.play(source_id, audio, text)

    async def play(self, source_id: str, audio: bytes, text: str = "", queue: bool = False) -> bool:
        """Play a 16-bit WAV at a satellite, at the set volume (its media player and the Speaker card use this).
        queue: after what is playing there (sentence by sentence); else it cuts in. A stop drops the queue."""
        source = self.sources.get(source_id)
        volume = int(self.settings.get("volume", 40))
        if source is None or volume <= 0:
            return False
        if queue:
            turn = self._turns.setdefault(source_id, 0)
            async with self._queues.setdefault(source_id, asyncio.Lock()):
                idle = self._idle.setdefault(source_id, asyncio.Event())
                while source.playing and turn == self._turns[source_id]:
                    idle.clear()
                    try:
                        await asyncio.wait_for(idle.wait(), QUEUE_WAIT)
                    except TimeoutError:
                        break
                if turn != self._turns[source_id] or source_id not in self.sources:  # stopped meanwhile
                    return False
                return await self._send_play(source, audio, text, volume)
        return await self._send_play(source, audio, text, volume)

    async def _send_play(self, source: Source, audio: bytes, text: str, volume: int) -> bool:
        if volume < 100:
            audio = await self.hass.async_add_executor_job(scale_wav, audio, volume / 100)
        source.playing = True
        self.mark(source.id, "playing")
        self.core.notify("audio")
        await source.send({"type": "play", "format": "wav", "bytes": len(audio), "text": text})
        await source.send(audio)
        return True

    def clip_url(self, wav: bytes) -> str:
        """A short-lived address where a WAV can be fetched (an HA media player plays a clip from it)."""
        from homeassistant.helpers.network import get_url  # noqa: PLC0415

        now = time.monotonic()
        self._clips = {k: v for k, v in getattr(self, "_clips", {}).items() if now - v[0] < CLIP_SECONDS}
        key = secrets.token_hex(16)
        self._clips[key] = (now, wav)
        return f"{get_url(self.hass, prefer_external=False)}{ClipView.url.replace('{key}', key)}"

    async def stop(self, source_id: str) -> bool:
        """Stop what a satellite is playing and drop what waits to play there (True if it was playing)."""
        self._turns[source_id] = self._turns.get(source_id, 0) + 1
        if idle := self._idle.get(source_id):
            idle.set()
        source = self.sources.get(source_id)
        if source is None or not source.playing:
            return False
        await source.send({"type": "stop"})
        return True

    def summary(self) -> dict[str, Any]:
        """For the panel's Audio page."""
        return {"sources": [s.summary() for s in self.sources.values()], "settings": self.settings,
                "languages": describe(self.core.languages()), "languages_own": bool(self.core.data.get("languages")),
                "languages_ha": describe(from_ha(self.hass.config.language, self.hass.config.country)),
                "language_names": NAMES, "timing": self.timing(),
                "stt_entities": sorted(self.hass.states.async_entity_ids("stt")),
                "tts_entities": sorted(self.hass.states.async_entity_ids("tts"))}


def scale_wav(audio: bytes, gain: float) -> bytes:
    """A 16-bit WAV, quieter."""
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
        dst.setnchannels(params.nchannels)
        dst.setsampwidth(params.sampwidth)
        dst.setframerate(params.framerate)
        dst.writeframes(samples.tobytes())
    return out.getvalue()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "source"


class ClipView(HomeAssistantView):
    """A clip for an HA media player (the address is the secret; it is gone after a few minutes)."""

    url = "/api/aac/clip/{key}.wav"
    name = "api:aac:clip"
    requires_auth = False  # a media player fetches it without a token

    async def get(self, request: web.Request, key: str) -> web.StreamResponse:
        """The WAV, if it is still there."""
        core = request.app[KEY_HASS].data.get(DOMAIN)
        found = getattr(core.audio, "_clips", {}).get(key) if core else None
        if found is None:
            return web.Response(status=404)
        return web.Response(body=found[1], content_type="audio/wav")


class AudioView(HomeAssistantView):
    """One WebSocket per satellite."""

    url = "/api/aac/audio"
    name = "api:aac:audio"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        """Accept a satellite and pump its audio into the hub."""
        hass = request.app[KEY_HASS]
        core = hass.data.get(DOMAIN)
        if core is None:
            return web.Response(status=503, text="AAC is not loaded")
        hub = core.audio
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        try:
            hello = await ws.receive_json(timeout=10)
        except (TimeoutError, TypeError, ValueError):
            await ws.close(message=b"expected hello")
            return ws
        if hello.get("type") != "hello" or int(hello.get("rate", SAMPLE_RATE)) != SAMPLE_RATE:
            await ws.close(message=b"hello with rate 16000 expected")
            return ws
        lock = asyncio.Lock()

        async def send(item) -> None:
            async with lock:
                if ws.closed:
                    return
                if isinstance(item, bytes):
                    await ws.send_bytes(item)
                else:
                    await ws.send_json(item)

        name = str(hello.get("name") or "Microphone")[:60]
        source = hub.connect(_slug(str(hello.get("id") or name)), name, hello.get("area"), send)
        LOGGER.info("AAC audio: %s connected", name)
        await send({"type": "welcome", "source_id": source.id})
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    hub.feed(source, msg.data)
                elif msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except ValueError:
                        continue
                    if data.get("type") == "played":
                        hub.played(source)
        finally:
            hub.disconnect(source)
            LOGGER.info("AAC audio: %s disconnected", name)
        return ws


async def async_satellite_access(core: Core) -> None:
    """The satellite add-on's own HA user and token, in `<config>/aac/satellite.json` (kept when it is there)."""
    hass = core.hass
    path = Path(hass.config.path("aac", "satellite.json"))
    info = core.data.setdefault("satellite_app", {})
    user = await hass.auth.async_get_user(info["user_id"]) if info.get("user_id") else None
    if user is None:
        user = await hass.auth.async_create_user("AAC satellite", group_ids=[GROUP_ID_USER], local_only=True)
        info.clear()
        info["user_id"] = user.id
    exists = await hass.async_add_executor_job(path.exists)
    if not exists or not any(t.id == info.get("refresh_token_id") for t in user.refresh_tokens.values()):
        refresh = await hass.auth.async_create_refresh_token(user, client_name="AAC satellite",
                                                             token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
                                                             access_token_expiration=timedelta(days=3650))
        info["refresh_token_id"] = refresh.id
        data = {"url": f"http://homeassistant:{hass.http.server_port or 8123}", "token": hass.auth.async_create_access_token(refresh),
                "created": dt_util.utcnow().isoformat()}

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2))
            path.chmod(0o600)

        await hass.async_add_executor_job(write)
    core.save()
