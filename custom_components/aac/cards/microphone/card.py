"""The Microphone card (docs/v2 page 2): The audio of one satellite (or any): the start of an audio chain. Wire it into
a Noise gate or an Audio clipper."""

from __future__ import annotations

from array import array
import asyncio
import base64
import math
from typing import Any

from custom_components.aac.api import SAMPLE_RATE, Card, pcm_to_wav, register

VERSION = "2.10.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)


def _test_sound() -> bytes:
    """A test sound for lining up: three short 1 kHz beeps (sharp starts that live through Bluetooth's speech codec),
    then a slow sweep from 100 to 500 Hz (its waves can be told apart when zoomed in; 1.4 s, 16 kHz PCM)."""
    out = array("h")
    beep = int(0.025 * SAMPLE_RATE)
    for _ in range(3):
        for i in range(beep):  # 25 ms at 1 kHz with soft ends (no clicks of its own), then silence up to 0.2 s
            edge = min(1.0, i / 40, (beep - i) / 40)
            out.append(int(16000 * edge * math.sin(2 * math.pi * 1000 * i / SAMPLE_RATE)))
        out.extend([0] * (int(0.2 * SAMPLE_RATE) - beep))
    phase, length = 0.0, int(0.8 * SAMPLE_RATE)
    for i in range(length):
        phase += 2 * math.pi * (100 + 400 * i / length) / SAMPLE_RATE
        out.append(int(14000 * math.sin(phase)))
    return out.tobytes()


