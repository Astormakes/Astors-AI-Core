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
        patch.object(hub, "async_stt", AsyncMock(side_effect=lambda pcm, prompt=None: {"text": next(heard)})),
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

    assert [(line["text"], line["addressed"]) for line in hub.transcript if line["kind"] != "aac"] == [
        ("hello there", False), ("Computer, what time is it?", True), ("on the computer screen they asked", False)]
    assert hub.transcript[-1]["gate"] == "not meant for AAC"
    assert len(payloads) == 3  # attention check, the answer, attention check (no second answer)
    assert "hello there" in payloads[0]["messages"][0]["content"]  # the check sees what came before
    assert "by voice" in payloads[1]["messages"][0]["content"]
    asked = payloads[1]["messages"][-1]["content"]  # look back: what was said just before the name goes with it
    assert "hello there" in asked and asked.endswith("spoken aloud there): Computer, what time is it?")
    assert not any(t["function"]["name"].startswith("aac-setup") for t in payloads[1]["tools"])  # voice is anonymous
    chat = mock_entry.runtime_data.chats.get("voice_pc")
    assert chat["messages"][0]["via"] == "voice" and chat["messages"][0]["mic"] == "PC"
    assert [m["text"] for m in chat["messages"]] == ["Computer, what time is it?", "It is five o'clock."]
    assert hub.sources == {}
    assert mock_entry.runtime_data.store.audio["known"] == [{"id": "pc", "name": "PC"}]


async def test_mentions(hass: HomeAssistant, mock_entry, configured):
    """A name as a whole word, case and punctuation ignored."""
    await setup_aac(hass, mock_entry)
    hub = mock_entry.runtime_data.audio
    assert hub.mentions("Hey, Computer! Lights off.")
    assert hub.mentions("Komputer, mach das Licht an") and hub.mentions("Hey Kompjuter")  # said in German
    assert not hub.mentions("computers are fun") and not hub.mentions("Kopie davon bitte") and not hub.mentions("Herr Kempten")
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
    assert heard == {"text": "lights off", "language": None, "confidence": None, "words": [], "segments": [],
                     "engine": "m_test0001"}


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


async def test_follow_up_without_the_name(hass: HomeAssistant, hass_client, aioclient_mock, mock_entry, configured):
    """Right after AAC spoke, the next sentence needs no name."""
    await setup_aac(hass, mock_entry)
    await async_set_access(mock_entry, "wakeword", "assistant", {"enabled": True})
    payloads = _queue(aioclient_mock, _text('{"to_assistant": true}'), _text("It is five."),
                      _text('{"to_assistant": true}'), _text("And the kitchen too."))
    heard = iter(["Computer, what time is it?", "and the kitchen lights too"])
    hub = mock_entry.runtime_data.audio
    client = await hass_client()
    speech = _audio((SILENCE, 0.3), (LOUD, 0.8), (SILENCE, 1))
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(side_effect=lambda pcm, prompt=None: {"text": next(heard)})),
        patch.object(hub, "async_tts", AsyncMock(return_value=b"RIFFwave")),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        await ws.send_bytes(speech)
        assert (await asyncio.wait_for(ws.receive_json(), 5))["type"] == "play"
        await ws.receive_bytes()
        await ws.send_json({"type": "played"})
        await asyncio.sleep(0.05)
        await ws.send_bytes(speech)
        for _ in range(100):
            if len(hub.transcript) == 2:
                break
            await asyncio.sleep(0.02)
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play["text"] == "And the kitchen too."
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [x["addressed"] for x in hub.transcript if x["kind"] != "aac"] == [True, True]
    assert [x["text"] for x in hub.transcript if x["kind"] == "aac"] == ["It is five.", "And the kitchen too."]
    assert 'just finished saying: "It is five."' in payloads[2]["messages"][0]["content"]  # the check knows


