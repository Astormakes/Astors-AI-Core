"""Audio: a satellite streams, the audio cards hear, the answer is spoken at the satellite (also an HA media player)."""

import asyncio
from array import array
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.aac.sound import EnergyVad, Segmenter

from custom_components.aac.sound import pcm_to_wav

from .conftest import BASE_URL, allow, reply, setup_aac

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
    from custom_components.aac.defaults import FOLLOW_UP
    core = await setup_aac(hass, mock_entry)
    core.audio.settings.update({"tts": "tts.test", "volume": 100, "known": [{"id": "pc", "name": "PC"}]})
    core.data["voice_chain"] = False
    await core.voice_chain()
    d = core.domain
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
    asked, answers, in_chat = [], [reply("It is five."), reply("nothing")], []

    async def model(method, url, data):  # what the chat shows while the model is still working
        asked.append(data)
        chat = d.cards["chat"]
        voiced = [c for c in chat.chats() if c["title"].startswith("Voice")]  # the newest spoken chat
        in_chat.append([m["text"] for m in chat.get(voiced[0]["id"])["messages"]] if voiced else [])
        return AiohttpClientMockResponse(method, url, json=answers[len(asked) - 1])
    aioclient_mock.post(f"{BASE_URL}/chat/completions", side_effect=model)
    heard = AsyncMock(side_effect=[{"text": "Computer, what time is it?"}, {"text": "see you in the next video"}])
    client = await hass_client()
    with (patch("aac_cards.pause_cut.make_vad", return_value=EnergyVad()),
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
        for _ in range(100):  # the socket hands the audio over in its own time: wait for the model to be asked
            await hass.async_block_till_done(wait_background_tasks=True)
            if len(asked) >= 2:
                break
            await asyncio.sleep(0.05)
        await hass.async_block_till_done(wait_background_tasks=True)
        await ws.close()
    assert asked[0]["messages"][-1]["content"].startswith("Computer, what time is it?")
    assert asked[1]["messages"][-1]["content"].startswith(FOLLOW_UP)
    assert "It is five." in str(asked[1]["messages"])  # the follow-up sees the conversation (read from the chat)
    chat = d.cards["chat"]
    kept = chat.get(next(c for c in chat.chats() if c["title"].startswith("Voice"))["id"])
    assert kept["title"].startswith("Voice · PC · ") and [m["text"] for m in kept["messages"]] == ["Computer, what time is it?", "It is five."]
    assert in_chat[0] == []  # a heard line shows with its answer (no answer: it never shows)
    assert in_chat[1] == ["Computer, what time is it?", "It is five."]  # the follow-up's "nothing" sent nothing


async def test_the_voice_chain_runs_side_by_side(hass: HomeAssistant, mock_entry):
    """Stop word, Wake word and Follow-up each get every heard line; only the right one passes it on."""
    from unittest.mock import patch

    from unittest.mock import AsyncMock

    from custom_components.aac.context import Context
    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "pc", "name": "PC"}]
    core.data["voice_chain"] = False
    await core.voice_chain()
    d = core.domain
    assert {"microphone", "audio_clipper", "speech_to_text", "stop_word", "wake_word", "follow_up", "spoken_turn", "follow_up_turn", "speaker"} <= set(d.cards)
    assert "answer" not in d.cards  # the answer goes to every card wired from the Model's out: no Relay in between
    assert {"from": "chat.provides", "to": "spoken_turn.chat"} in d.wires
    assert {"from": "model.out", "to": "chat.answers"} in d.wires and {"from": "model.out", "to": "text_to_speech.in"} in d.wires
    sent = []

    async def caught(card, context, port):  # what a Relay sends, without running the cards after it
        sent.append(port)

    async def route(card, text, seconds=1.0, **data):
        m = Context()
        m.add("speech_to_text.said", text, mic="pc", seconds=seconds, **data)
        sent.clear()
        with patch.object(d.flow, "send", caught):
            await d.cards[card].handle(m, "in")
        return list(sent)
    assert await route("wake_word", "Computer, lights on") == ["yes"]
    assert await route("wake_word", "see you in the next video") == []
    with patch.object(d, "stop", AsyncMock()) as stopped:
        assert await route("stop_word", "stop it") == [] and stopped.call_count == 1  # every card stops, nothing travels
    assert await route("follow_up", "and the kitchen too") == []  # AAC has not spoken
    core.audio._spoke["pc"] = __import__("time").time()
    assert await route("follow_up", "and the kitchen too") == ["yes"]
    assert await route("follow_up", "computer, and the kitchen") == []  # the Wake word takes that one
    core.audio._spoke["pc"] = __import__("time").time() - 25
    assert await route("follow_up", "a long answer", seconds=22) == ["yes"]  # began 3 s after
    assert await route("follow_up", "something later", seconds=2) == []  # began 23 s after
    core.audio._spoke["pc"] = __import__("time").time()
    assert await route("follow_up", "thank you", aac_spoke=True) == []  # began while AAC spoke: its own voice


