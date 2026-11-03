"""The Timer card (docs/v2 page 2): Holds the context for some seconds, then sends it on (e.g. to wait for a reply); a
stop drops what it holds. It holds on its own: a slow loop through it (a heartbeat) can run for ever."""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "2.0.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class Timer(Card):
    """Holds a context for a while."""

    type = "timer"
    title = "Timer"
    category = "Logic"
    help = "Holds the context for some seconds, then sends it on (e.g. to wait for a reply); a stop drops what it holds."
    settings_schema = {"seconds": {"type": "number", "title": "Seconds", "default": 5}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._waits: set[asyncio.Task] = set()

    async def handle(self, context: Context, port: str) -> None:
        """It holds the context on its own (what sent it goes on meanwhile), then sends it on."""
        task = self.task(self._later(context), background=True)
        self._waits.add(task)
        task.add_done_callback(self._waits.discard)

    async def _later(self, context: Context) -> None:
        await asyncio.sleep(max(0.0, float(self.settings.get("seconds") or 0)))
        await self.send(context, "out")

    async def stop(self) -> None:
        for task in list(self._waits):  # what it held is dropped
            task.cancel()
