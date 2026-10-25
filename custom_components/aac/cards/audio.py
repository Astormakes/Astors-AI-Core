"""Audio cards (docs/v2): Microphone → (Noise gate) → Audio clipper → Speech to text → … → Text to speech → Speaker (at the
satellite). Raw audio flows on `audio` wires as a stream (never a context); an Audio clipper makes clips (`clip` wires);
Speech to text turns a clip into a context. When AAC last spoke is the core's (templates: {{ audio.… }})."""

from __future__ import annotations

from array import array
import asyncio
import base64
from datetime import datetime, timedelta
import math
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import (CARD_TYPES, LOGGER, SAMPLE_RATE, Card, Context, Segmenter, level, make_vad, new_id, pcm_to_wav,
                                      register, resample, wav_to_pcm)

VERSION = "2.10.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

SILENT = ("nothing", "nothing.", "")
SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written
STREAM_PIECE = 2  # seconds of a live stream the Speaker plays at a time
JOIN_WAIT = 1.5  # seconds the Phrase joiner waits for a phrase that may bring no text
COLUMN = 290  # one column in the grid (the panel's card width and gap)


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
            seconds = len(sound) / 2 / SAMPLE_RATE + 1.0
        else:
            seconds = float(args.get("seconds") or 5)
        mic, ref = hub.recent(source, seconds) or {}, hub.recent(speaker, seconds) or {}
        return {"source": source, "speaker": speaker, "names": [hub.sources[source].name, hub.sources[speaker].name],
                "rate": SAMPLE_RATE, "mic": base64.b64encode(mic.get("mic", b"")).decode(),
                "reference": base64.b64encode(ref.get("reference", b"")).decode()}


def _test_sound() -> bytes:
    """A test sound for lining up: three clicks, then a slow sweep from 100 to 500 Hz (its waves can be told apart when
    zoomed in; 1.4 s, 16 kHz PCM)."""
    out = array("h")
    for _ in range(3):
        out.extend([20000, -20000] + [0] * 3198)  # a click, then 0.2 s of silence
    phase, length = 0.0, int(0.8 * SAMPLE_RATE)
    for i in range(length):
        phase += 2 * math.pi * (100 + 400 * i / length) / SAMPLE_RATE
        out.append(int(14000 * math.sin(phase)))
    return out.tobytes()


