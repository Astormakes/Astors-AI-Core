"""Each AAC satellite as a Home Assistant media player: HA (automations, TTS, the media browser) plays on it like on
any speaker, sets the volume, stops it. AAC's Voice out speaks through media players too."""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, LOGGER, SIGNAL


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """A media player per satellite AAC knows (new ones when they first connect)."""
    core = hass.data[DOMAIN]
    made: set[str] = set()

    @callback
    def add_new(event: dict[str, Any] | None = None) -> None:
        if event is not None and event.get("what") != "audio":
            return
        if event and (gone := event.get("forgot")):  # forgotten: its media player is removed
            made.discard(gone)
            registry = er.async_get(hass)
            if entity_id := registry.async_get_entity_id("media_player", DOMAIN, f"{entry.entry_id}_satellite_{gone}"):
                registry.async_remove(entity_id)
            return
        new = [k for k in core.audio.settings.get("known", []) if k["id"] not in made]
        made.update(k["id"] for k in new)
        if new:
            async_add_entities([SatellitePlayer(entry, core, k["id"], k["name"]) for k in new])

    add_new()
    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL, add_new))


async def to_wav(data: bytes) -> bytes | None:
    """Any audio as a 16-bit WAV (ffmpeg; a WAV stays as it is)."""
    if data[:4] == b"RIFF":
        return data
    if not (ffmpeg := shutil.which("ffmpeg")):
        LOGGER.warning("AAC: cannot play this audio on a satellite (no ffmpeg to turn it into WAV)")
        return None
    proc = await asyncio.create_subprocess_exec(ffmpeg, "-loglevel", "error", "-i", "pipe:0", "-f", "wav", "-acodec", "pcm_s16le",
                                                "-ac", "1", "-ar", "22050", "pipe:1", stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate(data)
    return out if proc.returncode == 0 and out else None


class SatellitePlayer(MediaPlayerEntity):
    """An AAC satellite's speaker."""

    _attr_should_poll = False
    _attr_supported_features = (MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.STOP
                                | MediaPlayerEntityFeature.VOLUME_SET | MediaPlayerEntityFeature.BROWSE_MEDIA
                                | MediaPlayerEntityFeature.MEDIA_ANNOUNCE)

    def __init__(self, entry, core, source_id: str, name: str) -> None:
        """Initialize."""
        self.core, self.source_id = core, source_id
        self._attr_unique_id = f"{entry.entry_id}_satellite_{source_id}"
        self._attr_name = f"AAC satellite {name}"

    async def async_added_to_hass(self) -> None:
        """Follow the satellite (connected, playing, volume)."""
        @callback
        def changed(event: dict[str, Any]) -> None:
            if event.get("what") == "audio":
                self.async_write_ha_state()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL, changed))

    @property
    def available(self) -> bool:
        """Connected."""
        return self.source_id in self.core.audio.sources

    @property
    def state(self) -> MediaPlayerState:
        """Playing or idle."""
        source = self.core.audio.sources.get(self.source_id)
        return MediaPlayerState.PLAYING if source and source.playing else MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float:
        """AAC's volume (one for every satellite, the Audio page's)."""
        return int(self.core.audio.settings.get("volume", 40)) / 100

    async def async_set_volume_level(self, volume: float) -> None:
        """Set AAC's volume."""
        self.core.audio.settings["volume"] = round(volume * 100)
        self.core.save()
        self.core.notify("audio")

    async def async_media_stop(self) -> None:
        """Stop playing."""
        await self.core.audio.stop(self.source_id)

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Play a media id (a TTS message, a media source, a URL) on the satellite."""
        if media_source.is_media_source_id(media_id):
            media_id = (await media_source.async_resolve_media(self.hass, media_id, self.entity_id)).url
        url = async_process_play_media_url(self.hass, media_id)
        async with async_get_clientsession(self.hass).get(url) as resp:
            resp.raise_for_status()
            data = await resp.read()
        if wav := await to_wav(data):
            await self.core.audio.play(self.source_id, wav, kwargs.get("extra", {}).get("title", ""))

    async def async_browse_media(self, media_content_type: str | None = None, media_content_id: str | None = None) -> BrowseMedia:
        """HA's media sources (audio)."""
        return await media_source.async_browse_media(self.hass, media_content_id,
                                                     content_filter=lambda item: item.media_content_type.startswith("audio/"))
