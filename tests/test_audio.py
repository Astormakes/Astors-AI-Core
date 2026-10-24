"""Audio: a satellite streams, the audio cards hear, the answer is spoken at the satellite (also an HA media player)."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.aac.audio import EnergyVad, Segmenter

from custom_components.aac.audio import pcm_to_wav

from .conftest import model_answers, reply, setup_aac

WAVE = pcm_to_wav(b"\x01\x00" * 1600, 22050)  # what a voice engine gives back

SILENCE = array("h", [0] * 160).tobytes()  # 10 ms
LOUD = array("h", [3000, -3000] * 80).tobytes()
QUIET = array("h", [200, -200] * 80).tobytes()


def audio(*parts) -> bytes:
    return b"".join(frame * int(seconds * 100) for frame, seconds in parts)


def test_pause_cut_cuts_speech():
    seg = Segmenter(EnergyVad(), pause_ms=300)
    assert seg.feed(audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 0.5))) and not seg.active


async def test_a_spoken_round_trip(hass: HomeAssistant, hass_client, mock_entry, aioclient_mock):
    """The default voice chain: "computer, …" is answered aloud and lands in a new chat; a line right after AAC spoke
    goes to the model as a possible follow-up, and "nothing" stays silent and is not kept."""
    from custom_components.aac.defaults import FOLLOW_UP, async_voice_chain
    core = await setup_aac(hass, mock_entry)
    core.audio.settings.update({"tts": "tts.test", "volume": 100, "known": [{"id": "pc", "name": "PC"}]})
    core.data["voice_chain"] = False
    await async_voice_chain(core)
    d = core.domain
    asked = model_answers(aioclient_mock, reply("It is five."), reply("nothing"))
    heard = AsyncMock(side_effect=[{"text": "Computer, what time is it?"}, {"text": "see you in the next video"}])
    client = await hass_client()
    with (patch("aac_cards.audio.make_vad", return_value=EnergyVad()),
          patch.object(core.audio, "stt", heard), patch.object(core.audio, "tts", AsyncMock(return_value=WAVE))):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        assert (await ws.receive_json())["type"] == "welcome"
        await ws.send_bytes(audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 1.0)))
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play == {"type": "play", "format": "wav", "bytes": len(WAVE), "text": "It is five."}
        assert await ws.receive_bytes() == WAVE
        await ws.send_json({"type": "played"})
        await hass.async_block_till_done(wait_background_tasks=True)
        assert core.audio.spoken()["satellites"]["pc"]["seconds"] < 10
        await ws.send_bytes(audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 1.0)))  # right after: a possible follow-up
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.close()
    assert asked[0]["messages"][-1]["content"].startswith("Computer, what time is it?")
    assert asked[1]["messages"][-1]["content"].startswith(FOLLOW_UP)
    assert "It is five." in str(asked[1]["messages"])  # the follow-up sees the conversation (read from the chat)
    chat = d.cards["chat"]
    kept = chat.get(chat.chats()[0]["id"])
    assert kept["title"].startswith("Voice · PC · ") and [m["text"] for m in kept["messages"]] == ["Computer, what time is it?", "It is five."]


async def test_the_voice_chain_runs_side_by_side(hass: HomeAssistant, mock_entry):
    """Stop word, Wake word and Follow-up each get every heard line; only the right one passes it on."""
    from custom_components.aac.defaults import async_voice_chain
    from custom_components.aac.flow import Step
    from custom_components.aac.context import Context
    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "pc", "name": "PC"}]
    core.data["voice_chain"] = False
    await async_voice_chain(core)
    d = core.domain
    assert {"mic", "cut", "ears", "stop_word", "wake", "follow_up", "spoken", "followed", "answer", "speaker"} <= set(d.cards)
    assert {"from": "chat.provides", "to": "spoken.reads"} in d.wires and {"from": "model.out", "to": "answer.in"} in d.wires
    assert {"from": "model.out", "to": "chat.answers"} not in d.wires

    async def route(card, text):
        m = Context()
        m.add("ears", "said", text, mic="pc")
        return await d.cards[card].handle(m, "in", Step(d.flow, d.cards[card], "in", m)), m
    assert (await route("wake", "Computer, lights on"))[0] == [("yes", "spoken")]
    assert (await route("wake", "see you in the next video"))[0] == []
    out, m = await route("stop_word", "stop it")
    assert out == [("stop", "charter")] and m.stopped
    assert (await route("follow_up", "and the kitchen too"))[0] == []  # AAC has not spoken
    core.audio._spoke["pc"] = __import__("time").time()
    assert (await route("follow_up", "and the kitchen too"))[0] == [("yes", "followed")]
    assert (await route("follow_up", "computer, and the kitchen"))[0] == []  # the Wake word takes that one


async def test_a_satellite_is_a_media_player(hass: HomeAssistant, hass_client, mock_entry, aioclient_mock):
    """HA plays on the satellite and stops it; Text to speech makes a clip the Speaker plays at the satellite the line
    came from; a stop reaching Text to speech goes on to the Speaker and cuts it off."""
    from custom_components.aac.flow import Step
    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    from custom_components.aac.audio import pcm_to_wav

    core.audio.settings.update({"tts": "tts.test", "volume": 100})
    aioclient_mock.get("http://example.local/chime.wav", content=b"RIFFchime")
    client = await hass_client()
    spoken = pcm_to_wav(b"\x01\x00" * 2205, 22050)
    with patch.object(core.audio, "tts", AsyncMock(return_value=spoken)):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        assert (await ws.receive_json())["type"] == "welcome"
        await hass.async_block_till_done()
        player = "media_player.aac_satellite_pc"
        assert hass.states.get(player).state == "idle"
        await hass.services.async_call("media_player", "play_media", {"entity_id": player, "media_content_id": "http://example.local/chime.wav",
                                                                      "media_content_type": "music"}, blocking=True)
        assert (await ws.receive_json())["type"] == "play" and await ws.receive_bytes() == b"RIFFchime"
        assert hass.states.get(player).state == "playing"
        await hass.services.async_call("media_player", "media_stop", {"entity_id": player}, blocking=True)
        assert await ws.receive_json() == {"type": "stop"}
        await ws.send_json({"type": "played"})
        for _ in range(100):  # the satellite's "played" arrives on its own connection
            if hass.states.get(player).state == "idle":
                break
            await asyncio.sleep(0.01)
        assert hass.states.get(player).state == "idle"
        voice = await core.domain.add_card("text_to_speech", "Text to speech")
        speaker = await core.domain.add_card("speaker", "Speaker")
        core.domain.set_wires([*core.domain.wires, {"from": f"{voice.id}.out", "to": f"{speaker.id}.in"}])
        m = Context()
        m.add("ears", "said", "computer, tell me a story", mic="pc")
        m.add("model", "answer", "Once upon a time …")
        await voice.handle(m, "in", Step(core.domain.flow, voice, "in", m))
        assert (await ws.receive_json())["text"] == "Once upon a time …" and await ws.receive_bytes() == spoken
        assert not await voice.stop({"kind": "signal", "text": "stop"})  # it was not busy; its stop clip goes on
        assert await ws.receive_json() == {"type": "stop"}
        await ws.close()
    core.audio.forget("pc")  # gone for good: out of the known ones, its media player removed
    await hass.async_block_till_done()
    assert hass.states.get(player) is None and not core.audio.settings["known"]


async def test_home_languages(hass, mock_entry, hass_ws_client):
    """The home's languages: from HA (language, then country) until the owner sets them; templates read them."""
    from custom_components.aac.languages import code_of, from_ha

    assert from_ha("de-DE", "CH") == ["de", "fr", "it"] and from_ha(None, None) == ["en"]
    assert code_of("german") == "de" and code_of("en_US") == "en" and code_of("klingon") is None
    core = await setup_aac(hass, mock_entry)
    hass.config.language, hass.config.country = "de", "AT"
    assert core.languages() == ["de"]
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "aac/languages/set", "languages": ["English", "de", "xx"]})
    result = (await client.receive_json())["result"]
    assert [x["code"] for x in result["languages"]] == ["en", "de"] and result["languages_own"]
    assert core.domain.template_values()["languages"] == ["en", "de"]
    await client.send_json_auto_id({"type": "aac/languages/set", "languages": None})
    assert [x["code"] for x in (await client.receive_json())["result"]["languages"]] == ["de"]


