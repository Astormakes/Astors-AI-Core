"""Relay (docs/v2 page 4): its script (Python, `script.py`) runs top to bottom every time something arrives;
`input("in")` is what arrived, `output("out", what)` sends, nothing leaves otherwise. Its ports are what the script
names (none named: in, out). Every def is a function it offers; a call runs only that def. It runs on every piece of a
turn still being written too, so it streams. The editor (`editor.js`) has a simple view that writes the code."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import sys
import time
from traceback import format_exc
from typing import Any

from custom_components.aac.api import Card, Context, new_id, register

sys.modules.pop(f"{__name__}.script", None)  # a newer card file brings a newer script.py
from .script import DEFAULT, OWN, REFERENCE, Run, Script, check, joined, reference_text  # noqa: E402

VERSION = "3.2.0"  # this folder's card (a newer one replaces an older one in <config>/aac/cards)

WAIT = 20  # seconds a script may run
PRINTED = 200  # print entries it keeps
PRINTED_FOR = 1800  # seconds they are kept


@register
class Relay(Card):
    """Its script decides what it adds, changes and sends."""

    type = "relay"
    title = "Relay"
    category = "Text"
    uses_tools = True
    help = ("A small Python script that runs every time something arrives: input(\"in\") is what arrived, "
            "output(\"out\", context) sends it on; nothing leaves otherwise. It adds texts (the system prompt, the time), "
            "changes, checks and routes what passes, calls cards wired from it (and back), and every def is a function models can "
            "use. A new Relay passes everything on.")
    ports_help = {"in": "what arrives here runs the script (input(\"in\")); the script names its inputs",
                  "out": "what the script sends with output(\"out\", …); the script names its outputs",
                  "*": "an input or output the script names: input(\"x\"), input(\"x\", read=True) (dotted), output(\"x\", …)"}
    settings_schema = {
        "script": {"type": "element", "element": "aac-relay-script", "file": "editor.js", "title": "Script", "default": DEFAULT,
                   "help": "Python, checked before it runs. The ? button lists everything with examples. For models:\n"
                           + reference_text()},
    }
    functions: dict[str, dict[str, Any]] = {}  # one per def (see __init__)
    dynamic_functions = True
    offers = False  # a model that reaches it gets a def only when it is ticked (For models)

    def __init__(self, *args: Any) -> None:
        """Initialize."""
        super().__init__(*args)
        self._script: Script | None = None
        self._calls: dict[str, asyncio.Future] = {}  # call id → its script waits for the result
        self.printed: deque[dict[str, Any]] = deque(maxlen=PRINTED)
        self.last_run: dict[str, Any] = {}
        self._refresh()

    def _refresh(self) -> None:
        """Its functions and tags, from its script."""
        script = self.script
        self.functions = dict(script.functions)
        found = {t: what for t, what in script.tags.items()}
        if any(c.id in script.names and c.functions for c in self.domain.cards.values()):
            found["call"] = "its script's calls (an answer with the call; the result comes back as a tool message)"
        self.tags = found

    @property
    def script(self) -> Script:
        """Its script, checked (again when the setting changed)."""
        source = self.settings.get("script") or ""
        if self._script is None or self._script.source != source:
            self._script = check(source)
            if self._script.problems:
                self.log(f"Its script: {'; '.join(self._script.problems)}", "warning", what="script")
        return self._script

    def ports(self) -> tuple[dict[str, str], dict[str, str], set[str]]:
        script = self.script
        return dict(script.inputs or {"in": "text"}), {n: "text" for n in script.outputs or ["out"]}, set()

    # ------------------------------------------------------------------ a context arrives
    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if self._answered(context):  # a result for its script's call ends here
            return []
        return await self._run(context, port)

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        return await self._run(context, port)

    def _read(self, port: str) -> Context:
        """What the dotted wires into a read input hold now, one after the other (nothing wired: empty)."""
        found = Context()
        for card, out in self.domain.flow.wired_into(self.id, port):
            if (held := card.held(out)) is not None:
                found = joined(found, held)
        return found

    async def _run(self, context: Context, port: str) -> list[Any]:
        script = self.script
        if script.problems or not script.code or script.inputs.get(port) == "read":
            return []
        reads = {name: self._read(name) for name, kind in script.inputs.items() if kind == "read"}
        audio = self.core.audio.spoken() if "audio" in script.names else None
        run = Run(self, context, port, reads, self.hass.loop, audio)
        try:
            await asyncio.wait_for(self.hass.async_add_executor_job(run.go, script), WAIT)
        except TimeoutError:  # the thread goes on by itself; the turn does not wait for it
            run.problems.append(f"the script ran longer than {WAIT} s")
        self._after(run, context.progress)
        if run.stop_now:
            await self.domain.stop()
        for out, sent in run.sends:
            await self.domain.flow.out(self, sent, self._picks(out, sent))
        return []

    def _after(self, run: Run, piece: bool) -> None:
        """What a run printed, logged and got wrong."""
        now = time.time()
        self.printed.extend({"time": now, "text": t} for t in run.printed)
        for text in run.logged:
            self.log(text, what="print")
        if run.problems:
            self.log(f"Its script: {'; '.join(run.problems)}", "warning", what="script")
        if not piece or run.problems:
            self.last_run = {"time": now, "problems": run.problems, "outputs": [o for o, _ in run.sends]}

    def _picks(self, out: str, context: Context) -> list[Any]:
        """The wires of one output; a card wired to it and back is its tool and only takes calls, except the card the
        context started at, the first on its way (a Chat wired to it and back gets its turn back)."""
        back = {c.id for p, kind in self.ports()[0].items() if kind == "text" for c, _ in self.domain.flow.wired_into(self.id, p)}
        return [(out, c.id) for c, _ in self.domain.flow.wired_from(self.id, out) if c.id not in back or context.way[:1] == [c.id]]

    # ------------------------------------------------------------------ calls
    async def travel(self, card: Any, function: str, args: dict[str, Any], context: Context) -> Any:
        """A script's call travels (docs/v2 page 3): along this Relay's wire to the card, which runs it; the result comes
        back along the card's wire. The call and its result stay in the context, visible."""
        port = next((p for p in self.ports()[1] if any(c.id == card.id for c, _ in self.domain.flow.wired_from(self.id, p))),
                    next(iter(self.ports()[1])))
        call = {"id": new_id(), "name": function, "card": card.id, "function": function, "args": args}
        context.add(self.id, "answer", "", ["call"], calls=[call])
        waiter = self.hass.loop.create_future()
        self._calls[call["id"]] = waiter
        try:
            await self.domain.flow.out(self, context, [(port, card.id)])
        finally:
            self._calls.pop(call["id"], None)
        if not waiter.done():
            raise ValueError(f"{card.name} is not wired back to this Relay: its result cannot return")
        return waiter.result()

    def _answered(self, context: Context) -> bool:
        """A result came back for a call its script is waiting for."""
        for m in reversed(context.messages):
            waiter = self._calls.get(m["data"].get("call_id")) if m["kind"] == "result" else None
            if waiter is not None and not waiter.done():
                try:
                    waiter.set_result(json.loads(m["text"]))
                except ValueError:
                    waiter.set_result(m["text"])
                return True
        return False

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """One of its defs, called (by a model or another Relay's script): only that def runs."""
        script = self.script
        if function not in script.functions:
            return await super().call(function, args, context)
        if script.problems:
            raise ValueError(f"its script has problems: {'; '.join(script.problems)}")
        run = Run(self, None, None, {}, self.hass.loop, self.core.audio.spoken() if "audio" in script.names else None,
                  calling=context or Context())
        try:
            result = await asyncio.wait_for(self.hass.async_add_executor_job(run.answer, script, function, args), WAIT)
        except TimeoutError as err:
            raise ValueError(f"{function} ran longer than {WAIT} s") from err
        finally:
            self._after(run, False)
            if run.stop_now:
                await self.domain.stop()
        return "" if result is None else result

    # ------------------------------------------------------------------ the editor
    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """The editor: check a script while it is written, what it printed, the reference."""
        if action == "check":
            found = check(str(args.get("script") or ""))
            taken = sorted(c.id for c in self.domain.cards.values() if c.id in OWN and c.id in found.names)
            return {"problems": found.problems, "inputs": found.inputs or {"in": "text"}, "outputs": found.outputs or ["out"],
                    "sends": bool(found.outputs), "functions": list(found.functions), "tags": [self.tag(t) for t in found.tags],
                    "taken": taken}
        if action == "printed":
            now = time.time()
            return {"printed": [p for p in self.printed if now - p["time"] < PRINTED_FOR], **self.last_run}
        if action == "reference":
            return [{"group": g, "sig": s, "what": w, "example": x} for g, s, w, x in REFERENCE]
        return await super().ui_call(action, args)

    def summary(self) -> dict[str, Any]:
        self._refresh()
        return {**super().summary(), "problems": self.script.problems}
