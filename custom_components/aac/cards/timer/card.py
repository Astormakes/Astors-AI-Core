"""The Timer card (docs/v2 page 2): Holds the context for some seconds, then sends it on (e.g. to wait for a reply); a
stop ends the wait."""

from __future__ import annotations

import asyncio
from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "1.3.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


@register
class Timer(Card):
    """Holds a context for a while."""

    type = "timer"
    title = "Timer"
    category = "Logic"
    help = "Holds the context for some seconds, then sends it on (e.g. to wait for a reply); a stop ends the wait."
    settings_schema = {"seconds": {"type": "number", "title": "Seconds", "default": 5}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._waits: set[asyncio.Task] = set()

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        task = asyncio.current_task()
        self._waits.add(task)
        try:
            await asyncio.sleep(max(0.0, float(self.settings.get("seconds") or 0)))
        except asyncio.CancelledError:
            return []
        finally:
            self._waits.discard(task)
        return None

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        for task in list(self._waits):  # what it held is dropped; the stop goes on
            task.cancel()
        return False

