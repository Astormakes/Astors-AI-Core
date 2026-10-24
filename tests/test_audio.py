"""Audio: a satellite streams, the audio cards hear, the answer is spoken at the satellite (also an HA media player)."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.aac.audio import EnergyVad, Segmenter

from .conftest import model_answers, reply, setup_aac

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
          patch.object(core.audio, "stt", heard), patch.object(core.audio, "tts", AsyncMock(return_value=b"RIFFwave"))):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        assert (await ws.receive_json())["type"] == "welcome"
        await ws.send_bytes(audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 1.0)))
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play == {"type": "play", "format": "wav", "bytes": 8, "text": "It is five."}
        assert await ws.receive_bytes() == b"RIFFwave"
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
    """HA plays on the satellite and stops it; a stop reaching Voice out cuts off what it says."""
    from custom_components.aac.flow import Step
    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    core.audio.settings.update({"tts": "tts.test", "volume": 100})
    aioclient_mock.get("http://example.local/chime.wav", content=b"RIFFchime")
    client = await hass_client()
    with patch.object(core.audio, "tts", AsyncMock(return_value=b"RIFFwave")):
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
        speaker = await core.domain.add_card("voice_out", "Voice out")
        m = Context()
        m.add("ears", "said", "computer, tell me a story", mic="pc")
        m.add("model", "answer", "Once upon a time …")
        await speaker.handle(m, "in", Step(core.domain.flow, speaker, "in", m))
        assert (await ws.receive_json())["text"] == "Once upon a time …" and await ws.receive_bytes() == b"RIFFwave"
        assert await speaker.stop({"kind": "signal", "text": "stop"})
        assert await ws.receive_json() == {"type": "stop"}
        await ws.close()
    core.audio.forget("pc")  # gone for good: out of the known ones, its media player removed
    await hass.async_block_till_done()
    assert hass.states.get(player) is None and not core.audio.settings["known"]
