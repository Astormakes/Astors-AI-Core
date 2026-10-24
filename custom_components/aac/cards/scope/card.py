"""Scope: shows live audio inside the card in the grid (level bars, the waveform or the spectrum) and passes it on
unchanged. Frames are made only while a Nodes page shows it."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from custom_components.aac.api import SAMPLE_RATE, Card, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

KEEP = 2048  # samples it looks at (0.13 s at 16 kHz)
BANDS = 40  # spectrum bars, spread like hearing (log)
POINTS = 64  # waveform points (min and max each; KEEP divides by it)


@register
class Scope(Card):
    """Shows audio."""

    type = "scope"
    title = "Scope"
    category = "Audio"
    help = ("Shows the audio passing through it inside the card: level bars, the waveform or the spectrum. The audio goes "
            "on unchanged, so it can sit anywhere in an audio chain.")
    inputs = {"in": "audio"}
    outputs = {"out": "audio"}
    ports_help = {"in": "live audio", "out": "the same audio, unchanged"}
    settings_schema = {"mode": {"type": "select", "title": "Shows", "default": "spectrum",
                                "options": [["levels", "Level bars"], ["waveform", "Waveform"], ["spectrum", "Spectrum"]]},
                       "fps": {"type": "number", "title": "Frames per second", "default": 15,
                               "help": "How often the view changes (1–30); more costs a little more."}}
    view = {"height": 90}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._samples = np.zeros(KEEP, dtype=np.float32)
        self._next = 0.0
        edges = np.geomspace(60, SAMPLE_RATE / 2, BANDS + 1)
        self._bins = np.clip((edges / (SAMPLE_RATE / 2) * (KEEP // 2)).astype(int), 1, KEEP // 2)
        self._window = np.hanning(KEEP).astype(np.float32)

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        self.domain.flow.stream(self.id, "out", chunk, meta)
        if not self.watched:
            return
        new = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
        self._samples = np.concatenate([self._samples, new])[-KEEP:]
        now = time.monotonic()
        if now < self._next:
            return
        self._next = now + 1 / max(1, min(30, float(self.settings.get("fps") or 15)))
        self.show(self._frame(len(new)))

    def _frame(self, fresh: int) -> dict[str, Any]:
        mode = self.settings.get("mode") or "spectrum"
        x = self._samples
        if mode == "levels":
            last = x[-max(fresh, 160):]
            rms = float(np.sqrt(np.mean(last ** 2)))
            return {"mode": mode, "db": round(20 * np.log10(rms + 1e-9), 1), "peak": round(20 * np.log10(float(np.max(np.abs(last))) + 1e-9), 1)}
        if mode == "waveform":
            parts = x.reshape(POINTS, -1)
            return {"mode": mode, "min": np.round(parts.min(axis=1), 3).tolist(), "max": np.round(parts.max(axis=1), 3).tolist()}
        mags = np.abs(np.fft.rfft(x * self._window)) / (KEEP / 4)
        bands = [float(mags[a:max(b, a + 1)].max()) for a, b in zip(self._bins[:-1], self._bins[1:])]
        return {"mode": mode, "db": [round(20 * np.log10(v + 1e-9), 1) for v in bands]}
