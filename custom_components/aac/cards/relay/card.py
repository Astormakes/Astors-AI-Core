"""Relay (docs/v2 page 4): its script (Python, `script.py`) runs top to bottom every time something arrives;
`input("in")` is what arrived, `output("out", what)` sends, nothing leaves otherwise. Its ports are what the script
names (none named: in, out). Every def is a function it offers; a call runs only that def. It runs on every context
that arrives, one still being written too (context.streaming), so it streams. The editor (`editor.js`) has a simple view that writes the code."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import sys
import time
from typing import Any

from custom_components.aac.api import Card, Context, new_id, register

sys.modules.pop(f"{__name__}.script", None)  # a newer card file brings a newer script.py
from .script import DEFAULT, OWN, REFERENCE, TOTAL, Run, Script, check, joined, reference_text  # noqa: E402

VERSION = "4.2.4"  # this folder's card (a newer one replaces an older one in <config>/aac/cards)

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
    needs = {"entities"}  # states() and state_attr() in its script, at read
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
        if any(c.id in script.names and c.functions for c in self.all_cards()):
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

    @staticmethod
    def _ports(script: Script) -> tuple[dict[str, str], dict[str, str]]:
        return dict(script.inputs or {"in": "text"}), {n: "text" for n in script.outputs or ["out"]}

    @classmethod
    def ports_for(cls, settings: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        return cls._ports(check(settings.get("script") or ""))

    def ports(self) -> tuple[dict[str, str], dict[str, str]]:
        return self._ports(self.script)  # checked once per script

    # ------------------------------------------------------------------ a context arrives
    async def handle(self, context: Context, port: str) -> None:
        if self._answered(context):  # a result for its script's call: the script waiting for it goes on
            return
        await self._run(context, port)

    def _read(self, port: str) -> Context:
        """What the dotted wires into a read input hold now, one after the other (nothing wired: empty)."""
        found = Context()
        for held in self.read(port):
            found = joined(found, held)
        return found

    async def _run(self, context: Context, port: str) -> None:
        script = self.script
        if script.problems or not script.code or script.inputs.get(port) == "read":
            return
        reads = {name: self._read(name) for name, kind in script.inputs.items() if kind == "read"}
        audio = await self.core.audio.spoken() if "audio" in script.names else None
        run = Run(self, context, port, reads, self.loop, audio)
        try:
            await asyncio.wait_for(self.blocking(run.go, script), TOTAL)
        except TimeoutError:  # stuck inside one line (the script stops itself on its next line); the turn goes on
            run.problems.append(f"the script ran longer than {TOTAL} s in all")
        self._after(run, context.streaming)
        if run.stop_now:
            await self.stop_all()
        for out, sent in run.sends:
            await self.send(sent, out)

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

    # ------------------------------------------------------------------ calls
    async def travel(self, card: Any, function: str, args: dict[str, Any], context: Context) -> Any:
        """A script's call travels (docs/v2 page 5) like every context: out at this Relay's outputs that reach the card,
        which answers; the result comes back along the wires into this Relay, and the script goes on with the context
        as it came back (the call, what the card added, its result: visible)."""
        ports = [p for p, kind in self.ports()[1].items() if kind == "text" and self.reaches(card.id, [p])]
        call = {"id": new_id(), "name": function, "card": card.id, "function": function, "args": args}
        context.add(self.tag("call"), "", calls=[call])
        waiter = self.loop.create_future()
        self._calls[call["id"]] = waiter
        try:
            for port in ports:
                await self.send(context, port)
        finally:
            self._calls.pop(call["id"], None)
        if not waiter.done():
            raise ValueError(f"{card.name} is not wired back to this Relay: its result cannot return")
        result, came_back = waiter.result()
        context.entries = came_back.entries
        return result

    def _answered(self, context: Context) -> bool:
        """A result came back for a call its script is waiting for."""
        for m in reversed(context.conversation):
            waiter = self._calls.get(m["data"].get("call_id"))
            if waiter is not None and not waiter.done():
                try:
                    waiter.set_result((json.loads(m["text"]), context))
                except ValueError:
                    waiter.set_result((m["text"], context))
                return True
        return False

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        """One of its defs, called (by a model or another Relay's script): only that def runs."""
        script = self.script
        if function not in script.functions:
            return await super().call(function, args, context)
        if script.problems:
            raise ValueError(f"its script has problems: {'; '.join(script.problems)}")
        run = Run(self, None, None, {}, self.loop, await self.core.audio.spoken() if "audio" in script.names else None,
                  calling=context or Context())
        try:
            result = await asyncio.wait_for(self.blocking(run.answer, script, function, args), TOTAL)
        except TimeoutError as err:
            raise ValueError(f"{function} ran longer than {TOTAL} s in all") from err
        finally:
            self._after(run, False)
            if run.stop_now:
                await self.stop_all()
        return "" if result is None else result

    # ------------------------------------------------------------------ the editor
    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """The editor: check a script while it is written, what it printed, the reference."""
        if action == "check":
            found = check(str(args.get("script") or ""))
            taken = sorted(c.id for c in self.all_cards() if c.id in OWN and c.id in found.names)
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
