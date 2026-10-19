"""AAC's own audio pipeline: segments, transcript, answers by voice."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.aac.audio_hub import EnergyVad, Segmenter

from .conftest import setup_aac
from .test_planning import _queue, _text

SILENCE = array("h", [0] * 160).tobytes()  # 10 ms
LOUD = array("h", [3000, -3000] * 80).tobytes()


def _audio(*parts: tuple[bytes, float]) -> bytes:
    return b"".join(frame * int(seconds * 100) for frame, seconds in parts)


def test_segmenter_cuts_speech():
    """Speech between silences becomes one segment (with pre-roll); blips are dropped."""
    seg = Segmenter(EnergyVad())
    assert seg.feed(_audio((SILENCE, 0.5), (LOUD, 0.1), (SILENCE, 1))) == []
    # odd chunk sizes must not matter
    data = _audio((SILENCE, 0.5), (LOUD, 1.0), (SILENCE, 1))
    found = [s for i in range(0, len(data), 999) for s in seg.feed(data[i : i + 999])]
    assert len(found) == 1
    assert 1.5 <= len(found[0]) / 32000 <= 2.4  # pre-roll + speech + closing silence


async def test_satellite_round_trip(hass: HomeAssistant, hass_client, aioclient_mock, mock_entry, configured):
    """A satellite streams; a line with AAC's name is answered by voice, others only transcribed."""
    await setup_aac(hass, mock_entry)
    payloads = _queue(aioclient_mock, _text("It is five o'clock."))
    heard = iter(["hello there", "Computer, what time is it?"])
    hub = mock_entry.runtime_data.audio
    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(side_effect=lambda pcm: next(heard))),
        patch.object(hub, "async_tts", AsyncMock(return_value=b"RIFFwave")),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "area": "Office", "rate": 16000})
        assert (await ws.receive_json())["type"] == "welcome"
        assert hub.sources["pc"].name == "PC"
        speech = _audio((SILENCE, 0.3), (LOUD, 0.8), (SILENCE, 1))
        await ws.send_bytes(speech)
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.send_bytes(speech)
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play["type"] == "play" and play["text"] == "It is five o'clock."
        assert await ws.receive_bytes() == b"RIFFwave"
        assert hub.sources["pc"].playing
        await ws.send_json({"type": "played"})
        for _ in range(100):
            if not hub.sources["pc"].playing:
                break
            await asyncio.sleep(0.01)
        assert not hub.sources["pc"].playing and hub.sources["pc"].last_answer
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [(line["text"], line["addressed"]) for line in hub.transcript] == [
        ("hello there", False), ("Computer, what time is it?", True)]
    assert len(payloads) == 1  # only the addressed line reached the master
    assert "by voice" in payloads[0]["messages"][0]["content"]
    assert not any(t["function"]["name"].startswith("aac-setup") for t in payloads[0]["tools"])  # voice is anonymous
    chat = mock_entry.runtime_data.chats.get("voice_pc")
    assert [m["text"] for m in chat["messages"]] == ["Computer, what time is it?", "It is five o'clock."]
    assert hub.sources == {}
    assert mock_entry.runtime_data.store.audio["known"] == [{"id": "pc", "name": "PC"}]


async def test_addressed(hass: HomeAssistant, mock_entry, configured):
    """A name as a whole word, or a follow-up right after AAC spoke."""
    await setup_aac(hass, mock_entry)
    hub = mock_entry.runtime_data.audio
    source = hub.connect("pc", "PC", None, AsyncMock())
    assert hub.addressed(source, "Hey, Computer! Lights off.")
    assert not hub.addressed(source, "computers are fun")
    hub.played(source)
    assert hub.addressed(source, "and the kitchen too")
    await mock_entry.runtime_data.store.async_update_audio({"follow_up": 0, "names": ["GLaDOS"]})
    assert not hub.addressed(source, "and the kitchen too")
    assert hub.addressed(source, "glados, are you there?")
    await hass.async_block_till_done(wait_background_tasks=True)