def test_voice_messages_say_they_were_spoken():
    """Per message: the model sees which lines were spoken (a chat can mix voice and typing)."""
    from custom_components.aac.chats import spoken

    assert spoken({"role": "user", "text": "hi", "via": "voice", "mic": "PC"}) == "[spoken at the microphone 'PC'] hi"
    assert spoken({"role": "aac", "text": "Hello.", "via": "voice"}) == "Hello."  # marked answers got copied
    assert spoken({"role": "aac", "text": "(You said this out loud) Hello.", "via": "voice"}) == "Hello."
    assert spoken({"role": "user", "text": "typed"}) == "typed"


async def test_voice_volume_tool(hass: HomeAssistant, mock_entry, configured):
    """AAC can make itself louder or quieter."""
    from homeassistant.helpers import llm

    await setup_aac(hass, mock_entry)
    api = await llm.async_get_api(hass, mock_entry.runtime_data.domain().api_id("aac_home"),
                                  llm.LLMContext(platform="aac", context=None, language="en", assistant=None, device_id=None))
    tool = next(t for t in api.tools if t.name == "set_voice_volume")
    result = await tool.async_call(hass, llm.ToolInput(tool_name="set_voice_volume", tool_args={"percent": 55}), api.llm_context)
    assert result.data["volume_now"] == 55 == mock_entry.runtime_data.store.audio["volume"]


def test_whisper_sounds_become_sound_lines():
    """*crunch*, (laughs) and [Music] are sounds, not speech; punctuation alone is nothing."""
    from custom_components.aac.modules.stt import SOUND

    assert [SOUND.match(t).group(1) for t in ("*crunch*", "(laughs)", "[Music]", " *door slams* ")] == [
        "crunch", "laughs", "Music", "door slams"]
    assert SOUND.match("Ha ha ha!") is None and SOUND.match("I said (maybe) yes") is None


async def test_sounds_and_voices(hass: HomeAssistant, hass_client, mock_entry, configured):
    """The Sounds module tags spoken lines and writes sound lines; the Voices module gives each spoken line a
    voice, a new one first, the same one again, and a voice the owner named as media is not talked to."""
    from custom_components.aac.modules.voices import Voices

    await setup_aac(hass, mock_entry)
    await async_set_access(mock_entry, "sounds", "assistant", {"enabled": True})
    hub = mock_entry.runtime_data.audio
    prints = iter([[1.0, 0.0], [0.99, 0.141], [0.0, 1.0]])

    async def senses(role, path, pcm):
        if role == "voices":
            return {"embedding": next(prints)}
        return {"labels": [["Speech", 0.8], ["Music", 0.45], ["Laughter", 0.35]]}

    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(return_value={"text": "hello there"})),
        patch.object(hub, "async_senses", side_effect=senses),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        for _ in range(3):
            await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 2.0), (SILENCE, 1)))
            await hass.async_block_till_done(wait_background_tasks=True)
            await asyncio.sleep(0.05)
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    speech = [x for x in hub.transcript if x["kind"] == "speech"]
    assert [x["speaker"]["name"] for x in speech] == ["voice #1", "voice #1", "voice #2"]
    assert [x["speaker"]["new"] for x in speech] == [True, False, True]
    assert speech[0]["sounds"][1] == ["Music", 0.45]
    assert any(x["kind"] == "sound" and x["text"] == "[music, laughter]" for x in hub.transcript)

    voices = Voices.__new__(Voices)  # the match logic alone
    voices.voices, voices.next = [], 1
    a, _, new = voices.match([1.0, 0.0], 0.8)
    b, score, again = voices.match([0.6, 0.8], 0.8)
    assert new and again and a is not b and score < 0.8


def test_sentences_split_a_long_piece():
    """Whisper's sentences become parts without gaps; very short ones join a neighbour."""
    from custom_components.aac.modules.stt import sentences

    segs = [{"text": "Alright, so we're into the next day.", "start": 0.4, "end": 3.0},
            {"text": "Yes.", "start": 3.2, "end": 3.6},
            {"text": "I had to cut a hole in the fabric.", "start": 4.0, "end": 7.5}]
    parts = sentences(segs, 8.3)
    assert [p["text"] for p in parts] == ["Alright, so we're into the next day. Yes.", "I had to cut a hole in the fabric."]
    assert parts[0]["start"] == 0 and parts[-1]["end"] == 8.3 and parts[0]["end"] == parts[1]["start"] == 3.8
    assert sentences(segs[:1], 3.0) is None


