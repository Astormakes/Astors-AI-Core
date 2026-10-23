"""AAC's own audio pipeline: segments, transcript, answers by voice."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.aac.audio_hub import EnergyVad, Segmenter
from custom_components.aac.modules import async_set_access

from .conftest import setup_aac, untimed
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
    await store.async_set_languages(["de", "en"])  # several home languages: the server detects which
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
        stt_role = mock_entry.runtime_data.pool.status()["roles"]["stt"]
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


async def test_answers_are_read_in_their_languages_voice(hass: HomeAssistant, mock_entry, configured):
    """German answers were read by an English voice (2026-10-20): each answer gets the owner's voice for its language,
    and the engine's default when that voice fails."""
    from unittest.mock import MagicMock, patch

    from custom_components.aac import languages

    assert languages.detect("Ja, das Licht im Schlafzimmer ist gerade an.") == "de"
    assert languages.detect("La luce della camera è accesa.") == "it"  # any language, not only de/en
    assert languages.detect("Done.") != "en" and languages.detect("Done.", ["de", "en"]) == "en"  # short: among those in use
    assert languages.code_of("german") == "de" and languages.code_of("de_DE") == "de"  # whisper.cpp / TTS locales
    await setup_aac(hass, mock_entry)
    hub = mock_entry.runtime_data.audio
    await mock_entry.runtime_data.store.async_update_audio(
        {"voices": {"de": {"language": "de_DE", "voice": "de_DE-thorsten-high"}}})
    asked = []

    def create(_hass, engine, language, options):
        asked.append((language, options.get("voice")))
        if options.get("voice") == "broken":
            raise HomeAssistantError("no such voice")
        stream = MagicMock()

        async def result():
            yield b"RIFF"
        stream.async_stream_result = result
        return stream

    with patch.object(hub, "engine", return_value="tts.piper"), \
            patch("custom_components.aac.audio_hub.tts.async_create_stream", side_effect=create):
        assert await hub.async_tts("Das Licht ist an.") == b"RIFF"
        assert await hub.async_tts("The light is on.") == b"RIFF"
        await mock_entry.runtime_data.store.async_update_audio({"voices": {"en": {"language": "en_US", "voice": "broken"}}})
        assert await hub.async_tts("The light is on.") == b"RIFF"
    assert asked == [("de_DE", "de_DE-thorsten-high"), (None, None), ("en_US", "broken"), (None, None)]
    assert mock_entry.runtime_data.store.audio["languages_seen"] == {"de": 1, "en": 2}  # proposed in the panel


async def test_home_languages(hass: HomeAssistant, hass_ws_client, mock_entry, configured):
    """One global list (owner, 2026-10-20): from HA's language and country until the owner sets it, English when HA
    says nothing; detection guesses among them; the old single STT language migrates into it."""
    from custom_components.aac import languages

    assert languages.from_ha("en", "DE") == ["en", "de"] and languages.from_ha("de-CH", "CH") == ["de", "fr", "it"]
    assert languages.from_ha(None, None) == ["en"]
    hass.config.language, hass.config.country = "en", "DE"
    await setup_aac(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "aac/languages"})
    got = (await ws.receive_json())["result"]
    assert got["languages"] == ["en", "de"] and not got["set_by_owner"] and got["names"]["de"] == "German"
    await ws.send_json_auto_id({"type": "aac/languages/set", "languages": ["de", "en", "nl"]})
    assert (await ws.receive_json())["result"]["languages"] == ["de", "en", "nl"]
    assert mock_entry.runtime_data.audio.languages() == ["de", "en", "nl"]
    await ws.send_json_auto_id({"type": "aac/state"})
    assert next(s for s in (await ws.receive_json())["result"]["setup"] if s["id"] == "languages")["done"]
    await ws.send_json_auto_id({"type": "aac/languages/set", "languages": None})
    assert (await ws.receive_json())["result"]["languages"] == ["en", "de"]

    # 0.41: audio.language forced one language for speech to text → first in the list
    from custom_components.aac.store import AACStore

    mock_entry.runtime_data.store.audio["language"] = "fr"
    await mock_entry.runtime_data.store.async_save()
    store = AACStore(hass)
    await store.async_load()
    assert store.data["languages"] == ["fr", "en", "de"] and "language" not in store.audio


