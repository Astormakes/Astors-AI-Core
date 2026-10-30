"""Relay: passes the context on, and its script (Python, `script.py`) works on it: `@new("name")` adds a text of its
own (the start: the system prompt; the end: read last; before or after a tag), `@on("tag")` changes the entries with
a tag, calls cards wired to it and back, sends stop or picks an output. Empty: a pure relay.

Streams: entries no block works on pass at once; one a block works on is held back until it is closed (`live=True`:
on every piece). The editor (`editor.js`) has a simple view that writes the code."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

from custom_components.aac.api import LOGGER, Card, Context, new_id, register

sys.modules.pop(f"{__name__}.script", None)  # a newer card file brings a newer script.py
from .script import REFERENCE, Run, Script, check, reference_text  # noqa: E402

VERSION = "2.2.1"  # this folder's card (a newer one replaces an older one in <config>/aac/cards)

KEEP_TURNS = 20  # turns whose side effects (stop, calls) are remembered, so a snapshot and the finished turn do them once
WAIT = 20  # seconds a script may run


@register
class Relay(Card):
    """Passes on; its script adds and changes."""

    type = "relay"
    title = "Relay"
    category = "Text"
    uses_tools = True
    help = ("Passes the context on. Its script (Python) adds texts (the system prompt, the time after the turn …) and "
            "changes, checks and routes what passes: @new(\"name\") makes its own entry, @on(\"tag\") works on entries "
            "with a tag, output(\"x\") sends the context out at x instead of out, stop() stops. With a dotted wire into 'reads' it starts from "
            "what it reads (e.g. a chat's conversation) and puts the arriving lines on top.")
    inputs = {"in": "text", "reads": "read"}
    ports_help = {"in": "a context arrives: the script runs",
                  "reads": "dotted: read when a context arrives, never starts anything. Wired from a Chat's 'provides': the "
                           "arriving lines go on top of that chat's conversation, and the answer files into that chat",
                  "out": "the context (when the script picks no output)",
                  "*": "an output the script picks with output(\"name\")"}
    tags = {"stop": "a stop it sent", "call": "its script's calls (an answer with the call; the result comes back as a tool message)"}
    settings_schema = {
        "script": {"type": "element", "element": "aac-relay-script", "file": "editor.js", "title": "Script", "default": "",
                   "help": "Python, checked before it runs. The ? button lists everything with examples. For models:\n"
                           + reference_text()},
    }
    functions: dict[str, dict[str, Any]] = {}  # one per @new block (see __init__)
    dynamic_functions = True
    offers = False  # a model wired to it and back gets a block's text only when it is ticked (For models)

    def __init__(self, *args: Any) -> None:
        """Initialize."""
        super().__init__(*args)
        self._script: Script | None = None
        self._done: dict[str, dict[Any, Any]] = {}  # turn id → side effects done
        self._calls: dict[str, asyncio.Future] = {}  # call id → its script waits for the result
        self.last_run: dict[str, Any] = {}
        self.tags = self._tags()
        self.functions = {h.tag: {"help": f"The text of its block {h.tag} (made now)."} for h in self.script.handlers if h.new}

    def _tags(self) -> dict[str, str]:
        """The tags its script writes: its @new texts, and stop / call only when it uses them."""
        script, found = self.script, {t: "its @new text" for t in self.script.new_tags}
        if "stop" in script.names:
            found["stop"] = type(self).tags["stop"]
        if any(c.id in script.names for c in self.domain.cards.values()):
            found["call"] = type(self).tags["call"]
        return found

    @property
    def script(self) -> Script:
        """Its script, checked (again when the setting changed)."""
        source = self.settings.get("script") or ""
        if self._script is None or self._script.source != source:
            self._script = check(source)
            if self._script.problems:
                LOGGER.warning("AAC: the script of %s: %s", self.id, "; ".join(self._script.problems))
        return self._script

    def ports(self) -> tuple[dict[str, str], dict[str, str], set[str]]:
        return dict(self.inputs), {"out": "text", **{n: "text" for n in self.script.outputs}}, set()

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if port != "in" or self._answered(context):  # a result for its script's call ends here
            return []
        if step and (read := step.read("reads")):  # start from what it reads (a chat's context), the new lines on top
            base, start = read[0], len(read[0].messages)
            context.events = [*base.events, *({**e, "at": e.get("at", 0) + start} for e in context.events)]
            context.messages = [*base.messages, *context.messages]
            context.origin, context.chat_id = base.origin, base.chat_id
        return await self._run(context, snapshot=False)

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        return await self._run(context, snapshot=True) if port == "in" else []

    async def _run(self, context: Context, snapshot: bool) -> list[Any] | None:
        script = self.script
        if script.problems or not script.code:
            return self._picks([], context)
        done = self._done.setdefault(context.id, {})
        self._done = dict(list(self._done.items())[-KEEP_TURNS:])
        tools = self.domain.flow.tools(self.id)
        audio = self.core.audio.spoken() if "audio" in script.names else None
        run = Run(self, context, snapshot, done, tools, self.hass.loop, audio)
        try:
            await asyncio.wait_for(self.hass.async_add_executor_job(run.go, script), WAIT)
        except TimeoutError:  # the thread goes on by itself; the turn does not wait for it
            run.problems.append(f"the script ran longer than {WAIT} s")
        except Exception:  # noqa: BLE001
            LOGGER.exception("AAC: the script of %s", self.id)
        if not snapshot or run.problems:
            self.last_run = {"time": time.time(), "ran": run.ran, "printed": run.printed, "problems": run.problems,
                             "outputs": run.outputs or ["out"]}
        if run.problems:
            LOGGER.warning("AAC: the script of %s: %s", self.id, "; ".join(run.problems))
        if run.stop_all:
            await self.domain.stop()
            return []
        return [] if run.hold else self._picks(run.outputs, context)

    def _picks(self, outputs: list[str], context: Context) -> list[Any]:
        """The wires of the outputs it picked (out when none); cards wired straight back only take calls, except the card
        the context started at (a Chat wired to it and back gets its turn back)."""
        mine = self.id
        back = {w["from"].split(".")[0] for w in self.domain.wires if w["to"] == f"{mine}.in"}
        ports = self.ports()[1]
        return [(p, c.id) for p in (outputs or ["out"]) if p in ports
                for c, _ in self.domain.flow.wired_from(mine, p) if c.id not in back or c.id == context.origin]

    async def travel(self, card: Any, function: str, args: dict[str, Any], context: Context) -> Any:
        """A script's call travels (docs/v2 page 3): along this Relay's wire to the card, which runs it; the result comes
        back along the card's wire. The call and its result stay in the context, visible."""
        port = next((p for p in self.ports()[1] if any(c.id == card.id for c, _ in self.domain.flow.wired_from(self.id, p))), "out")
        if context.progress:  # a snapshot: the call goes with a finished copy (side effects happen once per turn anyway)
            context = context.branch()
            context.progress = False
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
        if function in self.functions:  # one @new block's text, made now
            made = Context()
            run = Run(self, made, False, {}, [], self.hass.loop)
            script = self.script
            await self.hass.async_add_executor_job(run.go, Script(script.source, [h for h in script.handlers if h.new and h.tag == function],
                                                                    code=script.code, names=script.names))
            return "\n\n".join(e["text"] for e in made.entries)
        return await super().call(function, args, context)

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        """The editor: check a script while it is written, the last run, the reference."""
        if action == "check":
            found = check(str(args.get("script") or ""))
            return {"problems": found.problems, "outputs": found.outputs, "tags": [self.tag(t) for t in found.new_tags]}
        if action == "last_run":
            return self.last_run
        if action == "reference":
            return [{"group": g, "sig": s, "what": w, "example": x} for g, s, w, x in REFERENCE]
        return await super().ui_call(action, args)

    def summary(self) -> dict[str, Any]:
        self.tags = self._tags()
        return {**super().summary(), "problems": self.script.problems}
