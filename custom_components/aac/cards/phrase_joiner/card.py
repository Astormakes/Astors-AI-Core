"""The Phrase joiner card (docs/v2 page 2): Puts a sentence together again that an Audio clipper sent in phrases (its
phrase pause): the heard lines from Speech to text come in at 'lines', the clipper's 'cuts' say when the sentence is
done and how many parts it had; then the whole sentence goes on as one line. Lines without phrase tags go straight
on."""

from __future__ import annotations

from typing import Any

from custom_components.aac.api import Card, Context, register

VERSION = "2.10.2"  # this card's file (a newer file replaces an older one in <config>/aac/cards)

JOIN_WAIT = 1.5  # seconds the Phrase joiner waits for a phrase that may bring no text


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
        context = Context()
        context.add(self.id, "said", " ".join(p["text"].strip() for p in parts), ["heard"],
                    **{k: first.get(k) for k in ("mic", "mic_name", "area", "language", "aac_spoke")},
                    seconds=round(sum(float(p["data"].get("seconds") or 0) for p in parts), 1), phrases=len(parts))
        self.core.audio.mark(first.get("mic"), "text ready")
        await self.domain.flow.send(context, self.id, "out")

