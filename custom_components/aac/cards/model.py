"""Model: reads the whole document and answers, or calls one of its tools (the cards wired from its output and back
into it). It writes live; the answer goes back where the context came from."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import (LOGGER, Card, Client, Context, ModelError, ModelUnreachable, Step, entry, is_stop, register,
                                      tool_spec)

VERSION = "1.5.1"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

MAX_ROUNDS = 8  # model → tool → model … per context
SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written
KEEP_REQUESTS = 30  # chats whose last request is kept for the panel


def conversation(context: Context, me: str, names: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """The document as this model sees it: (system prompt, the chat). Its own answers and calls are the assistant's,
    their results tool messages; others' answers "[X said]"; notes "[…]"; trailers go with the turn they follow.
    Each chat item names the cards its text came from in "_from" (left out of the request; the panel shows it)."""
    system = "\n\n".join(e["text"] for e in context.entries if e["kind"] == "system" and e["text"].strip())
    chat: list[dict[str, Any]] = []
    results: dict[str, str] = {}
    for e in context.entries:
        kind, by, data = e["kind"], e["by"], e["data"]
        if kind == "signal" and is_stop(e):  # it was stopped here: the model knows next time
            chat.append({"role": "user", "content": "[stopped]", "_from": [by]})
            continue
        if kind in ("system", "signal") or e.get("state") == "open":  # an answer still being written is not read back
            continue
        if kind == "call" and data.get("model") == me:
            chat.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": data["call_id"], "type": "function",
                "function": {"name": data["function"], "arguments": json.dumps(data.get("args") or {})}}], "_from": [by]})
            chat.append({"role": "tool", "tool_call_id": data["call_id"], "content": None, "_from": [data.get("function", "").split("__")[0]]})
        elif kind == "result" and data.get("model") == me:
            results[data["call_id"]] = e["text"]
        elif kind == "answer" and by == me:
            chat.append({"role": "assistant", "content": e["text"], "_from": [by]})
        elif kind == "answer":
            chat.append({"role": "user", "content": f"[{names.get(by, by)} said] {e['text']}", "_from": [by]})
        elif kind == "note":
            chat.append({"role": "user", "content": f"[{e['text']}]", "_from": [by]})
        elif kind in ("said", "trailer"):
            chat.append({"role": "user", "content": e["text"], "_from": [by]})
    for item in chat:
        if item["role"] == "tool":
            item["content"] = results.get(item["tool_call_id"]) or "done"
    merged: list[dict[str, Any]] = []
    for item in chat:  # some chat templates allow no two user turns in a row
        if merged and item["role"] == "user" and merged[-1]["role"] == "user":
            merged[-1] = {**merged[-1], "content": f"{merged[-1]['content']}\n\n{item['content']}",
                          "_from": [*merged[-1]["_from"], *item["_from"]]}
        else:
            merged.append(item)
    return system, merged


@register
class Model(Card):
    """A model."""

    type = "model"
    title = "Model"
    category = "Models"
    help = ("Reads the whole context and answers, with the first free model of its list. Cards wired from its output "
            "back into it (straight, or through other cards like a Chat) are its tools. While it works, every finished sentence and tool call goes out at once (a "
            "Chat shows it, Text to speech speaks it); the finished answer follows.")
    outputs = {"out": "text"}
    square = {"out"}
    uses_tools = True
    tags = {"answer": "its answers", "call": "its tool calls", "stop": "it was stopped (its answer is cut off)",
            "error": "no answer came (no model available, a model failed); models never read these"}
    ports_help = {"in": "a context arrives: the model reads all of it and answers (a stop while it writes cuts the answer)",
                  "out": "square: the answer goes back to the card the context started at if that is wired, else to "
                         "every wired card but those wired straight back (that pair only attaches a tool). Every card "
                         "with functions on a way from here back into it is a tool (each can opt out)"}
    settings_schema = {
        "models": {"type": "modellist", "tag": "text", "title": "Models (in order)", "default": [], "help": "The list is run through from the top: a free model takes the context; a busy one queues it if its Queue is ticked, then the next is tried; the first that gets free takes it. Empty: every model with the tag, queued."},
        "temperature": {"type": "number", "title": "Temperature", "default": 0.7, "help": "0 = always the same, higher = more varied."},
        "max_tokens": {"type": "number", "title": "Longest answer (tokens)", "default": 1024},
        "thinking": {"type": "bool", "title": "Thinking", "default": False, "help": "Let models that can, think first (slower)."},
    }
    functions = {
        "ask": {"help": "Ask this model something; its answer comes back.",
                "args": {"text": {"type": "string", "help": "The question.", "required": True}}},
    }

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._task: asyncio.Task | None = None
        self._stop: dict[str, Any] | None = None
        self._sent: asyncio.Future | None = None  # the newest snapshot on its way (they go out in order)
        self._upto: dict[str, int] = {}  # answer entry → how far its text has gone out
        self.requests: dict[str, dict[str, Any]] = {}

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if port != "in":
            return []
        self._stop = None
        self._task = asyncio.create_task(self._answer(context, step))  # its own task: a stop cancels only the writing
        try:
            await self._task
        except asyncio.CancelledError:
            if not self._task.cancelled():  # the whole chain is being cancelled, not a stop
                raise
            if (last := context.last("answer")) and last["by"] == self.id:
                last["state"] = "cut"
            stop = self._stop or entry(self.id, "signal", "stop", [self.tag("stop")])
            context.entries.append({**stop, "data": {**stop["data"], "done": True}})  # visible history, no longer a running stop
        finally:
            self._task = None
            if self._sent:  # the snapshots first, then the finished turn
                await self._sent
            self._sent, self._upto = None, {}
        return self._routes(context, step)

    @staticmethod
    def _routes(context: Context, step) -> list[Any]:
        """The answer goes back where the context started (if wired), else to every wired card but those wired straight
        back (they only attach a tool)."""
        tools = {c.id for c in step.attached()}
        nexts = [c.id for c, _ in step.targets("out") if c.id not in tools]
        if context.origin in nexts:
            return [("out", context.origin)]
        return [("out", n) for n in nexts]

    def _snapshot(self, context: Context, step) -> None:
        """Send what it has so far (after the snapshots before it)."""
        before, send = self._sent, step.progress(self._routes(context, step))

        async def in_order() -> None:
            if before:
                await before
            await send
        self._sent = asyncio.ensure_future(in_order())

    async def _answer(self, context: Context, step) -> None:
        tools = {}
        specs = []
        for card in step.tool_cards():
            for name, spec in card.functions.items():
                tools[f"{card.id}__{name}"] = (card, name)
                specs.append(tool_spec(card, name, spec))
        names = {c.id: c.name for c in self.domain.cards.values()}
        params = {"temperature": float(self.settings.get("temperature") or 0.7),
                  "max_tokens": int(self.settings.get("max_tokens") or 1024)}
        if not self.settings.get("thinking"):
            params["chat_template_kwargs"] = {"enable_thinking": False}
        pool = self.core.pool
        entries = pool.entries(self.settings.get("models"), "text")
        if not entries:
            context.add(self.id, "error", "Error: no model for text is set up (Models in the sidebar).", ["error"])
            return
        answer = context.add(self.id, "answer", "", ["answer"])
        answer["state"] = "open"
        self._snapshot(context, step)  # under way: a chat shows the question at once (a spoken one too)
        await self._sent  # before the model is asked (only a few cards pass it on)
        told = [c for c in step.tool_cards() if (c.settings.get("instructions") or "").strip()]  # what its tools ask of it
        about = "About your tools:\n" + "\n".join(f"- {c.name} ({c.id}): {c.settings['instructions'].strip()}" for c in told) if told else ""
        for _ in range(MAX_ROUNDS):
            system, chat = conversation(context, self.id, names)
            system = "\n\n".join(x for x in (system, about) if x)
            sources = [e["by"] for e in context.entries if e["kind"] == "system" and e["text"].strip()] + [c.id for c in told]
            shown = ([{"role": "system", "content": system, "_from": sources}] if system else []) + chat
            payload = [{k: v for k, v in item.items() if not k.startswith("_")} for item in shown]
            self._keep(context, shown, specs, params)
            reply, tried, dropped, failed = None, set(), False, ""
            while True:  # down the list (a free model, else the queues); one that does not answer is skipped
                left = [e for e in entries if e["model"] not in tried]
                async with pool.slot(left) as model_id:
                    if model_id is None:
                        dropped = any(pool.usable(e["model"]) for e in left)
                        break
                    try:
                        reply = await Client(self.hass, pool.get(model_id)).chat(
                            payload, specs or None, on_delta=lambda t, a=answer: self._live(a, t, step), **params)
                        pool.mark_up(model_id)
                        break
                    except ModelUnreachable:
                        pool.mark_down(model_id)
                        tried.add(model_id)
                    except ModelError as err:
                        LOGGER.warning("AAC: model %s failed: %s", model_id, err)
                        failed = f"Error: the model failed ({err})."
                        break
            if reply is None:
                if dropped:
                    LOGGER.warning("AAC: %s: every model was busy and none queues; the context was dropped", self.id)
                failed = failed or ("Error: every model was busy and none queues, so this was dropped." if dropped
                                    else "Error: no model was available.")
                if answer["text"]:  # it had begun: what came stays, cut off
                    answer["state"] = "cut"
                else:
                    context.entries.remove(answer)
                context.add(self.id, "error", failed, ["error"])  # shown and spoken, never read by a model
                return
            if not reply["tool_calls"]:
                answer["text"] = reply["content"].strip()
                break
            if reply["content"].strip():  # it said something before its calls ("let me check"): that stays, finished
                answer["text"], answer["state"] = reply["content"].strip(), "closed"
                answer = context.add(self.id, "answer", "", ["answer"])
                answer["state"] = "open"
            context.entries.remove(answer)  # the call comes first, the answer after it
            for call in reply["tool_calls"]:
                fname = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                call_id = call.get("id") or f"c{len(context.entries)}"
                context.add(self.id, "call", "", ["call"], call_id=call_id, function=fname, args=args, model=self.id)
                card, fn = tools.get(fname, (None, None))
                try:
                    result = await card.call(fn, args, context) if card else {"error": f"no tool {fname}"}
                except Exception as err:  # noqa: BLE001 - a failing tool is told to the model
                    result = {"error": str(err)}
                text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                context.add(card.id if card else self.id, "result", text[:8000], call_id=call_id, model=self.id)
            context.entries.append(answer)
            self._snapshot(context, step)  # what it said and did so far goes out
        answer["state"] = "closed"

    def _keep(self, context: Context, shown: list[dict[str, Any]], specs: list[dict[str, Any]], params: dict[str, Any]) -> None:
        """What the model got (the panel's Request view), per chat; the newest few chats are kept, in memory."""
        key = context.chat_id or context.origin or ""
        self.requests.pop(key, None)
        self.requests[key] = {"at": dt_util.utcnow().isoformat(), "card": self.id, "messages": shown,
                              "tools": [t["function"]["name"] for t in specs], "params": params}
        while len(self.requests) > KEEP_REQUESTS:
            self.requests.pop(next(iter(self.requests)))

    def _live(self, answer: dict[str, Any], text: str, step) -> None:
        answer["text"] = text
        step.live(text)
        ends = [m.end() for m in SENTENCE.finditer(text)]
        if ends and ends[-1] > self._upto.get(answer["id"], 0):  # a sentence was finished: it goes out
            self._upto[answer["id"]] = ends[-1]
            self._snapshot(step.context, step)

    async def stop(self, stop: dict[str, Any] | None = None) -> bool:
        if not self._task or self._task.done():
            return False
        self._stop = dict(stop) if stop else None
        self._task.cancel()
        return True

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "ask":
            ask = Context(origin=self.id)
            ask.add(context.last("said")["by"] if context and context.last("said") else self.id, "said", str(args.get("text") or ""))
            await self._answer(ask, Step(self.domain.flow, self, "in", ask))
            return ask.answer
        return await super().call(function, args, context)
