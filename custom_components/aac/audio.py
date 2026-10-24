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

if TYPE_CHECKING:
    from .core import Core

SAMPLE_RATE = 16000
FRAME_BYTES = 320  # 10 ms of 16-bit mono
PREROLL_FRAMES = 50  # 500 ms before speech starts (the detector reacts late)
START_FRAMES = 3
MIN_SPEECH_FRAMES = 30
METER_FRAMES = 10  # one meter point = 100 ms
METER_POINTS = 100


def pcm_to_wav(pcm: bytes) -> bytes:
    """16 kHz mono PCM as WAV."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buf.getvalue()


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
        self._mics: tuple[float, list] = (float("-inf"), [])
        self._spoke: dict[str, float] = {}  # satellite → when AAC last finished speaking there (time.time())

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
    def disconnect(self, source: Source) -> None:
        """A satellite went away."""
        if self.sources.get(source.id) is source:
            del self.sources[source.id]
            self.core.notify("audio")

    @callback
    def feed(self, source: Source, chunk: bytes) -> None:
        """Raw audio: to every Microphone card that takes this source (not while AAC speaks there)."""
        source.meter.feed(chunk)
        if source.playing or not (domain := self.core.domain):
            return
        now = time.monotonic()
        if now - self._mics[0] > 1:  # looked up at most once a second (audio comes about 50 times a second)
            self._mics = (now, [c for c in domain.cards.values() if c.type == "microphone"])
        for card in self._mics[1]:
            if card.settings.get("source") in ("any", source.id, None, ""):
                card.hear(chunk, {"source": source.id, "name": source.name, "area": source.area})

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
        source.meter.reset()
        self.core.notify("audio")

    # ------------------------------------------------------------------ speech to text
    async def stt(self, pcm: bytes, language: str | None = None) -> dict[str, Any] | None:
        """Speech to text through the order in the audio settings; a machine that does not answer is skipped."""
        for item in list(self.settings.get("stt") or []):
            if item.startswith("stt."):
                if (found := await self._stt_entity(item, pcm, language)) is not None:
                    return found
                continue
            model = self.core.pool.get(item)
            if not model or not self.core.pool.usable(item):
                continue
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
                self.core.pool.mark_up(item)
                return {"text": str(data.get("text") or "").strip(), "language": data.get("language"), "engine": item}
            except (aiohttp.ClientConnectionError, TimeoutError):
                self.core.pool.mark_down(item)
            except (aiohttp.ClientError, ValueError) as err:
                LOGGER.warning("AAC audio: speech to text failed: %s", err)
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
    async def tts(self, text: str, language: str | None = None) -> bytes | None:
        """Text to speech (WAV) with the chosen HA engine (the voice set for the language, else its default)."""
        engine = self.settings.get("tts")
        if not engine:
            LOGGER.warning("AAC audio: no voice engine set (Audio in the sidebar)")
            return None
        options = {tts.ATTR_PREFERRED_FORMAT: "wav", tts.ATTR_PREFERRED_SAMPLE_RATE: 22050,
                   tts.ATTR_PREFERRED_SAMPLE_CHANNELS: 1, tts.ATTR_PREFERRED_SAMPLE_BYTES: 2}
        voice = (self.settings.get("voices") or {}).get((language or self.hass.config.language or "en")[:2])
        tries = [(voice["language"], {**options, tts.ATTR_VOICE: voice["voice"]})] if voice else []
        for lang, opts in [*tries, (None, options)]:
            try:
                stream = tts.async_create_stream(self.hass, engine, lang, opts)
                stream.async_set_message(text)
                return b"".join([chunk async for chunk in stream.async_stream_result()])
            except Exception as err:  # noqa: BLE001
                LOGGER.warning("AAC audio: text to speech failed: %s", err)
        return None

    async def say(self, source_id: str, text: str, language: str | None = None) -> bool:
        """Speak at a satellite, at the set volume (0 = never aloud)."""
        if source_id not in self.sources or int(self.settings.get("volume", 40)) <= 0 or not (audio := await self.tts(text, language)):
            return False
        return await self.play(source_id, audio, text)

    async def play(self, source_id: str, audio: bytes, text: str = "") -> bool:
        """Play a 16-bit WAV at a satellite, at the set volume (its media player and Voice out use this)."""
        source = self.sources.get(source_id)
        volume = int(self.settings.get("volume", 40))
        if source is None or volume <= 0:
            return False
        if volume < 100:
            audio = await self.hass.async_add_executor_job(scale_wav, audio, volume / 100)
        source.playing = True
        self.core.notify("audio")
        await source.send({"type": "play", "format": "wav", "bytes": len(audio), "text": text})
        await source.send(audio)
        return True

    async def stop(self, source_id: str) -> bool:
        """Stop what a satellite is playing (True if it was playing)."""
        source = self.sources.get(source_id)
        if source is None or not source.playing:
            return False
        await source.send({"type": "stop"})
        return True

    def summary(self) -> dict[str, Any]:
        """For the panel's Audio page."""
        return {"sources": [s.summary() for s in self.sources.values()], "settings": self.settings,
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
