"""Level: the loudness of the audio passing through it, as a graph moving right to left inside the card, with a line
where clips begin and end (whole clips, phrases, the end of a sentence). Audio and clips go on unchanged."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from custom_components.aac.api import Card, register

VERSION = "1.3.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


@register
class Level(Card):
    """Shows loudness and clips."""

    type = "level"
    title = "Level"
    category = "Audio"
    help = ("Shows how loud the audio passing through it is, as a graph moving right to left inside the card, and a line "
            "where clips begin and end: whole clips, phrases (a phrase pause), and the end of a sentence (an Audio "
            "clipper's 'ends'). Wire the audio and the clips into it to see where cuts are made. Audio and clips go on "
            "unchanged.")
    inputs = {"in": "audio|clip", "ends": "clip"}
    outputs = {"out": "audio", "clips": "clip", "ends": "clip"}
    ports_help = {"in": "live audio and/or clips (e.g. a Microphone and an Audio clipper's out)",
                  "ends": "the signals that a sentence is done (an Audio clipper's ends): a red line",
                  "out": "the same audio, unchanged", "clips": "the same clips, unchanged"}
    settings_schema = {
        "height": {"type": "number", "title": "Height (px)", "default": 0,
                   "help": "0: a small graph in its port row (the size of any card). E.g. 60 or 120: a bigger graph under it."},
        "seconds": {"type": "number", "title": "Seconds shown", "default": 8, "help": "How much time the graph shows: its speed."},
        "fps": {"type": "number", "title": "Points per second", "default": 20, "help": "More: finer (cuts are easier to see), 5–30."},
    }
    view = {"inline": True}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        height = int(self.settings.get("height") or 0)
        self.view = {"height": max(30, min(400, height))} if height else {"inline": True}  # its own size
        self._next, self._peak = 0.0, 0.0

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        self.domain.flow.stream(self.id, "out", chunk, meta)
        if not self.watched:
            return
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
        self._peak = max(self._peak, float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0)
        now = time.monotonic()
        if now >= self._next:  # the loudest bit since the last point
            self._next = now + 1 / max(5, min(30, float(self.settings.get("fps") or 20)))
            self.show({"db": round(20 * np.log10(self._peak + 1e-9), 1)})
            self._peak = 0.0

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        self.domain.flow.clip(self.id, "ends" if port == "ends" else "clips", clip)  # on unchanged, each its own way
        if not self.watched or clip.get("stop"):
            return
        if port == "ends" or clip.get("pcm") is None:  # the sentence is done
            self.show({"mark": "sentence", "seconds": 0})
        else:  # it arrives at its end and knows how long it is
            self.show({"mark": "phrase" if clip.get("utterance") else "clip", "seconds": float(clip.get("seconds") or 0)})