@register
class NoiseGate(Card):
    """Only sound above a level."""

    type = "noise_gate"
    title = "Noise gate"
    category = "Audio"
    help = "Lets audio through only above a loudness level (fan hum, a TV far away stay out); below it, silence."
    inputs = {"in": "audio"}
    outputs = {"out": "audio"}
    ports_help = {"in": "live audio", "out": "the same audio, silence below the level"}
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
    title = "Audio clipper"
    category = "Audio"
    help = ("Speech detection: a stretch of speech that ends with a pause leaves as one clip. Wire it into Speech to text. "
            "With a phrase pause it hands out each phrase at once (Speech to text works while you still speak); a Phrase "
            "joiner after Speech to text puts the sentence together again. 'cuts' says where it cuts.")
    inputs = {"in": "audio"}
    outputs = {"out": "clip", "cuts": "text"}
    ports_help = {"in": "live audio",
                  "out": "one clip per stretch of speech that ends with a pause (with a phrase pause: one per phrase, "
                         "tagged with its sentence and number)",
                  "cuts": "where it cuts, as small contexts (a signal entry tagged start / phrase / end; data: the sentence, "
                          "the parts, the satellite): for a Phrase joiner, a Level card, or any card"}
    tags = {"start": "speech began (data: ago = how many seconds back)", "phrase": "a phrase was cut (data: part)",
            "end": "the sentence is done (data: parts)"}
    settings_schema = {"pause": {"type": "number", "title": "Pause that ends a clip (ms)", "default": 800,
                                 "help": "Shorter: quicker, but a pause mid-sentence cuts it. Longer: whole sentences."},
                       "longest": {"type": "number", "title": "Longest clip (s)", "default": 30},
                       "phrase": {"type": "number", "title": "Phrase pause (ms): faster speech to text", "default": 0,
                                  "min": 100, "off": 0,
                                  "confirm": "This adds a Phrase joiner after Speech to text and moves the cards to its "
                                             "right one column over: the joiner takes over Speech to text's wires. OK?",
                                  "help": "0 = off: Speech to text only starts once you have finished (after the pause "
                                          "above). E.g. 250: whenever you pause briefly in the middle (a breath, a "
                                          "comma), the part so far goes to Speech to text at once, so most of the text "
                                          "is ready when you finish. A Phrase joiner puts the parts back into one "
                                          "sentence: setting this adds one after Speech to text and wires it for you."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._cuts: dict[str, Segmenter] = {}
        self._utterance: dict[str, str] = {}  # satellite → the sentence its phrases belong to
        self._aac_spoke: dict[str, bool] = {}  # satellite → AAC was speaking there when this speech began
        self._parts: dict[str, int] = {}  # sentence → phrases sent

    async def start(self) -> None:
        if int(self.settings.get("phrase") or 0) > 0 and not self._joined():
            self.hass.async_create_task(self._add_joiner())  # after this card is set up

    def _cut(self, what: str, meta: dict[str, Any], **data: Any) -> None:
        """A cut goes out at 'cuts' as a small context: one signal entry tagged start / phrase / end."""
        context = Context(origin=self.id)
        context.add(self.id, "signal", f"cut: {what}", [what], cut=what, source=meta.get("source"), name=meta.get("name"), **data)
        self.hass.async_create_background_task(self.domain.flow.send(context, self.id, "cuts"), f"aac {self.id} cut")

    def _joined(self) -> bool:
        """A card at its cuts that also takes heard lines puts phrases together (a Phrase joiner, or one of your own).
        Read from the domain's setup, not its running cards: while a setup is made (start, Undo), not all are there yet."""
        setup = self.domain.export()["cards"]
        def takes_lines(card_id: str) -> bool:
            if (card := self.domain.cards.get(card_id)) is not None:
                return "lines" in card.ports()[0]
            kind = CARD_TYPES.get(setup.get(card_id, {}).get("type", ""))
            return kind is not None and "lines" in kind.inputs
        return any(w["from"] == f"{self.id}.cuts" and takes_lines(w["to"].split(".")[0]) for w in self.domain.wires)

    async def _add_joiner(self) -> None:
        """Phrases need their sister card: a Phrase joiner after the cards it feeds (Speech to text), which takes over
        their outgoing wires; its 'cuts' come from here."""
        d = self.domain
        if self._joined():
            return
        fed = [w["to"].split(".")[0] for w in d.wires if w["from"] == f"{self.id}.out"]
        for w in d.wires:  # a joiner already behind Speech to text (only its cuts wire was lost): wire that one
            target = d.cards.get(w["to"].split(".")[0])
            if w["from"].split(".")[0] in fed and target is not None and {"lines", "cuts"} <= set(target.ports()[0]):
                d.set_wires([*d.wires, {"from": f"{self.id}.cuts", "to": f"{target.id}.cuts"}])
                return
        at = dict(d.positions.get(fed[0] if fed else self.id, {"x": 0, "y": 0}))
        for card_id, p in d.positions.items():  # room: what is right of Speech to text moves one column over
            if p["x"] > at["x"] and card_id != self.id:
                p["x"] += COLUMN
        joiner = await d.add_card("phrase_joiner", "Phrase joiner", at={"x": at["x"] + COLUMN, "y": at["y"]})
        wires = [{"from": f"{self.id}.cuts", "to": f"{joiner.id}.cuts"}]
        for w in d.wires:
            if w["from"].split(".")[0] in fed and w["from"].endswith(".out"):  # what Speech to text fed, the joiner feeds now
                wires.append({"from": f"{joiner.id}.out", "to": w["to"]})
            else:
                wires.append(w)
        wires += [{"from": f"{c}.out", "to": f"{joiner.id}.lines"} for c in fed]
        d.set_wires(wires)

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        key = meta.get("source", "")
        seg = self._cuts.get(key)
        if seg is None:
            seg = self._cuts[key] = Segmenter(make_vad(), int(self.settings.get("pause") or 800), int(self.settings.get("longest") or 30),
                                              int(self.settings.get("phrase") or 0))
        for kind, audio in seg.feed_parts(chunk):
            if kind == "start":  # speech began (`audio` frames ago); while AAC spoke there it is mostly AAC's own voice
                self._utterance[key] = utterance = new_id()
                self._aac_spoke[key] = bool(meta.get("speaking"))
                self._cut("start", meta, utterance=utterance, ago=audio / 100)
                continue
            utterance = self._utterance.get(key)
            if kind == "end":  # the sentence is done: how many parts it had
                self.core.audio.mark(meta.get("source"), "speech ended")
                self._parts.pop(utterance, None)
                self._cut("end", meta, utterance=self._utterance.pop(key, None), parts=audio)
                continue
            pcm = b"".join(audio) if kind == "piece" else audio
            clip = {**meta, "pcm": pcm, "seconds": round(len(pcm) / (SAMPLE_RATE * 2), 1), "end": dt_util.utcnow().isoformat(),
                    "aac_spoke": self._aac_spoke.get(key, False)}
            if kind == "phrase":
                self._parts[utterance] = self._parts.get(utterance, 0) + 1
                clip |= {"utterance": utterance, "part": self._parts[utterance]}
                self._cut("phrase", meta, utterance=utterance, part=self._parts[utterance])
            self.domain.flow.clip(self.id, "out", clip)


@register
class SpeechToText(Card):
    """A clip becomes text."""

    type = "speech_to_text"
    title = "Speech to text"
    category = "Audio"
    help = ("Turns each audio clip into text: a context of its own (the text, the satellite, the time), tagged "
            "'heard'. The engines and their order are on the Audio page.")
    inputs = {"in": "clip"}
    outputs = {"out": "text"}
    ports_help = {"in": "audio clips (e.g. speech from an Audio clipper)",
                  "out": "round: every wire gets each heard line as a new context (data: mic = the satellite, mic_name, "
                         "language, aac_spoke = it began while AAC spoke there: mostly AAC's own voice), e.g. into several "
                         "Conditionals side by side"}
    tags = {"heard": "what was said (data: the satellite)"}
    settings_schema = {"language": {"type": "text", "title": "Language (empty: the home's)", "default": "",
                                    "help": "A code like de. Empty: the home's languages from the Audio page (one: that one; "
                                            "several: detected among them)."},
                       "models": {"type": "modellist", "tag": "speech to text", "title": "Models (in order)", "default": [],
                                  "help": "The list is run through from the top: a free model takes it; a busy one queues it if its Queue is ticked, then the next is tried. Empty: every model with the tag, queued. HA's engines on the Audio page come after."}}

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if not clip.get("pcm"):  # a stop clip
            return
        entries = self.core.pool.entries(self.settings.get("models"), "speech to text")
        pcm = resample(clip["pcm"], int(clip.get("rate") or SAMPLE_RATE))
        heard = await self.core.audio.stt(pcm, self.settings.get("language") or None, entries)
        text = ((heard or {}).get("text") or "").strip()
        if not text:
            return
        if not clip.get("utterance"):  # a phrase's text is not the turn's yet (the Phrase joiner says when it is)
            self.core.audio.mark(clip.get("source"), "text ready")
        context = Context(origin=self.id)
        context.add(self.id, "said", text, ["heard"], mic=clip.get("source"), mic_name=clip.get("name"),
                    utterance=clip.get("utterance"), part=clip.get("part"), aac_spoke=bool(clip.get("aac_spoke")),
                    area=clip.get("area"), seconds=clip.get("seconds"), language=(heard or {}).get("language"))
        await self.domain.flow.send(context, self.id, "out")


@register
class TextToSpeech(Card):
    """An answer becomes a clip."""

    type = "text_to_speech"
    title = "Text to speech"
    category = "Audio"
    help = ("Turns the answers of a turn into audio clips, sentence by sentence as they are written, in a voice per "
            "language (the language it was spoken to in). A clip remembers the satellite the spoken line came from; wire "
            "it into a Speaker. An answer 'nothing' stays silent; a stop arriving here goes on to the Speaker as a stop.")
    inputs = {"in": "text"}
    outputs = {"out": "clip"}
    ports_help = {"in": "a context: the answers of its newest turn are spoken (while they are written: each finished sentence)",
                  "out": "each spoken sentence as a clip (data: the satellite it was heard at, the text, the language); a stop "
                         "goes out as a stop clip"}
    settings_schema = {"engine": {"type": "tts_engine", "title": "Voice engine", "default": "",
                                  "help": "A Home Assistant text-to-speech engine. Empty: the default on the Audio page."},
                       "voices": {"type": "voices", "title": "Voice per language", "default": {},
                                  "help": "For each home language (Audio page). Not set: the engine's default voice."}}
    functions = {
        "voices": {"help": "The voices it can use for each home language, and the one set."},
        "set_voice": {"help": "Speak a language in another voice.",
                      "args": {"language": {"type": "string", "help": "A language code, e.g. de.", "required": True},
                               "voice": {"type": "string", "help": "A voice's name or id (from voices); empty: the engine's default.",
                                         "required": True}}},
    }

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._spoken: dict[str, int] = {}  # answer entry → how much of its text is spoken
        self._last: asyncio.Future | None = None  # the newest sentence on its way (they go out in order)
        self._round = 0  # a stop starts a new round: sentences of the old one are dropped

    @property
    def busy(self) -> bool:
        return self._last is not None and not self._last.done()

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        self._speak(context, finished=False)
        return []

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        self._speak(context, finished=True)
        if self._last:
            await self._last
        return []

    def _speak(self, context: Context, finished: bool) -> None:
        """Queue what is newly finished in the answers of the newest turn (all of it once the turn is finished)."""
        said = max((i for i, e in enumerate(context.entries) if e["kind"] == "said"), default=-1)
        heard = (context.entries[said]["data"] if said >= 0 else {})
        for e in context.entries[said + 1:]:
            if e["kind"] not in ("answer", "error") or e.get("state") == "cut":  # an error is said too (nothing came)
                continue
            text, upto = e["text"], self._spoken.get(e["id"], 0)
            if finished or e.get("state") == "closed":
                end = len(text)
            else:
                ends = [m.end() for m in SENTENCE.finditer(text)]
                end = ends[-1] if ends else 0
            if end <= upto:
                continue
            self._spoken[e["id"]] = end
            if text.strip().lower() not in SILENT and (part := text[upto:end].strip()):
                self.core.audio.mark(heard.get("mic"), "first sentence")
                self._queue(part, heard)
        while len(self._spoken) > 50:  # only recent turns matter
            self._spoken.pop(next(iter(self._spoken)))

    def _queue(self, text: str, heard: dict[str, Any]) -> None:
        before, round_ = self._last, self._round

        async def in_order() -> None:
            wav = await self.core.audio.tts(text, heard.get("language"), self.settings.get("engine") or None, self.settings.get("voices"))
            if before:
                await before
            if not wav or round_ != self._round:
                return
            self.core.audio.mark(heard.get("mic"), "voice ready")
            pcm, rate = wav_to_pcm(wav)
            self.domain.flow.clip(self.id, "out", {"pcm": pcm, "rate": rate, "seconds": round(len(pcm) / 2 / rate, 1), "text": text,
                                                   "source": heard.get("mic"), "name": heard.get("mic_name"), "language": heard.get("language")})
        self._last = asyncio.ensure_future(in_order())

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        self._round += 1  # sentences still being made are dropped
        self.domain.flow.clip(self.id, "out", {"stop": True})  # the Speaker stops what it plays
        return False

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "voices":
            mine = self.settings.get("voices") or {}
            return {code: {"voices": [v["name"] for v in got["voices"]], "set": next((v["name"] for v in got["voices"]
                    if v["id"] == mine.get(code, {}).get("voice")), "the engine's default")}
                    for code, got in self.core.audio.voices(self.settings.get("engine") or None).items()}
        if function == "set_voice":
            code, wanted = str(args.get("language") or "").lower()[:3], str(args.get("voice") or "").strip()
            got = self.core.audio.voices(self.settings.get("engine") or None).get(code)
            if got is None:
                return {"error": f"{code} is not a home language"}
            voices = dict(self.settings.get("voices") or {})
            if not wanted:
                voices.pop(code, None)
            else:
                voice = next((v for v in got["voices"] if wanted.lower() in (v["id"].lower(), v["name"].lower())), None)
                if voice is None:
                    return {"error": f"no voice {wanted} for {code}", "voices": [v["name"] for v in got["voices"]]}
                voices[code] = {"language": voice["locale"], "voice": voice["id"]}
            await self.domain.update_card(self.id, settings={"voices": voices})  # the card is made again with it
            return {"done": "set_voice", "language": code, "voice": wanted or "the engine's default"}
        return await super().call(function, args, context)


@register
class Speaker(Card):
    """Plays sound."""

    type = "speaker"
    title = "Speaker"
    category = "Audio"
    help = ("Plays audio clips (a spoken answer, a sound) or a live audio stream: at the satellite a clip comes from, or "
            "at a satellite or Home Assistant media player you pick. A stop clip, or Stop, cuts it off. The volume is on "
            "the Audio page.")
    inputs = {"in": "audio|clip"}
    outputs: dict[str, str] = {}
    ports_help = {"in": "clips or a live stream (half green, half purple: it takes both)"}
    settings_schema = {"source": {"type": "player", "title": "Plays at", "default": "",
                                  "help": "Empty: the satellite the clip came from. Or a satellite or any media player."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._played_at: set[str] = set()  # where it played (a stop cuts them)
        self._buffers: dict[str, bytearray] = {}  # a live stream, gathered into short clips per satellite

    async def clip(self, port: str, clip: dict[str, Any]) -> None:
        if clip.get("stop"):
            await self.stop()
            return
        where = self.settings.get("source") or clip.get("source")
        if not where or not clip.get("pcm"):
            return
        await self._play(where, pcm_to_wav(clip["pcm"], int(clip.get("rate") or SAMPLE_RATE)), clip.get("text", ""), queue=True)

    def stream(self, port: str, chunk: bytes, meta: dict[str, Any]) -> None:
        """A live stream: played in short pieces (satellites take whole clips)."""
        where = self.settings.get("source") or meta.get("source")
        if not where:
            return
        buf = self._buffers.setdefault(where, bytearray())
        buf.extend(chunk)
        if len(buf) >= SAMPLE_RATE * 2 * STREAM_PIECE:
            wav, buf[:] = pcm_to_wav(bytes(buf)), b""
            self.hass.async_create_background_task(self._play(where, wav, ""), f"aac {self.id} stream")

    async def _play(self, where: str, wav: bytes, text: str, queue: bool = False) -> None:
        self._played_at.add(where)
        if not where.startswith("media_player."):
            await self.core.audio.play(where, wav, text, queue=queue)  # clips one after another, a stream at once
            return
        try:
            await self.hass.services.async_call("media_player", "play_media", {
                "entity_id": where, "media_content_id": self.core.audio.clip_url(wav), "media_content_type": "music",
                "announce": True}, blocking=True)
        except Exception as err:  # noqa: BLE001 - a speaker that fails must not break the chain
            LOGGER.warning("AAC: playing on %s failed: %s", where, err)

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        busy = False
        for where in list(self._played_at):
            if where.startswith("media_player."):
                if (state := self.hass.states.get(where)) and state.state == "playing":
                    await self.hass.services.async_call("media_player", "media_stop", {"entity_id": where}, blocking=True)
                    busy = True
            elif await self.core.audio.stop(where):
                busy = True
        self._played_at.clear()
        self._buffers.clear()
        return busy

@register
class PhraseJoiner(Card):
    """Puts phrases together."""

    type = "phrase_joiner"
    title = "Phrase joiner"
    category = "Audio"
    help = ("Puts a sentence together again that an Audio clipper sent in phrases (its phrase pause): the heard lines "
            "from Speech to text come in at 'lines', the clipper's 'cuts' say when the sentence is done and how many "
            "parts it had; then the whole sentence goes on as one line. Lines without phrase tags go straight on.")
    inputs = {"lines": "text", "cuts": "text"}
    outputs = {"out": "text"}
    ports_help = {"lines": "heard lines from Speech to text (one per phrase)",
                  "cuts": "the Audio clipper's 'cuts': it acts on 'the sentence is done, with how many parts'",
                  "out": "the whole sentence as one heard line (data: the satellite, the language, the phrases)"}
    tags = {"heard": "a whole sentence put together from phrases"}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._parts: dict[str, dict[int, dict[str, Any]]] = {}  # sentence → its heard phrases by number
        self._expected: dict[str, int] = {}  # sentence → how many phrases (when it is done)
        self._waits: dict[str, Any] = {}  # sentence → the timer that sends it with what came

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if port == "cuts":  # the clipper's cuts: only "the sentence is done" counts
            cut = next((e["data"] for e in reversed(context.entries) if e["kind"] == "signal" and e["data"].get("cut")), {})
            if cut.get("cut") == "end" and cut.get("utterance"):
                await self._ended(cut["utterance"], int(cut.get("parts") or 0))
            return []
        said = context.last("said") or {}
        utterance = said.get("data", {}).get("utterance")
        if port != "lines" or not utterance:
            return None if port == "lines" else []  # not a phrase: straight on
        self._parts.setdefault(utterance, {})[int(said["data"].get("part") or 0)] = said
        await self._check(utterance)
        return []

    async def _ended(self, utterance: str, parts: int) -> None:
        self._expected[utterance] = parts
        await self._check(utterance)
        if utterance in self._expected:  # a phrase without text never comes: send what came after a moment
            self._waits[utterance] = self.hass.loop.call_later(JOIN_WAIT, lambda: self.hass.async_create_task(self._send(utterance)))

    async def _check(self, utterance: str) -> None:
        if utterance in self._expected and len(self._parts.get(utterance, {})) >= self._expected[utterance]:
            await self._send(utterance)

    async def _send(self, utterance: str) -> None:
        if (wait := self._waits.pop(utterance, None)) is not None:
            wait.cancel()
        self._expected.pop(utterance, None)
        parts = [p for _, p in sorted(self._parts.pop(utterance, {}).items())]
        if not parts:
            return
        first = parts[0]["data"]
        context = Context(origin=self.id)
        context.add(self.id, "said", " ".join(p["text"].strip() for p in parts), ["heard"],
                    **{k: first.get(k) for k in ("mic", "mic_name", "area", "language", "aac_spoke")},
                    seconds=round(sum(float(p["data"].get("seconds") or 0) for p in parts), 1), phrases=len(parts))
        self.core.audio.mark(first.get("mic"), "text ready")
        await self.domain.flow.send(context, self.id, "out")
