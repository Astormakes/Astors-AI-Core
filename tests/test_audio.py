"""Audio: a satellite streams, the audio cards hear, the answer is spoken at the satellite, a follow-up window opens."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant

from custom_components.aac.audio import EnergyVad, Segmenter

from .conftest import model_answers, reply, setup_aac, v1_files

SILENCE = array("h", [0] * 160).tobytes()  # 10 ms
LOUD = array("h", [3000, -3000] * 80).tobytes()
QUIET = array("h", [200, -200] * 80).tobytes()


def audio(*parts) -> bytes:
    return b"".join(frame * int(seconds * 100) for frame, seconds in parts)


def test_pause_cut_cuts_speech():
    seg = Segmenter(EnergyVad(), pause_ms=300)
    assert seg.feed(audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 0.5))) and not seg.active


async def test_a_spoken_round_trip(hass: HomeAssistant, hass_client, mock_entry, aioclient_mock):
    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    core = await setup_aac(hass, mock_entry)
    d = core.domain
    for kind, cid, settings in (("microphone", "mic", {"source": "pc"}), ("noise_gate", "gate", {"level": 40, "hold": 100}),
                                ("pause_cut", "cut", {"pause": 300}), ("speech_to_text", "ears", {}),
                                ("voice_chat", "voice", {"follow_up": 30}), ("voice_out", "speaker", {})):
        await d.add_card(kind, cid, settings, card_id=cid)
    d.set_wires([*d.wires, {"from": "mic.out", "to": "gate.in"}, {"from": "gate.out", "to": "cut.in"},
                 {"from": "cut.out", "to": "ears.in"}, {"from": "ears.out", "to": "voice.heard"},
                 {"from": "voice.said", "to": "charter.in"}, {"from": "model.out", "to": "voice.answers"},
                 {"from": "voice.speak", "to": "speaker.in"}, {"from": "speaker.status", "to": "voice.status"}])
    core.audio.settings.update({"tts": "tts.test", "volume": 100})
    asked = model_answers(aioclient_mock, reply("It is five."))
    client = await hass_client()
    with (patch("custom_components.aac.cards.audio.make_vad", return_value=EnergyVad()),
          patch.object(core.audio, "stt", AsyncMock(return_value={"text": "What time is it?"})) as stt,
          patch.object(core.audio, "tts", AsyncMock(return_value=b"RIFFwave"))):
        ws = await client.ws_connect("/api/aac/audio")
        await ws.send_json({"type": "hello", "id": "pc", "name": "PC", "rate": 16000})
        assert (await ws.receive_json())["type"] == "welcome"
        await ws.send_bytes(audio((SILENCE, 0.2), (QUIET, 0.8), (SILENCE, 0.5)))  # below the gate
        await ws.send_bytes(audio((SILENCE, 0.2), (LOUD, 0.8), (SILENCE, 0.5)))
        play = await asyncio.wait_for(ws.receive_json(), 5)
        assert play == {"type": "play", "format": "wav", "bytes": 8, "text": "It is five."}
        assert await ws.receive_bytes() == b"RIFFwave"
        await ws.send_json({"type": "played"})
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.close()
    assert stt.await_count == 1  # the quiet stretch never became a piece
    assert asked[0]["messages"][-1]["content"].startswith("What time is it?")
    voice = d.cards["voice"]
    chat = voice.get(voice.chats()[0]["id"])
    assert chat["title"].startswith("Voice · PC · ") and [m["text"] for m in chat["messages"]] == ["What time is it?", "It is five."]
    assert voice._windows.get("pc")  # the follow-up window is open


async def test_the_default_voice_chain_and_its_wake_word(hass: HomeAssistant, mock_entry):
    """With satellites known, the default domain gets the voice chain; the Wake word passes 'computer' or a follow-up."""
    from custom_components.aac.defaults import async_voice_chain
    from custom_components.aac.flow import Step
    from custom_components.aac.message import Message

    await hass.async_add_executor_job(v1_files, hass.config.config_dir)
    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "pc", "name": "PC"}]
    core.data["voice_chain"] = False
    await async_voice_chain(core)
    d = core.domain
    assert {"mic", "cut", "ears", "voice", "wake", "voice_rules", "speaker"} <= set(d.cards)
    assert {"from": "voice_rules.out", "to": "charter.in"} in d.wires and {"from": "model.out", "to": "voice.answers"} in d.wires
    wake = d.cards["wake"]

    async def route(text, tags=()):
        m = Message()
        m.add("ears", "said", text, list(tags))
        return await wake.handle(m, "in", Step(d.flow, wake, "in", m))
    assert await route("Computer, lights on") == [("yes", "voice_rules")]
    assert await route("see you in the next video") == []
    assert await route("and the kitchen too", ["voice.follow_up"]) == [("yes", "voice_rules")]
