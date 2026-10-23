"""Audio cards (spec/graph.md "Audio cards", owner 2026-10-23): Hearing split into cards you wire yourself.

- Microphone: one satellite's (or any) audio, the start of a chain (raw audio is a stream, never a message).
- Noise gate: lets audio through only above a level (fan hum, a TV far away stay out).
- Pause cut: speech detection; a stretch of speech that ends with a pause leaves as one piece.
- Speech to text: a piece becomes a message of its own (the text, the satellite, the time); an answer that
  reaches the Voice card is spoken at that satellite.
All are templates under "Audio" in Add node. The old Hearing card keeps working next to them until it goes.
"""

from __future__ import annotations

from array import array
import math
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .request import TEXT, Message
from .streams import Node

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import AACConfigEntry

AUDIO, PIECE = "audio", "audio.piece"
SAMPLE_RATE = 16000

GATE_SETTINGS = {
    "level": {"type": "number", "title": "Opens at (0–100)", "default": 25,
              "help": "Loudness on a scale of 0 (silence) to 100 (full scale, about 60 dB of range); below it the "
                      "gate is closed. The audio pipeline page shows each microphone's live level."},
    "hold": {"type": "number", "title": "Stays open (ms)", "default": 300, "help": "After the sound drops below the level."},
}
CUT_SETTINGS = {
    "pause": {"type": "number", "title": "Pause that ends a piece (ms)", "default": 800,
              "help": "Shorter: quicker answers, but a pause in the middle of a sentence cuts it. Longer: whole "
                      "sentences, a slower start."},
    "longest": {"type": "number", "title": "Longest piece (s)", "default": 30},
}
STT_VARS = {"text": "What was said.", "mic": "The microphone's id.", "mic_name": "The microphone's name.",
            "area": "The microphone's area.", "start": "When the speech started (ISO time).", "seconds": "How long it was."}


def level(chunk: bytes) -> float:
    """Loudness of 16-bit mono audio: 0 = digital silence, 100 = full scale (about 60 dB of range)."""
    samples = array("h", chunk[: len(chunk) - len(chunk) % 2])
    if not samples:
        return 0.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return max(0.0, min(100.0, (20 * math.log10(rms / 32768) + 60) / 60 * 100)) if rms > 0 else 0.0


