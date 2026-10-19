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
        patch.object(hub, "async_stt", AsyncMock(side_effect=lambda pcm: {"text": next(heard)})),
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


async def test_stt_through_a_model_server(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Speech to text with a model from Models: WAV upload, language detected by the server."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.post(
        "http://llm.test/v1/audio/transcriptions",
        json={"text": " Mach das Licht aus. ", "language": "german",
              "segments": [{"avg_logprob": -0.1}], "words": [{"word": "Mach", "start": 0.1, "end": 0.3}]},
    )
    store = mock_entry.runtime_data.store
    await store.async_set_pool("stt", ["auto|w:m_test0001|whisper-large-v3-turbo"])
    heard = await mock_entry.runtime_data.audio.async_stt(SILENCE * 100)
    assert heard["text"] == "Mach das Licht aus." and heard["language"] == "german"
    assert 0.85 < heard["confidence"] < 0.95 and heard["words"][0]["word"] == "Mach"
    body = aioclient_mock.mock_calls[-1][2]
    fields = {f[0]["name"]: f[2] for f in body._fields}
    assert fields["model"] == "whisper-large-v3-turbo" and fields["response_format"] == "verbose_json"
    assert "language" not in fields  # detected per sentence
    assert fields["file"][:4] == b"RIFF"


async def test_stt_falls_back_to_plain_json(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """Servers without verbose_json still work (text only)."""
    await setup_aac(hass, mock_entry)
    calls = []

    async def side_effect(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        calls.append(data)
        if len(calls) == 1:
            return AiohttpClientMockResponse(method, url, status=400, text="unsupported response_format")
        return AiohttpClientMockResponse(method, url, json={"text": "lights off"})

    aioclient_mock.post("http://llm.test/v1/audio/transcriptions", side_effect=side_effect)
    await mock_entry.runtime_data.store.async_set_pool("stt", ["m_test0001"])
    heard = await mock_entry.runtime_data.audio.async_stt(SILENCE * 100)
    assert heard == {"text": "lights off", "language": None, "confidence": None, "words": [], "engine": "m_test0001"}


async def test_server_without_model_list(hass: HomeAssistant, hass_ws_client, aioclient_mock, mock_entry, configured):
    """A speech server without /models (whisper.cpp) is reachable and its model is typed by hand."""
    await setup_aac(hass, mock_entry)
    aioclient_mock.get("http://stt.test/v1/models", status=404)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/server/models", "base_url": "http://stt.test/v1"})
    assert (await ws.receive_json())["result"] == {"models": [], "unlisted": True}
    await ws.send_json_auto_id({"type": "aac/model/save", "name": "Whisper", "base_url": "http://stt.test/v1",
                                "model": "large-v3-turbo"})
    assert (await ws.receive_json())["success"]
    aioclient_mock.get("http://llm.test/v1/models", json={"data": [{"id": "test-model"}]})
    await ws.send_json_auto_id({"type": "aac/models"})
    manual = {m["name"]: m for m in (await ws.receive_json())["result"]["manual"]}
    assert manual["Whisper"]["online"]


async def test_stt_falls_back_when_machine_is_off(hass: HomeAssistant, aioclient_mock, mock_entry, configured):
    """An unreachable speech model (PC off) → HA's engine meanwhile, retried after a minute."""
    import aiohttp

    from homeassistant.components import stt

    await setup_aac(hass, mock_entry)
    aioclient_mock.post("http://llm.test/v1/audio/transcriptions", exc=aiohttp.ClientConnectionError("off"))
    hub = mock_entry.runtime_data.audio
    await mock_entry.runtime_data.store.async_set_pool("stt", ["m_test0001"])

    class FakeEntity:
        entity_id = "stt.local"
        supported_languages = ["en"]

        async def internal_async_process_audio_stream(self, metadata, stream):
            return stt.SpeechResult("lights off", stt.SpeechResultState.SUCCESS)

    with (
        patch.object(hub, "ha_engine", return_value="stt.local"),
        patch("custom_components.aac.audio_hub.stt.async_get_speech_to_text_entity", return_value=FakeEntity()),
    ):
        heard = await hub.async_stt(SILENCE * 100)
        assert heard["text"] == "lights off" and heard["engine"] == "stt.local"
        stt_role = mock_entry.runtime_data.broker.status()["roles"]["stt"]
        assert stt_role["items"][0]["state"] == "down" and stt_role["fallback"]
        calls = aioclient_mock.call_count
        await hub.async_stt(SILENCE * 100)
        assert aioclient_mock.call_count == calls  # no retry within the minute
