"""Log: writes down whatever reaches its one input, of any kind: a context (its newest entry), a clip, audio starting
and stopping, and on a dotted wire what is new in what is provided there. Its page in the sidebar shows the log."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, register

VERSION = "1.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

READ_EVERY = 2  # seconds between looks at what dotted wires provide
QUIET = 1.5  # seconds without audio from a satellite: it stopped


@register
class Log(Card):
    """Writes down what reaches it."""

    type = "log"
    title = "Log"
    category = "Data"
    help = ("Writes down whatever reaches its input, of any kind: a context (its newest entry and where it came from), a "
            "clip (length, satellite), audio starting and stopping, and on a dotted wire what is new in what is "
            "provided there. Its page in the sidebar shows the log, newest first.")
    inputs = {"in": "any"}
    outputs: dict[str, str] = {}
    ports_help = {"in": "anything: contexts, clips, audio, a dotted wire from what a card provides"}
    settings_schema = {"keep": {"type": "number", "title": "Entries kept", "default": 300, "min": 10}}
    keeps_data = True
    ui = True

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._task: asyncio.Task | None = None
        self._seen: dict[str, set[str]] = {}  # dotted wire → entry ids already written down
        self._audio: dict[str, list[float]] = {}  # satellite → [started, last chunk]

    def default_data(self) -> dict[str, Any]:
        return {"log": []}

    async def start(self) -> None:
        self._task = self.hass.async_create_background_task(self._watch(), f"aac {self.id} watch")

    async def unload(self) -> None:
        if self._task:
            self._task.cancel()

    def _write(self, what: str, came_from: str, text: str) -> None:
        log = self.data.setdefault("log", [])
        log.append({"time": dt_util.utcnow().isoformat(), "what": what, "from": came_from, "text": text[:600]})
        del log[:-max(10, int(self.settings.get("keep") or 300))]
        self.hass.async_create_task(self.save())
        self.core.notify("card", domain=self.domain.id, card=self.id)

    @staticmethod
    def _entry(e: dict[str, Any]) -> str:
        tags = " ".join(f"#{t}" for t in e.get("tags") or [])
        body = e.get("text") or (f"{e['data'].get('function')}({e['data'].get('args') or {}})" if e["data"].get("function") else "")
        return f"{e['kind']} {tags}: {body}".strip()

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        came_from = context.way[-2] if len(context.way) > 1 else context.origin
        newest = context.entries[-1] if context.entries else None
        what = "snapshot" if context.progress else "context"
        self._write(what, self._name(came_from), (self._entry(newest) if newest else "(empty)") + f"  [{len(context.entries)} entries]")
        return []

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        return await self.handle(context, port, None)

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if clip.get("stop"):
            self._write("clip", clip.get("name") or clip.get("source") or "", "stop")
            return
        part = f", phrase {clip['part']}" if clip.get("part") else ""
        said = f": {clip['text']}" if clip.get("text") else ""
        self._write("clip", clip.get("name") or clip.get("source") or "", f"{clip.get('seconds')} s{part}{said}")

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        source, now = meta.get("name") or meta.get("source") or "audio", time.monotonic()
        if source not in self._audio:
            self._audio[source] = [now, now]
            self._write("audio", source, "started")
        else:
            self._audio[source][1] = now

    async def _watch(self) -> None:
        """Audio that stopped, and what is new on dotted wires (what a card provides)."""
        while True:
            await asyncio.sleep(READ_EVERY)
            now = time.monotonic()
            for source, (began, last) in list(self._audio.items()):
                if now - last > QUIET:
                    self._audio.pop(source)
                    self._write("audio", source, f"stopped ({round(last - began)} s)")
            for card, port in self.domain.flow.wired_into(self.id, "in"):
                if card.ports()[1].get(port) != "read":
                    continue
                held = card.held(port)
                key, ids = f"{card.id}.{port}", [e["id"] for e in (held.entries if held else [])]
                if key not in self._seen:  # the first look: what is there now is the start
                    self._seen[key] = set(ids)
                    continue
                new = [e for e in held.entries if e["id"] not in self._seen[key]] if held else []
                self._seen[key] = set(ids)
                for e in new[-5:]:
                    self._write("read", f"{card.name}.{port}", self._entry(e))

    def _name(self, card_id: str) -> str:
        card = self.domain.cards.get(card_id)
        return card.name if card else card_id

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        if action == "clear":
            self.data["log"] = []
            await self.save()
            self.core.notify("card", domain=self.domain.id, card=self.id)
            return {"cleared": True}
        return await super().ui_call(action, args)
