"""The Microphone card (docs/v2 page 2): The audio of one satellite (or any): the start of an audio chain. Wire it into
a Noise gate or an Audio clipper."""

from __future__ import annotations

from array import array
from typing import Any

from custom_components.aac.api import SAMPLE_RATE, Card, register

VERSION = "3.1.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)




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
    needs = {"audio"}  # the satellites' microphones, at read
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
        try:
            self.listen(self.hear)
        except PermissionError as err:  # hears nothing until the owner allows it
            self.log(str(err), "warning", what="permission")

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
        self.stream_out("out", chunk, meta)

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
        if action not in ("test", "recent"):
            return await super().ui_call(action, args)
        return await self.core.audio.waveforms(self.settings.get("source"), self.settings.get("echo_from") or "",
                                               test=action == "test", seconds=float(args.get("seconds") or 5))