async def test_a_scope_shows_audio_only_while_watched(hass, mock_entry):
    """A card's view: the Scope passes audio on and sends frames only while a Nodes page shows it; live wire levels
    likewise only while a Nodes page is open."""
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.aac.const import SIGNAL

    core = await setup_aac(hass, mock_entry)
    d = core.domain
    scope = await d.add_card("scope", "Scope", {"mode": "spectrum", "fps": 30})
    gate = await d.add_card("noise_gate", "Gate")
    d.set_wires([*d.wires, {"from": f"{scope.id}.out", "to": f"{gate.id}.in"}])
    assert scope.summary()["view"]["element"] == "aac-view-scope" and scope.summary()["view"]["height"] == 90
    seen, passed = [], []
    unsub = async_dispatcher_connect(hass, SIGNAL, lambda e: seen.append(e["what"]))
    with patch.object(type(gate), "stream", lambda self, port, chunk, meta: passed.append(len(chunk)), create=True):
        d.flow.stream(scope.id, "in", audio((LOUD, 0.1)), {"source": "pc"})  # nobody watches
        scope.stream("in", audio((LOUD, 0.1)), {"source": "pc"})
        assert "view" not in seen and "level" not in seen and passed
        core.watch([scope.id])
        scope.stream("in", audio((LOUD, 0.1)), {"source": "pc"})
        await hass.async_block_till_done()
    unsub()
    assert "view" in seen and "level" in seen