async def test_a_split_segment_gets_a_voice_per_sentence(hass: HomeAssistant, hass_client, mock_entry, configured):
    """Speech to text splits a long piece: each sentence is its own line with its own voice; the next piece
    is recognized with the last line as context."""
    await setup_aac(hass, mock_entry)
    mock_entry.runtime_data.store.data["module_settings"].setdefault("assistant", {})["stt"] = {"split": True}
    hub = mock_entry.runtime_data.audio
    prints = iter([[1.0, 0.0], [0.0, 1.0]])
    prompts: list = []

    async def stt(pcm, prompt=None):
        prompts.append(prompt)
        return {"text": "First sentence here. Second one there.", "segments": [
            {"text": "First sentence here.", "start": 0.0, "end": 3.0}, {"text": "Second one there.", "start": 3.0, "end": 6.0}]}

    async def senses(role, path, pcm):
        return {"embedding": next(prints)} if role == "voices" else None

    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", side_effect=stt),
        patch.object(hub, "async_senses", side_effect=senses),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 6.0), (SILENCE, 1)))
        for _ in range(200):
            if len([x for x in hub.transcript if x["kind"] == "speech"]) == 2:
                break
            await asyncio.sleep(0.01)
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    speech = [x for x in hub.transcript if x["kind"] == "speech"]
    assert [x["text"] for x in speech] == ["First sentence here.", "Second one there."]
    assert [x["speaker"]["name"] for x in speech] == ["voice #1", "voice #2"]
    assert speech[0]["time"] < speech[1]["time"] and speech[0]["seconds"] > 1
    assert prompts[0] == "Computer."  # AAC's names, so the recognizer spells them right (no line before yet)


def test_voices_that_sound_the_same_are_merged():
    """A line that fits two unnamed voices joins them; a named voice keeps its name."""
    from custom_components.aac.modules.voices import Voices

    voices = Voices.__new__(Voices)
    voices.voices, voices.next = [], 1
    a, _, _ = voices.match([1.0, 0.0], 0.8)
    b, _, _ = voices.match([0.0, 1.0], 0.8)
    b.update(name="Astor", named=True, kind="person")
    found, _, new = voices.match([0.7071, 0.7071 + 0.0001], 0.7)
    assert not new and found is b and len(voices.voices) == 1 and found["merged"] == ["voice #1"]


def test_unsure_lines_do_not_start_voices():
    """65–80 %: probably the closest voice (it does not learn); short lines never start a new voice."""
    from custom_components.aac.modules.voices import Voices

    voices = Voices.__new__(Voices)
    voices.voices, voices.next = [], 1
    a, _, _ = voices.match([1.0, 0.0], 0.8, 0.65)
    before = list(a["print"])
    b, score, new = voices.match([0.7, 0.714], 0.8, 0.65)
    assert b is a and not new and 0.65 <= score < 0.8 and a["print"] == before
    c, _, _ = voices.match([0.0, 1.0], 0.8, 0.65, may_add=False)
    assert c is None and len(voices.voices) == 1


