"""WebSocket endpoint where audio sources (AAC satellites) stream to AAC.

Protocol (JSON text messages + binary audio):
  satellite → AAC: {"type": "hello", "id": "pc-astor", "name": "PC", "area": "Office", "rate": 16000}
  AAC → satellite: {"type": "welcome", "source_id": ...}
  satellite → AAC: binary = raw PCM, 16-bit little endian, 16 kHz, mono
  AAC → satellite: {"type": "play", "format": "wav", "bytes": n, "text": ...} then n bytes (binary)
  satellite → AAC: {"type": "played"} when playback finished (the mic is ignored until then)
Auth: a Home Assistant access token (Authorization: Bearer ...).
"""

from __future__ import annotations

import asyncio
import json
import re

from aiohttp import WSMsgType, web

from homeassistant.components.http import KEY_HASS, HomeAssistantView

from .audio_hub import SAMPLE_RATE
from .const import DOMAIN, LOGGER

URL = "/api/aac/audio"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "source"


class AACAudioView(HomeAssistantView):
    """One WebSocket per source."""

    url = URL
    name = "api:aac:audio"
    requires_auth = True

    async def get(self, request: web.Request) -> web.StreamResponse:
        """Accept a source and pump its audio into the hub."""
        hass = request.app[KEY_HASS]
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return web.Response(status=503, text="AAC is not loaded")
        hub = entries[0].runtime_data.audio

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
