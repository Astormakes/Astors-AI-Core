"""Sound for the cards (docs/v2 page 2): plain helpers without Home Assistant, so the card worker has them too: WAV and
PCM, resampling, loudness, speech detection (Home Assistant's micro VAD when it is installed, else loudness) and the
Segmenter that cuts speech at its pauses. What they need is imported here, at the top (the walled worker cannot load
anything later)."""

from __future__ import annotations

from array import array
from collections import deque
from collections.abc import Callable
import io
import math
from typing import Any
import wave

import numpy as np

try:
    from pymicro_vad import MicroVad
except ImportError:  # optional
    MicroVad = None

SAMPLE_RATE = 16000
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
    if MicroVad is None:
        return EnergyVad()
    vad = MicroVad()
    return lambda frame: vad.Process10ms(frame)


class Segmenter:
    """Cuts a PCM stream into pieces of speech that end with a pause; keeps a live meter."""

    def __init__(self, vad: Callable[[bytes], float], pause_ms: int = 800, longest_s: int = 30, phrase_ms: int = 0) -> None:
        """Initialize. phrase_ms: a shorter pause inside the speech that already hands out what came so far (a phrase);
        0: only whole pieces (up to the pause)."""
        self._vad = vad
        self._pending = b""
        self._preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        self._segment: list[bytes] | None = None
        self._run = self._silence = self._speech = 0
        self.end_silence = max(10, pause_ms // 10)
        self.phrase_silence = phrase_ms // 10 if 0 < phrase_ms < pause_ms else 0
        self._cut = self._cut_speech = 0  # where the current phrase began (frame index), the speech in it
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
        """Add audio; returns finished pieces (whole ones, up to the pause)."""
        return [b"".join(audio) for kind, audio in self.feed_parts(chunk) if kind == "piece"]

    def feed_parts(self, chunk: bytes) -> list[tuple[str, Any]]:
        """Add audio; returns what happened, in order: ("start", frames since the speech really began), ("phrase", pcm)
        at a short pause inside the speech (only with phrase_ms), ("piece", frames) for a whole piece (without phrases),
        ("end", parts) when the pause came."""
        done: list[tuple[str, Any]] = []
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
                    self._cut, self._cut_speech, self._phrases = 0, self._run, 0
                    self._preroll.clear()
                    done.append(("start", len(self._segment)))
                continue
            self._segment.append(frame)
            if speech:
                self._speech, self._silence, self._cut_speech = self._speech + 1, 0, self._cut_speech + 1
            else:
                self._silence += 1
            if self.phrase_silence and self._silence == self.phrase_silence and self._cut_speech >= MIN_SPEECH_FRAMES:
                done.append(("phrase", b"".join(self._segment[self._cut:])))  # a short pause: what came so far goes
                self._cut, self._cut_speech, self._phrases = len(self._segment), 0, self._phrases + 1
            if self._silence >= self.end_silence or len(self._segment) >= self.longest:
                if self.phrase_silence:
                    if self._cut_speech >= MIN_SPEECH_FRAMES:  # cut off by the longest: the rest is a phrase too
                        done.append(("phrase", b"".join(self._segment[self._cut:])))
                        self._phrases += 1
                    if self._phrases:
                        done.append(("end", self._phrases))
                elif self._speech >= MIN_SPEECH_FRAMES:
                    done.append(("piece", self._segment))
                    done.append(("end", 1))
                self._segment, self._run = None, 0
        return done

    def reset(self) -> None:
        """Forget a half-heard piece (AAC itself spoke)."""
        self._pending, self._segment, self._run = b"", None, 0
        self._preroll.clear()
