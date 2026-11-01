"""The Speaker card (docs/v2 page 2): Plays audio clips (a spoken answer, a sound) or a live audio stream: at the
satellite a clip comes from, or at a satellite or Home Assistant media player you pick; a clip without a satellite (a
typed turn) where its setting says, or not at all (the log says so). A stop clip, or Stop, cuts it off. The volume is
on the Audio page."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import SAMPLE_RATE, Card, pcm_to_wav, register

VERSION = "3.0.0"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

STREAM_PIECE = 2  # seconds of a live stream the Speaker plays at a time


@register
class Speaker(Card):
    """Plays sound."""

    type = "speaker"
    title = "Speaker"
    category = "Audio"
    help = ("Plays audio clips (a spoken answer, a sound) or a live audio stream: at the satellite a clip comes from, or "
            "at a satellite or Home Assistant media player you pick; a clip without a satellite (a typed turn) where its "
            "setting says. A stop clip, or Stop, cuts it off. The volume is on the Audio page.")
    inputs = {"in": "audio|clip"}
    outputs: dict[str, str] = {}
    ports_help = {"in": "clips or a live stream (half green, half purple: it takes both)"}
    settings_schema = {"source": {"type": "player", "title": "Plays at", "default": "",
                                  "help": "Empty: the satellite the clip came from. Or a satellite or any media player."},
                       "without": {"type": "player", "title": "A clip without a satellite plays at", "default": "",
                                   "help": "A clip that came from no satellite (an answer to something typed). Empty: it is "
                                           "not played (the log says so)."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._played_at: set[str] = set()  # where it played (a stop cuts them)
        self._buffers: dict[str, bytearray] = {}  # a live stream, gathered into short clips per satellite

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if clip.get("stop"):
            await self.stop()
            return
        where = self.settings.get("source") or clip.get("source") or self.settings.get("without")
        if not clip.get("pcm"):
            return
        if not where:
            self.log(f"Not played (a clip without a satellite; set where such clips play): {clip.get('text', '')[:80]}", what="not played")
            return
        await self._play(where, pcm_to_wav(clip["pcm"], int(clip.get("rate") or SAMPLE_RATE)), clip.get("text", ""), queue=True)

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """A live stream: played in short pieces (satellites take whole clips)."""
        where = self.settings.get("source") or meta.get("source")
        if not where:
            return
        buf = self._buffers.setdefault(where, bytearray())
        buf.extend(chunk)
        if len(buf) >= SAMPLE_RATE * 2 * STREAM_PIECE:
            wav, buf[:] = pcm_to_wav(bytes(buf)), b""
            self.hass.async_create_background_task(self._play(where, wav, ""), f"aac {self.id} stream")

    async def _play(self, where: str, wav: bytes, text: str, queue: bool = False) -> None:
        self._played_at.add(where)
        if not where.startswith("media_player."):
            await self.core.audio.play(where, wav, text, queue=queue)  # clips one after another, a stream at once
            return
        try:
            await self.hass.services.async_call("media_player", "play_media", {
                "entity_id": where, "media_content_id": self.core.audio.clip_url(wav), "media_content_type": "music",
                "announce": True}, blocking=True)
        except Exception as err:  # noqa: BLE001 - a speaker that fails must not break the chain
            self.log(f"Playing on {where} failed: {err}", "warning", what="play")

    async def stop(self) -> None:
        for where in list(self._played_at):
            if where.startswith("media_player."):
                if (state := self.hass.states.get(where)) and state.state == "playing":
                    await self.hass.services.async_call("media_player", "media_stop", {"entity_id": where}, blocking=True)
            else:
                await self.core.audio.stop(where)
        self._played_at.clear()
        self._buffers.clear()

