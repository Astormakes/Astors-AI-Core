"""Level: the loudness of the audio passing through it, as a graph moving right to left inside the card, with a line
where an Audio clipper cuts (speech starts, a phrase is cut, the sentence is done). Audio and cuts go on unchanged."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from custom_components.aac.api import Card, Context, register

VERSION = "2.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)


@register
class Level(Card):
    """Shows loudness and clips."""

    type = "level"
    title = "Level"
    category = "Audio"
    help = ("Shows how loud the audio passing through it is, as a graph moving right to left inside the card, and a line "
            "where an Audio clipper cuts: green where speech starts, purple where a phrase is cut, red where the "
            "sentence is done. Wire the audio into 'in' and the clipper's 'cuts' into 'cuts'. Both go on unchanged.")
    inputs = {"in": "audio", "cuts": "text"}
    outputs = {"out": "audio", "cuts": "text"}
    ports_help = {"in": "live audio (e.g. a Microphone): the graph", "cuts": "an Audio clipper's cuts: the lines",
                  "out": "the same audio, unchanged"}
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
        self.stream_out("out", chunk, meta)
        if not self.watched:
            return
        x = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768
        self._peak = max(self._peak, float(np.sqrt(np.mean(x ** 2))) if len(x) else 0.0)
        now = time.monotonic()
        if now >= self._next:  # the loudest bit since the last point
            self._next = now + 1 / max(5, min(30, float(self.settings.get("fps") or 20)))
            self.show({"db": round(20 * np.log10(self._peak + 1e-9), 1)})
            self._peak = 0.0

    async def handle(self, context: Context, port: str) -> None:
        """A cut (an entry with data cut: start / phrase / end): a line; the context goes on at 'cuts'."""
        cut = next((e["data"] for e in reversed(context.conversation) if e["data"].get("cut")), None)
        if self.watched and cut and cut["cut"] in ("start", "phrase", "end"):  # a start knows how long ago speech began
            self.show({"mark": cut["cut"], "ago": float(cut.get("ago") or 0)})
        await self.send(context, "cuts")
