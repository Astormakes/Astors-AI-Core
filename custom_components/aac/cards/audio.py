"""Audio cards (docs/v2): Microphone → (Noise gate) → Pause cut → Speech to text → Voice chat ⇄ the line, Voice chat
→ Voice out (speaks at the satellite; its status output tells the Voice chat when it finished: a follow-up window).
Raw audio flows on `audio` wires as a stream (never a message); a Pause cut makes pieces (`piece` wires); Speech to
text turns a piece into a message."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from ..audio import SAMPLE_RATE, Segmenter, level, make_vad
from ..message import Message
from . import Card, register
from .chat import Chat

SILENT = ("nothing", "nothing.", "")


@register
class Microphone(Card):
    """A satellite's audio."""

    type = "microphone"
    title = "Microphone"
    category = "Audio"
    help = "The audio of one satellite (or any): the start of an audio chain. Wire it into a Noise gate or a Pause cut."
    inputs: dict[str, str] = {}
    outputs = {"out": "audio"}
    settings_schema = {"source": {"type": "source", "title": "Listens to", "default": "any",
                                  "help": "One satellite (each its own chain), or any of them."}}

    def hear(self, chunk: bytes, meta: dict[str, Any]) -> None:
        """Audio from the hub."""
        self.domain.flow.stream(self.id, "out", chunk, meta)