async def test_a_satellite_is_a_media_player(hass: HomeAssistant, hass_client, mock_entry, aioclient_mock):
    """HA plays on the satellite and stops it; Text to speech makes a clip the Speaker plays at the satellite the line
    came from; a stop reaching Text to speech goes on to the Speaker and cuts it off."""
    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    from custom_components.aac.sound import pcm_to_wav

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
        speaker = await allow(core.domain, speaker.id, {"audio": "full"})
        core.domain.set_wires([*core.domain.wires, {"from": f"{voice.id}.out", "to": f"{speaker.id}.in"}])
        m = Context()
        m.add("speech_to_text.said", "computer, tell me a story", mic="pc")
        m.add("model.said", "Once upon a time …")
        await voice.handle(m, "in")
        assert (await ws.receive_json())["text"] == "Once upon a time …" and await ws.receive_bytes() == spoken
        await voice.stop()  # its stop clip goes to the Speaker
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
    assert (await core.domain.template_values())["languages"] == ["en", "de"]
    await client.send_json_auto_id({"type": "aac/languages/set", "languages": None})
    assert [x["code"] for x in (await client.receive_json())["result"]["languages"]] == ["de"]


async def test_a_scope_shows_audio_only_while_watched(hass, mock_entry):
    """A card's view: the Scope passes audio on and sends frames only while a Cards page shows it; live wire levels
    likewise only while a Cards page is open."""
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
    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    level = await core.domain.add_card("level", "Level")
    await core.domain.add_card("relay", "Sink", {"script": ""})
    core.domain.set_wires([*core.domain.wires, {"from": f"{level.id}.cuts", "to": "sink.in"}])
    assert level.summary()["view"]["inline"] is True
    shown = []
    with patch.object(type(level), "show", lambda self, data: shown.append(data)):
        level.stream("in", audio((LOUD, 0.1)), {"source": "pc"})
        assert shown == []  # nobody watches: nothing computed
        core.watch([level.id])
        level.stream("in", audio((LOUD, 0.1)), {"source": "pc"})
        for data in ({"cut": "start", "utterance": "u1", "ago": 0.3}, {"cut": "end", "utterance": "u1", "parts": 1}):
            c = Context()
            c.add(f"audio_clipper.{data['cut']}", "", **data)
            await level.handle(c, "cuts")
            assert core.domain.flow.held[f"{level.id}.cuts"] is not None  # on at 'cuts'

    assert len(shown) == 3 and -40 < shown[0]["db"] < 0
    assert shown[1] == {"mark": "start", "ago": 0.3} and shown[2] == {"mark": "end", "ago": 0.0}
    await core.domain.update_card(level.id, settings={"height": 80})
    assert core.domain.cards[level.id].summary()["view"]["height"] == 80  # bigger: under its ports


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
    assert [k for k, _ in got] == ["start", "phrase", "phrase", "end"] and got[-1][1] == 2
    whole = Segmenter(EnergyVad(), pause_ms=600)  # without: one piece, as before
    assert len(whole.feed(audio((SILENCE, 0.2), (LOUD, 0.6), (SILENCE, 0.3), (LOUD, 0.6), (SILENCE, 0.8)))) == 1


