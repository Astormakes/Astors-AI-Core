"""The Audio clipper card (docs/v2 page 2): Speech detection: a stretch of speech that ends with a pause leaves as one
clip. Wire it into Speech to text. With a phrase pause it hands out each phrase at once (Speech to text works while
you still speak); a Phrase joiner after Speech to text puts the sentence together again. 'cuts' says where it cuts."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import CARD_TYPES, SAMPLE_RATE, Card, Context, Segmenter, make_vad, new_id, register

VERSION = "2.10.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

COLUMN = 290  # one column in the grid (the panel's card width and gap)


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
        context = Context()
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