@register
class NoiseGate(Card):
    """Only sound above a level."""

    type = "noise_gate"
    title = "Noise gate"
    category = "Audio"
    help = "Lets audio through only above a loudness level (fan hum, a TV far away stay out); below it, silence."
    inputs = {"in": "audio"}
    outputs = {"out": "audio"}
    settings_schema = {"level": {"type": "number", "title": "Opens at (0–100)", "default": 25,
                                 "help": "The Audio page shows each satellite's live level."},
                       "hold": {"type": "number", "title": "Stays open (ms)", "default": 300}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._open: dict[str, datetime] = {}

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        now, key = dt_util.utcnow(), meta.get("source", "")
        if level(chunk) >= float(self.settings.get("level") or 25):
            self._open[key] = now + timedelta(milliseconds=int(self.settings.get("hold") or 300))
        passes = now <= self._open.get(key, now - timedelta(seconds=1))
        self.domain.flow.stream(self.id, "out", chunk if passes else bytes(len(chunk)), meta)


@register
class PauseCut(Card):
    """Speech detection."""

    type = "pause_cut"
    title = "Pause cut"
    category = "Audio"
    help = "Speech detection: a stretch of speech that ends with a pause leaves as one piece. Wire it into Speech to text."
    inputs = {"in": "audio"}
    outputs = {"out": "piece"}
    settings_schema = {"pause": {"type": "number", "title": "Pause that ends a piece (ms)", "default": 800,
                                 "help": "Shorter: quicker, but a pause mid-sentence cuts it. Longer: whole sentences."},
                       "longest": {"type": "number", "title": "Longest piece (s)", "default": 30}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._cuts: dict[str, Segmenter] = {}

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        key = meta.get("source", "")
        seg = self._cuts.get(key)
        if seg is None:
            seg = self._cuts[key] = Segmenter(make_vad(), int(self.settings.get("pause") or 800), int(self.settings.get("longest") or 30))
        for pcm in seg.feed(chunk):
            seconds = round(len(pcm) / (SAMPLE_RATE * 2), 1)
            self.domain.flow.piece(self.id, "out", {**meta, "pcm": pcm, "seconds": seconds, "end": dt_util.utcnow().isoformat()})


@register
class SpeechToText(Card):
    """A piece becomes text."""

    type = "speech_to_text"
    title = "Speech to text"
    category = "Audio"
    help = ("Turns each piece of speech into text: a message of its own (the text, the satellite, the time), tagged "
            "'heard'. The engines and their order are on the Audio page.")
    inputs = {"in": "piece"}
    outputs = {"out": "text"}
    tags = {"heard": "what was said (data: the satellite)"}
    settings_schema = {"language": {"type": "text", "title": "Language (empty: detect)", "default": ""}}

    async def piece(self, port: str, piece: dict[str, Any]) -> None:
        heard = await self.core.audio.stt(piece["pcm"], self.settings.get("language") or None)
        text = ((heard or {}).get("text") or "").strip()
        if not text:
            return
        message = Message(origin=self.id)
        message.add(self.id, "said", text, ["heard"], mic=piece.get("source"), mic_name=piece.get("name"),
                    area=piece.get("area"), seconds=piece.get("seconds"), language=(heard or {}).get("language"))
        await self.domain.flow.send(message, self.id, "out")


@register
class VoiceChat(Chat):
    """A spoken conversation."""

    type = "voice_chat"
    title = "Voice chat"
    category = "In and out"
    help = ("A spoken conversation: heard lines in (from Speech to text), out at 'said' with the conversation so far; the "
            "answer back into 'answers' is kept and goes on to 'speak' (Voice out). Wire Voice out's status back into "
            "'status': lines heard right after AAC spoke are tagged follow_up.")
    inputs = {"heard": "text", "answers": "text", "status": "text"}
    outputs = {"said": "text", "speak": "text"}
    tags = {"heard": "a spoken line", "follow_up": "heard right after AAC spoke"}
    settings_schema = {
        "new_after": {"type": "number", "title": "A new chat after quiet (minutes)", "default": 20},
        "follow_up": {"type": "number", "title": "Follow-up window (s)", "default": 10},
    }

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._windows: dict[str, datetime] = {}
        self._sessions: dict[str, str] = {}

    def _session(self, mic: str, name: str) -> dict[str, Any]:
        current = self.get(self._sessions.get(mic, ""))
        quiet = timedelta(minutes=float(self.settings.get("new_after") or 20))
        if current is None or dt_util.utcnow() - datetime.fromisoformat(current["updated"]) > quiet:
            current = self._new(f"Voice · {name} · {dt_util.now().strftime('%H:%M')}")
        self._sessions[mic] = current["id"]
        return current

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        if port == "status":  # Voice out finished: a follow-up window opens at that satellite
            signal = message.last("signal")
            if signal and signal["text"] == "finished":
                self._windows[signal["data"].get("mic", "")] = dt_util.utcnow() + timedelta(seconds=float(self.settings.get("follow_up") or 10))
            return []
        if port == "answers":
            if message.origin != self.id:
                return []
            chat = self.get(message.chat_id or "")
            if chat is not None:
                chat["entries"] = [e for e in message.entries if e["kind"] != "system"]
                if message.answer.strip().lower() not in SILENT:
                    self._add(chat, "aac", message.answer)
                await self.save()
                self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
            return ["speak"]
        said = message.last("said")  # a heard line (from Speech to text)
        if said is None:
            return []
        mic = said["data"].get("mic") or ""
        chat = self._session(mic, said["data"].get("mic_name") or mic)
        tags = [*said["tags"], self.tag("heard")]
        if dt_util.utcnow() <= self._windows.get(mic, dt_util.utcnow() - timedelta(seconds=1)):
            tags.append(self.tag("follow_up"))
        line = {**said, "tags": tags}
        out = Message(entries=[*[e for e in chat["entries"] if e["kind"] != "system"], line], origin=self.id, chat_id=chat["id"])
        self._add(chat, "user", said["text"], mic=said["data"].get("mic_name"))
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
        await self.domain.flow.send(out, self.id, "said")
        return []


@register
class VoiceOut(Card):
    """Speaks."""

    type = "voice_out"
    title = "Voice out"
    category = "Audio"
    help = ("Speaks the newest answer at the satellite the spoken line came from (the voice and volume are on the Audio "
            "page). Its status output sends 'finished' when it is done (wire it into a Voice chat's status).")
    inputs = {"in": "text"}
    outputs = {"status": "text"}
    tags = {"finished": "it stopped speaking (data: the satellite)"}
    settings_schema = {"source": {"type": "source", "title": "Speaks at", "default": "",
                                  "help": "Empty: where the spoken line came from."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._speaking: set[str] = set()

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        text = message.answer.strip()
        said = next((e for e in reversed(message.entries) if e["kind"] == "said" and e["data"].get("mic")), None)
        mic = self.settings.get("source") or (said["data"].get("mic") if said else None)
        if mic and text and text.lower() not in SILENT:
            language = (said or {}).get("data", {}).get("language")
            if await self.core.audio.say(mic, text, language):
                self._speaking.add(mic)
        return []

    def finished(self, mic: str) -> None:
        """The satellite finished playing."""
        if mic not in self._speaking:
            return
        self._speaking.discard(mic)
        message = Message(origin=self.id)
        message.add(self.id, "signal", "finished", ["finished"], mic=mic)
        self.hass.async_create_background_task(self.domain.flow.send(message, self.id, "status"), f"aac {self.id} status")