@pytest.mark.fresh  # the default voice chain's wiring (an older install keeps its own)
async def test_the_audio_clipper_puts_its_phrases_together(hass, mock_entry):
    """One card for both halves: the voice chain wires Speech to text's lines back into the Audio clipper and what was
    said leaves there (the Stop word hears Speech to text at once); a whole line goes straight on, phrases wait for the
    sentence's end and leave as one line, in order."""
    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    core.audio.settings["known"] = [{"id": "pc", "name": "PC"}]
    core.data["voice_chain"] = False
    await core.voice_chain()
    d = core.domain
    for wire in (("speech_to_text.out", "audio_clipper.lines"), ("audio_clipper.said", "wake_word.in"),
                 ("audio_clipper.said", "follow_up.in"), ("speech_to_text.out", "stop_word.in")):
        assert {"from": wire[0], "to": wire[1]} in d.wires
    clipper = await d.update_card("audio_clipper", settings={"phrase": 250})
    sent = []
    with patch.object(d.flow, "send", AsyncMock(side_effect=lambda card, ctx, port: sent.append((port, ctx)))):
        line = Context()
        line.add("speech_to_text.said", "a whole line", mic="pc")
        await clipper.handle(line, "lines")
        assert [(p, c.get("*.said")["text"]) for p, c in sent] == [("said", "a whole line")]  # straight on
        sent.clear()
        for part, text in ((2, "what time is it?"), (1, "computer,")):  # out of order
            c = Context()
            c.add("speech_to_text.said", text, mic="pc", utterance="u1", part=part, seconds=0.6)
            await clipper.handle(c, "lines")
        assert sent == []
        await clipper._ended("u1", 2)  # noqa: SLF001 - its own cut: the sentence is done, with two parts
    ((port, whole),) = sent
    said = whole.get("*.said")
    assert port == "said" and said["tag"] == "audio_clipper.said" and said["text"] == "computer, what time is it?"
    assert said["data"]["mic"] == "pc" and said["data"]["phrases"] == 2


async def test_microphone_mutes_or_keeps_listening_while_aac_speaks(hass, mock_entry):
    """The core passes a satellite's audio on, marked while AAC speaks there; the Microphone card mutes it, or keeps
    listening (with echo cancellation a stop word works mid-answer)."""
    core = await setup_aac(hass, mock_entry)
    mic = await core.domain.add_card("microphone", "Mic")
    mic = await allow(core.domain, mic.id, {"audio": "read"})
    sent = []
    with patch.object(core.domain.flow, "stream", lambda card, port, chunk, meta: sent.append(meta.get("speaking"))):
        mic.hear(b"\x00\x00", {"source": "pc", "speaking": False})
        mic.hear(b"\x00\x00", {"source": "pc", "speaking": True})
        assert sent == [False]  # muted while AAC speaks
        await core.domain.update_card(mic.id, settings={"speaking": "listen"})
        core.domain.cards[mic.id].hear(b"\x00\x00", {"source": "pc", "speaking": True})
    assert sent == [False, True]


