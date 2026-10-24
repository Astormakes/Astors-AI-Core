"""Level: the loudness of the audio passing through it, as a small graph moving right to left inside the card (the
size of any card), and the audio goes on unchanged."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from custom_components.aac.api import Card, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

FPS = 12  # points per second on the graph


@register
class Level(Card):
    """Shows loudness."""

    type = "level"
    title = "Level"
    category = "Audio"
    help = ("Shows how loud the audio passing through it is: a small graph moving right to left, inside the card. The "
            "audio goes on unchanged, so it can sit anywhere in an audio chain.")
    inputs = {"in": "audio"}
    outputs = {"out": "audio"}
    ports_help = {"in": "live audio", "out": "the same audio, unchanged"}
    view = {"inline": True}  # in its port row, between the port names: it keeps the size of any card

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._next, self._peak = 0.0, 0.0

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        self.domain.flow.stream(self.id, "out", chunk, meta)
        if not self.watched:
            return
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
        self._peak = max(self._peak, float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0)
        now = time.monotonic()
        if now >= self._next:  # the loudest bit since the last point
            self._next = now + 1 / FPS
            self.show(round(20 * np.log10(self._peak + 1e-9), 1))
            self._peak = 0.0
