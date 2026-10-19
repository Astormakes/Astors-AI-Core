"""AAC's own audio pipeline: segments, transcript, answers by voice."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.aac.audio_hub import EnergyVad, Segmenter
from custom_components.aac.modules import async_set_access

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
    await async_set_access(mock_entry, "wakeword", "assistant", {"enabled": True})
    payloads = _queue(aioclient_mock, _text('{"to_assistant": true}'), _text("It is five o'clock."),
                      _text('{"to_assistant": false}'))
    heard = iter(["hello there", "Computer, what time is it?", "on the computer screen they asked"])
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
        hub.sources["pc"].last_answer = None  # no follow-up window: only the name counts
        await ws.send_bytes(speech)  # a video says the name: the attention check says it is not for AAC
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert [(line["text"], line["addressed"]) for line in hub.transcript] == [
        ("hello there", False), ("Computer, what time is it?", True), ("on the computer screen they asked", False)]
    assert hub.transcript[-1]["gate"] == "not meant for AAC"
    assert len(payloads) == 3  # attention check, the answer, attention check (no second answer)
    assert "hello there" in payloads[0]["messages"][0]["content"]  # the check sees what came before
    assert "by voice" in payloads[1]["messages"][0]["content"]
    asked = payloads[1]["messages"][-1]["content"]  # look back: what was said just before the name goes with it
    assert "hello there" in asked and asked.endswith("Now to you: Computer, what time is it?")
    assert not any(t["function"]["name"].startswith("aac-setup") for t in payloads[1]["tools"])  # voice is anonymous
    chat = mock_entry.runtime_data.chats.get("voice_pc")
    assert [m["text"] for m in chat["messages"]] == ["Computer, what time is it?", "It is five o'clock."]
    assert hub.sources == {}
    assert mock_entry.runtime_data.store.audio["known"] == [{"id": "pc", "name": "PC"}]


async def test_mentions(hass: HomeAssistant, mock_entry, configured):
    """A name as a whole word, case and punctuation ignored."""
    await setup_aac(hass, mock_entry)
    hub = mock_entry.runtime_data.audio
    assert hub.mentions("Hey, Computer! Lights off.")
    assert not hub.mentions("computers are fun")
    await mock_entry.runtime_data.store.async_update_audio({"names": ["GLaDOS"]})
    assert hub.mentions("glados, are you there?") and not hub.mentions("Computer?")


async def test_only_hearing_without_a_module(hass: HomeAssistant, hass_client, aioclient_mock, mock_entry, configured):
    """The Wake word module is off by default: AAC hears and transcribes, but never answers."""
    await setup_aac(hass, mock_entry)
    payloads = _queue(aioclient_mock, _text("should not be asked"))
    hub = mock_entry.runtime_data.audio
    assert hub.overview()["hearing"] == []
    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(return_value={"text": "Computer, what time is it?"})),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 0.8), (SILENCE, 1)))
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [(x["text"], x["addressed"]) for x in hub.transcript] == [("Computer, what time is it?", False)]
    assert payloads == []

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


async def test_satellite_app_access(hass: HomeAssistant, hass_ws_client, mock_entry, configured, tmp_path):
    """AAC makes a local-only user + long-lived token for its HA app and writes them to a file; once."""
    import json

    with patch("custom_components.aac.satellite_app.config_path", return_value=tmp_path / "aac" / "satellite.json"):
        await setup_aac(hass, mock_entry)
        ws = await hass_ws_client(hass)
        await ws.send_json_auto_id({"type": "aac/satellite/app"})
        assert (await ws.receive_json())["result"]["ready"]
        data = json.loads((tmp_path / "aac" / "satellite.json").read_text())
        assert data["url"].startswith("http://homeassistant:") and data["token"]
        user = await hass.auth.async_get_user(mock_entry.runtime_data.store.data["satellite_app"]["user_id"])
        assert user.name == "AAC satellite" and user.local_only and not user.is_admin
        await ws.send_json_auto_id({"type": "aac/satellite/app"})
        await ws.receive_json()
        assert json.loads((tmp_path / "aac" / "satellite.json").read_text())["token"] == data["token"]  # kept


def test_volume_scales_the_voice():
    """AAC's voice at 50 %: the samples are halved; anything that is not a 16-bit WAV stays as it is."""
    import io
    import struct
    import wave

    from custom_components.aac.audio_hub import scale_wav

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(struct.pack("<3h", 1000, -2000, 32767))
    with wave.open(io.BytesIO(scale_wav(buf.getvalue(), 0.5))) as r:
        assert struct.unpack("<3h", r.readframes(3)) == (500, -1000, 16383)
    assert scale_wav(b"RIFFwave", 0.5) == b"RIFFwave"
    streamed = bytearray(buf.getvalue())  # Piper streams with "unknown length" in the header
    streamed[4:8] = streamed[40:44] = b"\xff\xff\xff\xff"
    with wave.open(io.BytesIO(scale_wav(bytes(streamed), 0.5))) as r:
        assert struct.unpack("<3h", r.readframes(3)) == (500, -1000, 16383)


async def test_stages_fill_the_buffer(hass: HomeAssistant, hass_client, mock_entry, configured):
    """Segment stages add to a speech line in order; a window stage adds sound lines of its own;
    without the speech to text module a segment is still a (textless) speech line."""
    from custom_components.aac.capabilities import Stage

    await setup_aac(hass, mock_entry)
    hub, caps = mock_entry.runtime_data.audio, mock_entry.runtime_data.capabilities
    seen: list[str] = []

    async def mood(source, pcm, line):
        seen.append(line["text"])  # runs after speech to text (order)
        return {"mood": "calm"}

    async def sounds(source, pcm):
        return [{"text": "[beeping]", "labels": [["Beep", 0.8]]}]

    caps.add_stage(Stage(id="mood", title="Mood", kind="segment", run=mood, order=30))
    caps.add_stage(Stage(id="sounds", title="Sounds", kind="window", run=sounds, window=1.0))
    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(return_value={"text": "hello there", "engine": "x"})),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 0.8), (SILENCE, 1)))
        for _ in range(200):  # the socket hands the audio on in its own time
            if any(x["kind"] == "speech" for x in hub.transcript):
                break
            await asyncio.sleep(0.01)
        await async_set_access(mock_entry, "stt", "assistant", {"enabled": False})
        await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 0.8), (SILENCE, 1)))
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    speech = [x for x in hub.transcript if x["kind"] == "speech"]
    assert [(x["text"], x.get("mood")) for x in speech] == [("hello there", "calm"), ("", "calm")]
    assert seen == ["hello there", ""] and speech[0]["seconds"] > 0.5 and speech[0]["loudness_db"] < 0
    assert any(x["kind"] == "sound" and x["text"] == "[beeping]" for x in hub.transcript)