async def test_echo_subtraction(hass: HomeAssistant, mock_entry):
    """A satellite that sends what its speaker plays (2 channels): the hub splits them, the Microphone takes that sound
    out (delayed, scaled, maybe inverted; a negative delay delays the microphone), the waveforms view gets both."""
    import base64
    from array import array
    core = await setup_aac(hass, mock_entry)
    hub = core.audio

    async def send(item):
        return None
    source = hub.connect("box", "Box", None, send, reference=True)
    mic_card = await core.domain.add_card("microphone", "Box mic", {"source": "box", "speaking": "listen", "echo": True,
                                                                     "echo_delay": 1.0, "echo_gain": 0.5})
    mic_card = await allow(core.domain, mic_card.id, {"audio": "read"})
    heard = []
    mic_card.domain.flow.stream = lambda card_id, port, chunk, meta: heard.append(chunk) if card_id == mic_card.id else None
    speaker = array("h", (int(8000 * ((i * 37) % 101 - 50) / 50) for i in range(3200)))  # what the speaker plays
    echo = array("h", [0] * 16) + array("h", (v // 2 for v in speaker[:-16]))  # it reaches the mic 1 ms later, half as loud
    for at in range(0, 3200, 320):  # 20 ms chunks, interleaved as the satellite sends them
        both = array("h", [0] * 640)
        both[0::2], both[1::2] = echo[at:at + 320], speaker[at:at + 320]
        hub.feed(source, both.tobytes())
    out = array("h", b"".join(heard))
    assert len(out) == 3200 and max(abs(v) for v in out) <= 1  # the echo is gone (rounding aside)
    assert max(abs(v) for v in echo) > 3000

    mic_card._history.clear()
    mic_card.settings.update(echo_invert=True)  # an inverted path: the same sound added back
    inverted = array("h", (-v for v in echo))
    heard.clear()
    for at in range(0, 3200, 320):
        both = array("h", [0] * 640)
        both[0::2], both[1::2] = inverted[at:at + 320], speaker[at:at + 320]
        hub.feed(source, both.tobytes())
    assert max(abs(v) for v in array("h", b"".join(heard))) <= 1

    mic_card._history.clear()
    mic_card.settings.update(echo_invert=False, echo_delay=-1.0, echo_gain=2)  # the speaker's sound comes 1 ms late
    late_ref = array("h", [0] * 16) + array("h", (v // 2 for v in echo[:-16]))
    heard.clear()
    for at in range(0, 3200, 320):
        both = array("h", [0] * 640)
        both[0::2], both[1::2] = echo[at:at + 320], late_ref[at:at + 320]
        hub.feed(source, both.tobytes())
    tail = array("h", b"".join(heard))[64:]  # the microphone runs 1 ms late now; the start was silence
    assert max(abs(v) for v in tail) <= 2

    got = await mic_card.ui_call("recent", {"seconds": 0.1})
    assert got["source"] == "box" and len(base64.b64decode(got["mic"])) == 3200 and len(base64.b64decode(got["reference"])) == 3200


async def test_a_text_to_speech_that_never_finishes_is_given_up(hass, mock_entry):
    """An engine that never finishes (owner 2026-11-01: a whole context sent to Piper) holds up nothing: after TTS_WAIT
    it is given up, the log says so, and the next text is spoken."""
    import asyncio

    core = await setup_aac(hass, mock_entry)
    core.audio.settings["tts"] = "tts.test"

    class Stuck:
        def async_set_message(self, text):
            pass

        async def async_stream_result(self):
            await asyncio.sleep(3600)
            yield b""
    with patch("custom_components.aac.audio.TTS_WAIT", 0.05), \
            patch("custom_components.aac.audio.tts.async_create_stream", return_value=Stuck()):
        assert await core.audio.tts("a very long text") is None
    assert "given up" in core.domain.log.read(part="audio", levels=["warning"])[-1]["text"]


async def test_text_to_speech_speaks_each_entry_once(hass, mock_entry):
    """Whatever Speaks says (owner 2026-11-01: it matched every entry, and a long chat was read out from the start on
    every turn): only entries written after the card started, each once; a long conversation is not read again."""
    from custom_components.aac.context import Context

    core = await setup_aac(hass, mock_entry)
    old = Context()
    for i in range(120):
        old.add("chat.said", f"old line {i}.")
    voice = await core.domain.add_card("text_to_speech", "Voice", {"speaks": "*"})
    spoken = []
    with patch.object(type(voice), "_queue", lambda self, text, heard: spoken.append(text)):
        await voice.handle(old, "in")
        assert spoken == []  # written before it started
        old.add("model.said", "Hello there.")
        await voice.handle(old, "in")
        await voice.handle(old, "in")
        old.add("chat.said", "And now?")
        await voice.handle(old, "in")
    assert spoken == ["Hello there.", "And now?"]