async def test_audio_cards_chain(hass: HomeAssistant, hass_client, aioclient_mock, mock_entry, configured):
    """The audio cards (spec/graph.md "Audio cards"): Microphone → Noise gate → Pause cut → Speech to text → the
    line → model → Voice, spoken at the satellite it came from; the gate keeps quiet audio out."""
    await setup_aac(hass, mock_entry)
    await async_set_access(mock_entry, "companion", "assistant", {"enabled": False})  # only the new chain answers
    data = mock_entry.runtime_data
    graph = data.store.graph("assistant")
    graph["nodes"].update({
        "mic": {"type": "microphone", "title": "PC mic", "settings": {"source": "pc"}},
        "gate": {"type": "noise_gate", "title": "Gate", "settings": {"level": 40, "hold": 100}},
        "cut": {"type": "pause_cut", "title": "Cut", "settings": {"pause": 300}},
        "heard_text": {"type": "speech_to_text", "title": "Heard text", "settings": {}}})
    wires = [(f"{a}.{ap}", f"{b}.{bp}") for a, ap, b, bp in data.bus.wires("assistant")]
    wires += [("mic.out", "gate.in"), ("gate.out", "cut.in"), ("cut.out", "heard_text.in"), ("heard_text.out", "charter.in")]
    graph["wires"] = [{"from": a, "to": b} for a, b in wires]
    payloads = _queue(aioclient_mock, *[_text("It is five o'clock.")] * 4)
    hub = data.audio
    client = await hass_client()
    with (
        patch("custom_components.aac.audio_hub.make_vad", return_value=EnergyVad()),
        patch.object(hub, "async_stt", AsyncMock(return_value={"text": "What time is it?"})) as stt,
        patch.object(hub, "async_tts", AsyncMock(return_value=b"RIFFwave")),
    ):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "area": "Office", "rate": 16000})
        assert (await ws.receive_json())["type"] == "welcome"
        quiet = array("h", [200, -200] * 80).tobytes()  # below the gate: never a piece
        await ws.send_bytes(_audio((SILENCE, 0.2), (quiet, 0.8), (SILENCE, 0.5)))
        await ws.send_bytes(_audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 0.5)))
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play["type"] == "play" and play["text"] == "It is five o'clock."
        await ws.close()
    await hass.async_block_till_done(wait_background_tasks=True)
    pieces = data.bus.recent("assistant", "cut", "out")
    assert len(pieces) == 1 and pieces[0]["seconds"] >= 0.8  # the quiet stretch never became a piece
    run = next(r for r in reversed(data.runs.runs) if r.input == "What time is it?")
    assert run.source == "heard_text" and untimed(payloads[0]["messages"][-1]["content"]).endswith("What time is it?")
    assert stt.await_count >= 1
    chat = next(c for c in data.domain("assistant").chats.list() if c["title"] == "Voice · PC")  # kept as a voice chat
    assert [m["text"] for m in data.domain("assistant").chats.get(chat["id"])["messages"]] == ["What time is it?", "It is five o'clock."]


async def test_speech_to_text_stays_in_the_home_languages(hass: HomeAssistant, mock_entry, configured):
    """Whisper guessed Dutch for German with an accent: the Speech to text card transcribes it again in the home's language."""
    await setup_aac(hass, mock_entry)
    await mock_entry.runtime_data.store.async_set_languages(["de", "en"])
    hub, calls = mock_entry.runtime_data.audio, []

    async def stt(pcm, prompt=None, language=None):
        calls.append(language)
        return {"text": "Mach het licht uit." if language is None else "Mach das Licht aus.", "language": language or "dutch"}

    card = mock_entry.runtime_data.bus.templates["speech_to_text"].make("ears", "Ears", {"remember": 0})
    sent = []
    with patch.object(hub, "async_stt", side_effect=stt), \
            patch.object(mock_entry.runtime_data.flow, "async_send", AsyncMock(side_effect=lambda m, **k: sent.append(m))):
        await card.run("assistant", "in", {"pcm": SILENCE, "source": "pc", "name": "PC", "seconds": 1.0})
    assert calls == [None, "de"] and sent[0].text.startswith("Mach das Licht aus.")
