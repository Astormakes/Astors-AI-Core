"""The Audio clipper card (docs/v2 page 2): Speech detection: a stretch of speech that ends with a pause leaves as one
clip. Wire it into Speech to text. With a phrase pause it hands out each phrase at once (Speech to text works while
you still speak); a Phrase joiner after Speech to text puts the sentence together again. 'cuts' says where it cuts."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import SAMPLE_RATE, Card, Context, Segmenter, make_vad, new_id, register

VERSION = "3.1.0"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

ROW = 160  # one row in the grid: a Phrase joiner it adds goes below the card it follows


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
                  "cuts": "where it cuts, as small contexts (an entry without text tagged start / phrase / end; data: the sentence, "
                          "the parts, the satellite): for a Phrase joiner, a Level card, or any card"}
    tags = {"start": "speech began (data: ago = how many seconds back)", "phrase": "a phrase was cut (data: part)",
            "end": "the sentence is done (data: parts)"}
    needs = {"setup"}  # read: is a Phrase joiner wired after it; full: add one (phrases on)
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
        if int(self.settings.get("phrase") or 0) > 0:
            self.hass.async_create_task(self._add_joiner())  # after this card is set up (nothing when it has one)

    def _cut(self, what: str, meta: dict[str, Any], **data: Any) -> None:
        """A cut goes out at 'cuts' as a small context: one entry without text tagged start / phrase / end."""
        context = Context()
        context.add(self.tag(what), "", cut=what, source=meta.get("source"), name=meta.get("name"), **data)
        self.hass.async_create_background_task(self.send(context, "cuts"), f"aac {self.id} cut")

    def _joined(self, setup: dict[str, Any]) -> bool:
        """A card at its cuts that also takes heard lines puts phrases together (a Phrase joiner, or one of your own).
        Read from the domain's setup, not its running cards: while a setup is made (start, Undo), not all are there yet."""
        return any(w["from"] == f"{self.id}.cuts" and "lines" in (setup["cards"].get(w["to"].split(".")[0]) or {}).get("inputs", {})
                   for w in setup["wires"])

    async def _add_joiner(self) -> None:
        """Phrases need their sister card: a Phrase joiner after the cards it feeds (Speech to text), which takes over
        their outgoing wires; its 'cuts' come from here (it builds through the setup interface: setup at full)."""
        try:
            setup = self.read_setup()
            if self._joined(setup):
                return
            fed = [w["to"].split(".")[0] for w in setup["wires"] if w["from"] == f"{self.id}.out"]
            for w in setup["wires"]:  # a joiner already behind Speech to text (only its cuts wire was lost): wire that one
                target = setup["cards"].get(w["to"].split(".")[0])
                if w["from"].split(".")[0] in fed and target is not None and {"lines", "cuts"} <= set(target["inputs"]):
                    self.set_wires([*setup["wires"], {"from": f"{self.id}.cuts", "to": f"{w['to'].split('.')[0]}.cuts"}])
                    return
            at = dict(setup["positions"].get(fed[0] if fed else self.id, {"x": 0, "y": 0}))
            joiner = (await self.add_card("phrase_joiner", "Phrase joiner", at={"x": at["x"], "y": at["y"] + ROW}))["id"]
            wires = [{"from": f"{self.id}.cuts", "to": f"{joiner}.cuts"}]
            for w in self.read_setup()["wires"]:
                if w["from"].split(".")[0] in fed and w["from"].endswith(".out"):  # what Speech to text fed, the joiner feeds now
                    wires.append({"from": f"{joiner}.out", "to": w["to"]})
                else:
                    wires.append(w)
            self.set_wires(wires + [{"from": f"{c}.out", "to": f"{joiner}.lines"} for c in fed])
        except PermissionError as err:  # phrases stay off until the owner allows it (or adds a joiner)
            self.log(f"No Phrase joiner added: {err}", "warning", what="permission")

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
            self.clip_out("out", clip)