async def test_split_utterance_is_answered_once(hass: HomeAssistant, hass_client, aioclient_mock, mock_entry, configured):
    """'Computer, are you there? Please say hello.' split into two sentences: one request with both."""
    await setup_aac(hass, mock_entry)
    mock_entry.runtime_data.store.data["module_settings"].setdefault("assistant", {})["stt"] = {"split": True}
    await async_set_access(mock_entry, "wakeword", "assistant", {"enabled": True})
    await async_set_access(mock_entry, "voices", "assistant", {"enabled": False})
    payloads = _queue(aioclient_mock, _text('{"to_assistant": true}'), _text("Hello!"))
    hub = mock_entry.runtime_data.audio

    async def stt(pcm, prompt=None):
        return {"text": "Computer, are you there? Please say hello.", "segments": [
            {"text": "Computer, are you there?", "start": 0.0, "end": 2.0}, {"text": "Please say hello.", "start": 2.0, "end": 4.0}]}

    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", side_effect=stt),
        patch.object(hub, "async_tts", AsyncMock(return_value=b"RIFFwave")),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 4.0), (SILENCE, 1)))
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play["text"] == "Hello!"
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(payloads) == 2  # one attention check, one answer
    assert payloads[1]["messages"][-1]["content"].endswith("Computer, are you there? Please say hello.")
    assert [x["addressed"] for x in hub.transcript if x["kind"] != "aac"] == [True, True]


def test_the_model_hears_which_voice_spoke():
    """The spoken prefix names the voice, so 'that's me' points at the right one."""
    from custom_components.aac.audio_hub import voice_label
    from custom_components.aac.chats import spoken

    assert voice_label({"name": "voice #8", "kind": "unknown", "sure": True}) == "voice #8"
    assert voice_label({"name": "voice #9", "kind": "unknown", "sure": False}) == "probably voice #9"
    assert voice_label({"name": "Astor", "kind": "person"}) == "Astor (person)"
    assert spoken({"role": "user", "text": "hi", "via": "voice", "mic": "PC", "voice": "voice #8"}) == \
        "[spoken at the microphone 'PC' by voice #8] hi"


async def test_owner_manages_voices(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """The panel lists, names and forgets voices (all of them for a fresh start)."""
    await setup_aac(hass, mock_entry)
    voices, _ = hass.data["aac_voices"][mock_entry.entry_id]
    voices.match([1.0, 0.0], 0.8)
    voices.match([0.0, 1.0], 0.8)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/voices"})
    listed = (await ws.receive_json())["result"]["voices"]
    assert [v["name"] for v in listed] == ["voice #1", "voice #2"] and "print" not in listed[0]
    await ws.send_json_auto_id({"type": "aac/voices/change", "action": "name", "voice": "v2", "name": "Astor", "kind": "person"})
    assert (await ws.receive_json())["result"]["voices"][1]["name"] == "Astor"
    await ws.send_json_auto_id({"type": "aac/voices/change", "action": "forget", "voice": "v1"})
    assert [v["name"] for v in (await ws.receive_json())["result"]["voices"]] == ["Astor"]
    await ws.send_json_auto_id({"type": "aac/voices/change", "action": "forget_all"})
    assert (await ws.receive_json())["result"]["voices"] == []


async def test_companion_follows_the_conversation(hass: HomeAssistant, hass_client, aioclient_mock, mock_entry, configured):
    """No name needed: the Companion judges each line with the conversation so far and answers when it fits."""
    await setup_aac(hass, mock_entry)
    await async_set_access(mock_entry, "companion", "assistant", {"enabled": True})
    payloads = _queue(aioclient_mock, _text('{"respond": false, "why": "people talk to each other", "related": []}'),
                      _text('{"respond": true, "why": "a question for the assistant", "related": [{"line": 1, "percent": 10}]}'),
                      _text("It is five."))
    heard = iter(["Anna, how was your day?", "Could you tell me what time it is?"])
    hub = mock_entry.runtime_data.audio
    client = await hass_client()
    speech = _audio((SILENCE, 0.3), (LOUD, 0.8), (SILENCE, 1))
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(side_effect=lambda pcm, prompt=None: {"text": next(heard)})),
        patch.object(hub, "async_tts", AsyncMock(return_value=b"RIFFwave")),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        await ws.send_bytes(speech)
        for _ in range(200):
            if hub.transcript and hub.transcript[-1].get("judged"):
                break
            await asyncio.sleep(0.01)
        await ws.send_bytes(speech)
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play["text"] == "It is five."
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    people = [x for x in hub.transcript if x["kind"] == "speech"]
    assert [(x["addressed"], x["judged"]) for x in people] == [
        (False, "people talk to each other"), (True, "a question for the assistant")]
    second = payloads[1]["messages"][1]["content"]  # the decision sees the conversation so far
    assert "Anna, how was your day?" in second and second.endswith("Could you tell me what time it is?")
    assert any(x["kind"] == "aac" and x["text"] == "It is five." for x in hub.transcript)
    asked = payloads[2]["messages"][-1]["content"]  # the master gets the room with percentages
    assert "(10 %) someone: Anna, how was your day?" in asked and "use only what fits" in asked