async def test_a_level_card_keeps_the_size_of_any_card(hass, mock_entry):
    """The Level card: an inline view (no extra height), one point per frame while watched, audio passed on."""
    core = await setup_aac(hass, mock_entry)
    level = await core.domain.add_card("level", "Level")
    assert level.summary()["view"]["inline"] is True
    shown = []
    with patch.object(type(level), "show", lambda self, data: shown.append(data)):
        level.stream("in", audio((LOUD, 0.1)), {"source": "pc"})
        assert shown == []  # nobody watches: nothing computed
        core.watch([level.id])
        level.stream("in", audio((LOUD, 0.1)), {"source": "pc"})
    assert len(shown) == 1 and -40 < shown[0] < 0


async def test_spoken_turn_timing(hass, mock_entry):
    """The steps of a spoken turn are timed from the end of the speech; only turns that got to playing are shown."""
    core = await setup_aac(hass, mock_entry)
    a = core.audio
    a.mark("pc", "speech ended")
    a.mark("pc", "text ready")
    assert a.timing() == []  # not answered (yet)
    a.mark("pc", "first sentence")
    a.mark("pc", "text ready")  # once per turn
    a.mark("pc", "playing")
    (turn,) = a.timing()
    assert turn["source"] == "pc" and list(turn["stages"]) == ["text ready", "first sentence", "playing"]


def test_phrases_inside_the_speech():
    """With a phrase pause, a short pause inside the speech hands out the phrase so far; the long pause ends it."""
    seg = Segmenter(EnergyVad(), pause_ms=600, phrase_ms=200)
    got = seg.feed_parts(audio((SILENCE, 0.2), (LOUD, 0.6), (SILENCE, 0.3), (LOUD, 0.6), (SILENCE, 0.8)))
    assert [k for k, _ in got] == ["phrase", "phrase", "end"] and got[-1][1] == 2
    whole = Segmenter(EnergyVad(), pause_ms=600)  # without: one piece, as before
    assert len(whole.feed(audio((SILENCE, 0.2), (LOUD, 0.6), (SILENCE, 0.3), (LOUD, 0.6), (SILENCE, 0.8)))) == 1


async def test_phrase_joiner_and_its_wiring(hass, mock_entry):
    """Setting a phrase pause adds a Phrase joiner after Speech to text (it takes over its wires); the joiner sends the
    sentence once all its phrases are heard, in order."""
    from custom_components.aac.context import Context
    from custom_components.aac.defaults import async_voice_chain

    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "pc", "name": "PC"}]
    core.data["voice_chain"] = False
    await async_voice_chain(core)
    d = core.domain
    wake_x, ears = d.positions["wake"]["x"], dict(d.positions["ears"])
    await d.update_card("cut", settings={"phrase": 250})
    await hass.async_block_till_done()
    joiner = next(c for c in d.cards.values() if c.type == "phrase_joiner")
    assert d.positions["wake"]["x"] == wake_x + 290 and d.positions[joiner.id] == {"x": ears["x"] + 290, "y": ears["y"]}
    assert {"from": "cut.ends", "to": f"{joiner.id}.ends"} in d.wires and {"from": "ears.out", "to": f"{joiner.id}.lines"} in d.wires
    assert {"from": f"{joiner.id}.out", "to": "wake.in"} in d.wires and {"from": "ears.out", "to": "wake.in"} not in d.wires
    sent = []
    with patch.object(d.flow, "send", AsyncMock(side_effect=lambda ctx, card, port: sent.append(ctx))):
        for part, text in ((2, "what time is it?"), (1, "computer,")):  # out of order
            c = Context()
            c.add("ears", "said", text, ["heard"], mic="pc", utterance="u1", part=part, seconds=0.6)
            assert await joiner.handle(c, "lines", None) == []
        assert sent == []
        await joiner.clip("ends", {"utterance": "u1", "parts": 2})
    (whole,) = sent
    assert whole.last("said")["text"] == "computer, what time is it?" and whole.last("said")["data"]["mic"] == "pc"