def audio_nodes(hass: HomeAssistant, entry: AACConfigEntry) -> list[Node]:
    """The audio card types."""
    from . import audio_hub  # noqa: PLC0415 - looked up when used (the hub imports the bus too)

    bus = entry.runtime_data.bus

    def mic_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        node = Node(id=inst_id, title=title, outputs={"out": AUDIO}, role="source", settings=settings, category="Audio",
                    description=MIC_HELP)
        node.listens_to = settings.get("source") or "any"  # the hub hands it the audio of this source
        return node

    def gate_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        opens = float(settings.get("level") or 25)
        hold = max(0, int(settings.get("hold") or 300)) / 1000
        open_until: dict[str, float] = {}  # per source: open until this time

        def stream(domain_id: str, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
            now = dt_util.utcnow().timestamp()
            key = meta.get("source", "")
            if level(chunk) >= opens:
                open_until[key] = now + hold
            # closed: silence of the same length (the pause cut after it still sees the pause)
            bus.stream(domain_id, inst_id, "out", chunk if now <= open_until.get(key, 0) else bytes(len(chunk)), meta)

        return Node(id=inst_id, title=title, inputs={"in": AUDIO}, outputs={"out": AUDIO}, settings=settings,
                    category="Audio", stream=stream, description=GATE_HELP)

    def cut_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        pause = max(100, int(settings.get("pause") or 800))
        longest = max(1, int(settings.get("longest") or 30))
        cuts: dict[str, Any] = {}  # per source: its speech detector

        def stream(domain_id: str, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
            key = meta.get("source", "")
            seg = cuts.get(key)
            if seg is None:
                seg = cuts[key] = audio_hub.Segmenter(audio_hub.make_vad())
                seg.end_silence = max(1, pause // 10)  # 10 ms frames
            for pcm in seg.feed(chunk):
                seconds = round(len(pcm) / (SAMPLE_RATE * 2), 1)
                if seconds > longest + 0.5:
                    pcm, seconds = pcm[: longest * SAMPLE_RATE * 2], float(longest)
                piece = {**meta, "pcm": pcm, "seconds": seconds,
                         "start": dt_util.utcnow().timestamp() - seconds - pause / 1000}
                bus.record(domain_id, inst_id, "out", {"text": f"{seconds} s of speech · {meta.get('name') or key}",
                                                       "source": key, "seconds": seconds})
                for reader, in_port in bus.wired_from(domain_id, inst_id, "out"):
                    if reader.run:
                        entry.async_create_background_task(hass, reader.run(domain_id, in_port, piece), f"aac cut {inst_id}")

        return Node(id=inst_id, title=title, inputs={"in": AUDIO}, outputs={"out": PIECE}, settings=settings,
                    category="Audio", stream=stream, description=CUT_HELP)

    def stt_make(inst_id: str, title: str, settings: dict[str, Any]) -> Node:
        async def run(domain_id: str, port: str, piece: dict[str, Any]) -> None:
            hub = entry.runtime_data.audio
            if hub is None:
                return
            heard = await hub.async_stt(piece["pcm"])
            text = ((heard or {}).get("text") or "").strip()
            if not text:
                return
            domain = entry.runtime_data.domain(domain_id)
            start = dt_util.utc_from_timestamp(piece.get("start") or dt_util.utcnow().timestamp()).isoformat()
            message = Message(domain=domain, kind="chat", trigger=f"heard: {piece.get('name') or piece.get('source')}",
                              title=f"Heard · {piece.get('name') or ''}", origin=inst_id, reply_to="voice",
                              language=(heard or {}).get("language") or hass.config.language, priority="normal")
            message.add(inst_id, "user", text, mic=piece.get("source"), mic_name=piece.get("name"))
            message.set_vars(inst_id, text=text, mic=piece.get("source"), mic_name=piece.get("name"),
                             area=piece.get("area"), start=start, seconds=piece.get("seconds"))
            await entry.runtime_data.flow.async_send(message, start=inst_id)

        return Node(id=inst_id, title=title, inputs={"in": PIECE}, outputs={"out": TEXT}, role="source", run=run,
                    settings=settings, category="Audio", variables=STT_VARS, description=STT_HELP)

    return [
        Node(id="microphone", title="Microphone", outputs={"out": AUDIO}, role="source", template=True, make=mic_make,
             category="Audio", description=MIC_HELP, settings_schema={
                 "source": {"type": "select", "title": "Listens to", "default": "any", "options_from": "sources",
                            "help": "One satellite or microphone (each its own chain), or any of them."}}),
        Node(id="noise_gate", title="Noise gate", inputs={"in": AUDIO}, outputs={"out": AUDIO}, template=True,
             make=gate_make, category="Audio", settings_schema=GATE_SETTINGS, description=GATE_HELP),
        Node(id="pause_cut", title="Pause cut", inputs={"in": AUDIO}, outputs={"out": PIECE}, template=True,
             make=cut_make, category="Audio", settings_schema=CUT_SETTINGS, description=CUT_HELP),
        Node(id="speech_to_text", title="Speech to text", inputs={"in": PIECE}, outputs={"out": TEXT}, role="source",
             template=True, make=stt_make, category="Audio", variables=STT_VARS, description=STT_HELP),
    ]


MIC_HELP = ("The audio of one satellite or microphone (or any of them): the start of an audio chain. Wire it into a "
            "Noise gate or a Pause cut.")
GATE_HELP = ("Lets audio through only above a loudness level, so fan hum or a TV far away do not keep starting "
             "pieces of speech. Below it the audio becomes silence.")
CUT_HELP = ("Speech detection: a stretch of speech that ends with a pause leaves as one piece. Set the pause: "
            "shorter answers quicker, longer keeps whole sentences. Wire it into Speech to text.")
STT_HELP = ("Turns each piece of speech into text, a message of its own (with the satellite and the time). Wire "
            "it into the line (Header …) like a chat; an answer that reaches the Voice card is spoken at that "
            "satellite. Values for templates: {{ <card id>.text }}, .mic_name, .area, .start, .seconds.")
