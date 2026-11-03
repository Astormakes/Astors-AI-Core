"""The Audio clipper card (docs/v2 page 2): Speech detection: a stretch of speech that ends with a pause leaves as one
clip. Wire it into Speech to text, and Speech to text's lines back into 'lines': with a phrase pause it hands out each
phrase at once (Speech to text works while you still speak) and puts the sentence together again from the lines that
come back; it sends it at 'said'. 'cuts' says where it cuts. Either half works alone."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import SAMPLE_RATE, Card, Context, Segmenter, make_vad, new_id, register

VERSION = "4.0.1"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

JOIN_WAIT = 1.5  # seconds it waits, once the sentence is done, for a phrase that may bring no text


@register
class PauseCut(Card):
    """Speech detection, and the sentence put together again."""

    type = "pause_cut"
    title = "Audio clipper"
    category = "Audio"
    help = ("Speech detection: a stretch of speech that ends with a pause leaves as one clip. Wire 'out' into Speech to "
            "text and Speech to text back into 'lines'; what was said leaves at 'said'. With a phrase pause each phrase "
            "goes to Speech to text at once (it works while you still speak) and the sentence is put together again from "
            "the lines that come back. 'cuts' says where it cuts.")
    inputs = {"in": "audio", "lines": "text"}
    outputs = {"out": "clip", "said": "text", "cuts": "text"}
    ports_help = {"in": "live audio",
                  "lines": "the heard lines from Speech to text (one per phrase while phrases are on)",
                  "out": "one clip per stretch of speech that ends with a pause (with a phrase pause: one per phrase, "
                         "tagged with its sentence and number)",
                  "said": "what was said: a line as it came in at 'lines', or a sentence put together from its phrases "
                          "(data: the satellite, the language, the phrases)",
                  "cuts": "where it cuts, as small contexts (an entry without text tagged start / phrase / end; data: the "
                          "sentence, the parts, the satellite): for a Level card, or any card"}
    tags = {"said": "a whole sentence put together from phrases (data: the satellite, the language, the phrases)",
            "start": "speech began (data: ago = how many seconds back)", "phrase": "a phrase was cut (data: part)",
            "end": "the sentence is done (data: parts)"}
    settings_schema = {"pause": {"type": "number", "title": "Pause that ends a clip (ms)", "default": 800,
                                 "help": "Shorter: quicker, but a pause mid-sentence cuts it. Longer: whole sentences."},
                       "longest": {"type": "number", "title": "Longest clip (s)", "default": 30},
                       "phrase": {"type": "number", "title": "Phrase pause (ms): faster speech to text", "default": 0,
                                  "min": 100, "off": 0,
                                  "help": "0 = off: Speech to text only starts once you have finished (after the pause "
                                          "above). E.g. 250: whenever you pause briefly in the middle (a breath, a "
                                          "comma), the part so far goes to Speech to text at once, so most of the text "
                                          "is ready when you finish; the lines that come back at 'lines' are put "
                                          "together into one sentence at 'said'."}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._cuts: dict[str, Segmenter] = {}
        self._utterance: dict[str, str] = {}  # satellite → the sentence its phrases belong to
        self._aac_spoke: dict[str, bool] = {}  # satellite → AAC was speaking there when this speech began
        self._sent: dict[str, int] = {}  # sentence → phrases sent to Speech to text
        self._heard: dict[str, dict[int, dict[str, Any]]] = {}  # sentence → its heard phrases by number
        self._expected: dict[str, int] = {}  # sentence → how many phrases (once it is done)
        self._waits: dict[str, Any] = {}  # sentence → the timer that sends it with what came

    # ------------------------------------------------------------------ clipping
    def _cut(self, what: str, meta: dict[str, Any], **data: Any) -> None:
        """A cut goes out at 'cuts' as a small context: one entry without text tagged start / phrase / end."""
        context = Context()
        context.add(self.tag(what), "", cut=what, source=meta.get("source"), name=meta.get("name"), **data)
        self.task(self.send(context, "cuts"), background=True)

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
                self._sent.pop(utterance, None)
                done = self._utterance.pop(key, None)
                self._cut("end", meta, utterance=done, parts=audio)
                if done and audio:
                    self.task(self._ended(done, int(audio)))
                continue
            pcm = b"".join(audio) if kind == "piece" else audio
            clip = {**meta, "pcm": pcm, "seconds": round(len(pcm) / (SAMPLE_RATE * 2), 1), "end": dt_util.utcnow().isoformat(),
                    "aac_spoke": self._aac_spoke.get(key, False)}
            if kind == "phrase":
                self._sent[utterance] = self._sent.get(utterance, 0) + 1
                clip |= {"utterance": utterance, "part": self._sent[utterance]}
                self._cut("phrase", meta, utterance=utterance, part=self._sent[utterance])
            self.clip_out("out", clip)

    # ------------------------------------------------------------------ the sentence again
    async def handle(self, context: Context, port: str) -> None:
        if port != "lines" or context.streaming:
            return
        said = context.get("*.said") or {}
        utterance = said.get("data", {}).get("utterance")
        if not utterance:  # a whole line: straight on
            await self.send(context, "said")
            return
        self._heard.setdefault(utterance, {})[int(said["data"].get("part") or 0)] = said
        await self._check(utterance)

    async def _ended(self, utterance: str, parts: int) -> None:
        self._expected[utterance] = parts
        await self._check(utterance)
        if utterance in self._expected:  # a phrase without text never comes: send what came after a moment
            self._waits[utterance] = self.later(JOIN_WAIT, lambda: self._joined(utterance))

    async def _check(self, utterance: str) -> None:
        if utterance in self._expected and len(self._heard.get(utterance, {})) >= self._expected[utterance]:
            await self._joined(utterance)

    async def _joined(self, utterance: str) -> None:
        """The sentence, put together from its heard phrases in order, leaves at 'said'."""
        if (cancel := self._waits.pop(utterance, None)) is not None:
            cancel()
        self._expected.pop(utterance, None)
        parts = [p for _, p in sorted(self._heard.pop(utterance, {}).items())]
        if not parts:
            return
        first = parts[0]["data"]
        context = Context()
        context.add(self.tag("said"), " ".join(p["text"].strip() for p in parts),
                    **{k: first.get(k) for k in ("mic", "mic_name", "area", "language", "aac_spoke")},
                    seconds=round(sum(float(p["data"].get("seconds") or 0) for p in parts), 1), phrases=len(parts))
        self.core.audio.mark(first.get("mic"), "text ready")
        await self.send(context, "said")

    async def unload(self) -> None:
        for cancel in self._waits.values():
            cancel()