@register
class Microphone(Card):
    """A satellite's audio."""

    type = "microphone"
    title = "Microphone"
    category = "Audio"
    help = "The audio of one satellite (or any): the start of an audio chain. Wire it into a Noise gate or an Audio clipper."
    inputs: dict[str, str] = {}
    outputs = {"out": "audio"}
    ports_help = {"out": "the satellite's live audio (a stream; the wire swells with the loudness)"}
    settings_schema = {"source": {"type": "source", "title": "Listens to", "default": "any",
                                  "help": "One satellite (each its own chain), or any of them."},
                       "speaking": {"type": "select", "title": "While AAC speaks there", "default": "mute",
                                    "options": [["mute", "mute (it would hear itself)"], ["listen", "keep listening"]],
                                    "help": "Keep listening only with echo cancellation (the satellite's echo_cancel, or a "
                                            "speakerphone that does it itself, or the subtraction below): then a stop word "
                                            "works while AAC speaks."},
                       "echo": {"type": "bool", "title": "Subtract what the speaker plays", "default": False, "fold": "Echo",
                                "help": "For a satellite that sends what its speaker plays (its reference option): that sound, "
                                        "delayed, scaled and maybe inverted, is taken out of the microphone. Line them up in "
                                        "the waveforms."},
                       "echo_from": {"type": "source", "title": "Subtract the speaker of", "default": "", "fold": "Echo",
                                     "empty": "the same satellite",
                                     "help": "Whose speaker it takes out: the satellite it listens to, or another one that sends "
                                             "what its speaker plays (two devices drift apart a little; one device holds best)."},
                       "echo_delay": {"type": "number", "title": "Delay (ms)", "default": 0, "fold": "Echo",
                                      "help": "How much later the speaker's sound reaches the microphone (negative: earlier). "
                                              "Steps of 1/16 ms (one sample)."},
                       "echo_gain": {"type": "number", "title": "Gain", "default": 1, "fold": "Echo",
                                     "help": "How loud the speaker's sound arrives at the microphone, compared to what it plays."},
                       "echo_invert": {"type": "bool", "title": "Inverted", "default": False, "fold": "Echo",
                                       "help": "The speaker's sound arrives upside down (then it is added instead)."},
                       "echo_view": {"type": "waveforms", "title": "Waveforms", "fold": "Echo"}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._history: dict[str, array] = {}  # per satellite: what came before (the delayed side of the subtraction)
        self._other = bytearray()  # another satellite's speaker, as it came, until the microphone takes it

    async def start(self) -> None:
        self.core.subscribe("audio", self, self.hear)

    def hear(self, chunk: bytes, meta: dict[str, Any]) -> None:
        """Audio from the core's "audio" channel (only the satellite it listens to; muted while AAC speaks there unless
        it keeps listening); with the subtraction on, what the speaker played is taken out first."""
        other = self.settings.get("echo_from") or ""
        if self.settings.get("echo") and other and other == meta["source"] and meta.get("reference"):
            self._other += meta["reference"]  # the other satellite's speaker, for the microphone's next chunks
            del self._other[: max(0, len(self._other) - SAMPLE_RATE * 2)]
        if self.settings.get("source") not in ("any", meta["source"], None, ""):
            return
        if self.settings.get("echo"):
            if other and other != meta["source"]:
                reference = bytes(self._other[: len(chunk)]).ljust(len(chunk), b"\0")
                del self._other[: len(chunk)]
                chunk = self._subtract(chunk, reference, meta["source"])
            elif meta.get("reference"):
                chunk = self._subtract(chunk, meta["reference"], meta["source"])
        if meta.get("speaking") and (self.settings.get("speaking") or "mute") == "mute":
            return
        self.domain.flow.stream(self.id, "out", chunk, meta)

    def _subtract(self, chunk: bytes, reference: bytes, source: str) -> bytes:
        """microphone − gain × the speaker's sound, shifted by the delay (whole samples; a negative one delays the
        microphone instead)."""
        mic, ref = array("h", chunk), array("h", reference)
        n, d = len(mic), round(float(self.settings.get("echo_delay") or 0) * SAMPLE_RATE / 1000)
        gain = float(self.settings.get("echo_gain") or 0) * (-1 if self.settings.get("echo_invert") else 1)
        late, early = (ref, mic) if d >= 0 else (mic, ref)  # the side that is delayed, the side taken as it comes
        past = self._history.get(source, array("h")) + late
        lag = abs(d)
        past = array("h", bytes(2 * max(0, n + lag - len(past)))) + past  # silence before the first samples
        shifted, self._history[source] = past[len(past) - n - lag: len(past) - lag], past[-(lag + n):]
        mic_part, ref_part = (early, shifted) if d >= 0 else (shifted, early)
        return array("h", (max(-32768, min(32767, int(m - gain * r))) for m, r in zip(mic_part, ref_part))).tobytes()

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """The waveforms view: "recent" (the satellite's last seconds: microphone and what its speaker played), "test"
        (play a test sound there and send back the seconds it took)."""
        hub = self.core.audio
        if action not in ("test", "recent"):
            return await super().ui_call(action, args)
        speaker = self.settings.get("echo_from") or ""
        source = self.settings.get("source")
        if source in ("any", None, ""):  # any: the speaker's satellite, else the first that sends what its speaker plays
            source = speaker or next((s.id for s in hub.sources.values() if s.reference), None)
        speaker = speaker or source
        if source not in hub.sources:
            raise ValueError("the satellite it listens to is not connected")
        if speaker not in hub.sources or not hub.sources[speaker].reference:
            raise ValueError("that satellite does not send what its speaker plays (its reference option)")
        if action == "test":
            sound = _test_sound()
            await hub.play(speaker, pcm_to_wav(sound), "")
            for _ in range(120):  # until it has played (at most 6 s)
                await asyncio.sleep(0.05)
                if not hub.sources[speaker].playing:
                    break
            await asyncio.sleep(0.3)  # the tail of the sound reaches the microphone
            seconds = len(sound) / 2 / SAMPLE_RATE + 1.5  # room for a long way back (Bluetooth: several hundred ms)
        else:
            seconds = float(args.get("seconds") or 5)
        mic, ref = hub.recent(source, seconds) or {}, hub.recent(speaker, seconds) or {}
        return {"source": source, "speaker": speaker, "names": [hub.sources[source].name, hub.sources[speaker].name],
                "rate": SAMPLE_RATE, "mic": base64.b64encode(mic.get("mic", b"")).decode(),
                "reference": base64.b64encode(ref.get("reference", b"")).decode()}