async def test_companion_steps_back_for_the_wake_word(hass: HomeAssistant, mock_entry, configured):
    """Both on: only the Wake word answers (never two answers)."""
    await setup_aac(hass, mock_entry)
    await async_set_access(mock_entry, "companion", "assistant", {"enabled": True})
    await async_set_access(mock_entry, "wakeword", "assistant", {"enabled": True})
    hub = mock_entry.runtime_data.audio
    companion = mock_entry.runtime_data.capabilities.hearing["companion"]
    source = hub.connect("pc", "PC", None, AsyncMock())
    with patch.object(hub, "async_answer", AsyncMock()) as answer:
        await companion.heard(source, {"kind": "speech", "text": "what time is it", "time": "2026-10-19T20:00:00+00:00",
                                       "source": "pc"}, [])
    answer.assert_not_called()


def test_only_the_same_voice_goes_along():
    """The master gets what the person talking to AAC said just before, not the video next to them."""
    from custom_components.aac.modules.companion import same_voice

    me, video = {"id": "v5", "name": "Astor"}, {"id": "v18", "name": "voice #18"}
    lines = [{"kind": "speech", "text": "Matthias built a sensor.", "speaker": video, "time": "2026-10-19T21:57:40+00:00"},
             {"kind": "speech", "text": "Hm.", "speaker": me, "time": "2026-10-19T21:57:50+00:00"},
             {"kind": "aac", "text": "Okay.", "time": "2026-10-19T21:57:52+00:00"}]
    line = {"kind": "speech", "text": "That was for you.", "speaker": me, "time": "2026-10-19T21:57:59+00:00"}
    assert same_voice(lines, line) == ["Hm."]
    assert same_voice(lines, {**line, "speaker": None}) == []


async def test_mood_on_spoken_lines(hass: HomeAssistant, hass_client, mock_entry, configured):
    """The Mood module gives spoken lines the mood the model hears; only a clear, not neutral one is passed on."""
    from custom_components.aac.audio_hub import mood_word
    from custom_components.aac.chats import spoken

    await setup_aac(hass, mock_entry)
    await async_set_access(mock_entry, "mood", "assistant", {"enabled": True})
    hub = mock_entry.runtime_data.audio
    moods = iter([[["happy", 0.82], ["neutral", 0.1]], [["neutral", 0.9]], [["sad", 0.4], ["neutral", 0.35]]])

    async def senses(role, path, pcm):
        return {"moods": next(moods)} if role == "mood" else None

    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(return_value={"text": "hello there"})),
        patch.object(hub, "async_senses", side_effect=senses),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        await ws.receive_json()
        for _ in range(3):
            await ws.send_bytes(_audio((SILENCE, 0.3), (LOUD, 2.0), (SILENCE, 1)))
            await hass.async_block_till_done(wait_background_tasks=True)
            await asyncio.sleep(0.05)
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    speech = [x for x in hub.transcript if x["kind"] == "speech"]
    assert [x["mood"]["label"] for x in speech] == ["happy", "neutral", "sad"]
    assert [mood_word(x) for x in speech] == ["sounds happy", None, None]
    message = {"role": "user", "via": "voice", "mic": "PC", "voice": "voice #1", "mood": "sounds happy", "text": "hi"}
    assert spoken(message) == "[spoken at the microphone 'PC' by voice #1, sounds happy] hi"
