"""Model: reads the context as it is (docs/v2 page 3: system, messages, tools) and answers. Its tools are the cards
wired from its output and straight back: a call travels there along the wire and comes back with its result, then the
model goes on. The answer goes back where the context came from."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from custom_components.aac.api import (LOGGER, Card, Client, Context, ModelError, ModelUnreachable, entry, new_id, register, run,
                                      tool_spec)

VERSION = "2.0.2"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

MAX_ROUNDS = 8  # model → tools → model … per turn
SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written


def conversation(context: Context, me: str, names: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """What the model gets: (system prompt, messages). The context already has the shape; only who is who is decided
    here: its own answers (and their calls) are the assistant's, their results tool messages; what another member of
    the chat answered is a message from that member, by name. Attachments go into their message (notes before the
    text, trailers after)."""
    system = "\n\n".join(e["text"] for e in context.system if e["text"].strip())
    chat: list[dict[str, Any]] = []
    mine: set[str] = set()
    for m in context.messages:
        kind, by, data = m["kind"], m["by"], m["data"]
        attached = m.get("attachments", [])
        before = [a for a in attached if a["kind"] == "note" and a["text"].strip()]
        after = [a for a in attached if a["kind"] != "note" and a["text"].strip()]
        if kind == "said":
            parts = [*before, m, *after]
            chat.append({"role": "user", "content": "\n\n".join(x["text"] for x in parts if x["text"].strip())})
            continue
        if kind == "answer" and by == me and m.get("state") != "open":
            calls = data.get("calls") or []
            mine |= {c["id"] for c in calls}
            text = m["text"] + (" …[stopped]" if m.get("state") == "cut" else "")
            chat.append({"role": "assistant", "content": text or None,
                         **({"tool_calls": [{"id": c["id"], "type": "function", "function": {
                             "name": c["name"], "arguments": json.dumps(c.get("args") or {})}} for c in calls]} if calls else {})})
        elif kind == "answer" and by != me and m["text"].strip():
            who = names.get(by, by)
            chat.append({"role": "user", "content": f"{who}: {m['text']}", "name": re.sub(r"[^A-Za-z0-9_-]", "_", who)[:40]})
        elif kind == "result" and data.get("call_id") in mine:
            chat.append({"role": "tool", "tool_call_id": data["call_id"], "content": m["text"]})
        if before or after:  # attachments on something nobody said (rare): read right after it
            chat.append({"role": "user", "content": "\n\n".join(a["text"] for a in [*before, *after])})
    out: list[dict[str, Any]] = []
    for item in chat:
        if item["role"] == "user" and out and out[-1]["role"] == "user":  # some chat templates allow no two user turns in a row
            out[-1] = {"role": "user", "content": f"{out[-1]['content']}\n\n{item['content']}"}
        else:
            out.append(item)
        if item.get("tool_calls"):  # every call needs its tool message (a stop can leave one without)
            answered = {m.get("tool_call_id") for m in chat}
            out += [{"role": "tool", "tool_call_id": c["id"], "content": "(no result: stopped)"}
                    for c in item["tool_calls"] if c["id"] not in answered]
    return system, out


@register
class Model(Card):
    """A model."""

    type = "model"
    title = "Model"
    category = "Models"
    help = ("Reads the context and answers, with the first free model of its list. Cards wired from its output and "
            "straight back into it are its tools: a call goes there along the wire and comes back with the result. "
            "While it works, every finished sentence goes out at once (a Chat shows it, Text to speech speaks it); the "
            "finished answer follows.")
    outputs = {"out": "text"}
    square = {"out"}
    uses_tools = True
    tags = {"answer": "its answers (with the calls it made)", "stop": "it was stopped (its answer is cut off)",
            "error": "no answer came (no model available, a model failed); models never read these"}
    ports_help = {"in": "a context arrives: the model reads it and answers; a tool's result comes back here too (a stop "
                        "while it writes cuts the answer)",
                  "out": "square: a call goes to the called card; the answer goes back to the card the context started "
                         "at if that is wired, else to every wired card but its tools. A card wired from here and "
                         "straight back is a tool (its functions: For models)"}
    settings_schema = {
        "models": {"type": "modellist", "tag": "text", "title": "Models (in order)", "default": [], "help": "The list is run through from the top: a free model takes the context; a busy one queues it if its Queue is ticked, then the next is tried; the first that gets free takes it. Empty: every model with the tag, queued."},
        "temperature": {"type": "number", "title": "Temperature", "default": 0.7, "help": "0 = always the same, higher = more varied."},
        "max_tokens": {"type": "number", "title": "Longest answer (tokens)", "default": 1024},
        "thinking": {"type": "bool", "title": "Thinking", "default": False, "help": "Let models that can, think first (slower)."},
    }
    functions = {
        "ask": {"help": "Ask another model something; it answers from what it knows (without its own tools).",
                "args": {"text": {"type": "string", "help": "The question.", "required": True}}},
    }

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._task: asyncio.Task | None = None
        self._stop: dict[str, Any] | None = None
        self._sent: asyncio.Future | None = None  # the newest snapshot on its way (they go out in order)
        self._upto: dict[str, int] = {}  # answer entry → how far its text has gone out

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        if port != "in":
            return []
        if pending := context.open_calls(self.id):  # a result came back and another call waits: on to that card
            return [("out", pending[0]["card"])]
        self._stop = None
        self._task = asyncio.create_task(self._round(context, step))  # its own task: a stop cancels only the writing
        routes = None
        try:
            routes = await self._task
        except asyncio.CancelledError:
            if not self._task.cancelled():  # the whole chain is being cancelled, not a stop
                raise
            if (last := context.last("answer")) and last["by"] == self.id:
                last["state"] = "cut"
            stop = self._stop or entry(self.id, "signal", "stop", [self.tag("stop")])
            context.add(self.id, "signal", "stop", stop["tags"], **{**stop["data"], "done": True})  # history, no longer a running stop
        finally:
            self._task = None
            if self._sent:  # the snapshots first, then the finished turn
                await self._sent
            self._sent, self._upto = None, {}
        return routes or self._routes(context, step)

    def _routes(self, context: Context, step) -> list[Any]:
        """The answer goes back where the context started (if wired, even when that card is a tool too: a Chat wired
        straight to it and back), else to every wired card but its tools."""
        targets = [c.id for c, _ in step.targets("out")]
        if context.origin in targets:
            return [("out", context.origin)]
        tools = {c.id for c in self.domain.flow.paired(self.id)}
        return [("out", n) for n in targets if n not in tools]

    def _snapshot(self, context: Context, step) -> None:
        """Send what it has so far (after the snapshots before it)."""
        before, send = self._sent, step.progress(self._routes(context, step))

        async def in_order() -> None:
            if before:
                await before
            await send
        self._sent = asyncio.ensure_future(in_order())

    def _tools(self, context: Context) -> dict[str, dict[str, Any]]:
        """The tools this turn (into the context's tools): the functions of the cards wired to it and back, as For
        models names them; a name two cards offer gets the card's name in front."""
        found = [(card, fn, spec) for card in self.domain.flow.tools(self.id) for fn, spec in card.offered().items()]
        names = [spec["name"] for _, _, spec in found]
        tools = {}
        for card, fn, spec in found:
            name = spec["name"] if names.count(spec["name"]) == 1 else f"{re.sub(r'[^a-z0-9]+', '_', card.name.lower()).strip('_')}_{spec['name']}"
            tools[name] = {"name": name, "card": card.id, "function": fn, "description": spec["help"], "args": spec["args"]}
        context.tools = list(tools.values())
        return tools

    async def _round(self, context: Context, step) -> list[Any] | None:
        """Ask the model (again). Calls: their answer goes to the first called card (returns that route); an answer
        without calls: None (it goes on as the answer)."""
        said = max((i for i, m in enumerate(context.messages) if m["kind"] == "said"), default=-1)
        rounds = sum(1 for m in context.messages[said + 1:] if m["kind"] == "answer" and m["by"] == self.id)
        if rounds >= MAX_ROUNDS:
            context.add(self.id, "error", f"Error: {MAX_ROUNDS} rounds of tool calls without an answer.", ["error"])
            return None
        tools = self._tools(context)
        specs = [tool_spec(t["name"], {"help": t["description"], "args": t["args"]}) for t in tools.values()]
        names = {c.id: c.name for c in self.domain.cards.values()}
        params = {"temperature": float(self.settings.get("temperature") or 0.7),
                  "max_tokens": int(self.settings.get("max_tokens") or 1024)}
        if not self.settings.get("thinking"):
            params["chat_template_kwargs"] = {"enable_thinking": False}
        pool = self.core.pool
        entries = pool.entries(self.settings.get("models"), "text")
        if not entries:
            context.add(self.id, "error", "Error: no model for text is set up (Models in the sidebar).", ["error"])
            return None
        answer = context.add(self.id, "answer", "", ["answer"])
        answer["state"] = "open"
        if not rounds:
            self._snapshot(context, step)  # under way: a chat shows the question at once (a spoken one too)
            await self._sent  # before the model is asked (only a few cards pass it on)
        system, chat = conversation(context, self.id, names)
        payload = ([{"role": "system", "content": system}] if system else []) + chat
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
                    context.request = {"model": model_id, "body": {"messages": payload, **({"tools": specs} if specs else {}), **params},
                                       "reply": {"content": reply["content"], "calls": [  # what it wrote
                                           f"{c['function']['name']}({c['function']['arguments']})" for c in reply["tool_calls"]]}}
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
                context.remove(answer)
            context.add(self.id, "error", failed, ["error"])  # shown and spoken, never read by a model
            return None
        answer["text"], answer["state"] = reply["content"].strip(), "closed"
        if not reply["tool_calls"]:
            return None
        calls = []
        for call in reply["tool_calls"]:
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            tool = tools.get(call["function"]["name"])
            calls.append({"id": call.get("id") or new_id(), "name": call["function"]["name"], "args": args,
                          "card": tool["card"] if tool else None, "function": tool["function"] if tool else None})
        answer["data"]["calls"] = calls
        for call in calls:
            if call["card"] is None:  # a tool it does not have: told at once, nothing travels
                context.add(self.id, "result", json.dumps({"error": f"there is no tool {call['name']}"}), call_id=call["id"],
                            name=call["name"])
        self._snapshot(context, step)  # what it said and is calling goes out (a chat shows it)
        if pending := context.open_calls(self.id):
            return [("out", pending[0]["card"])]
        return await self._round(context, step)  # only unknown tools: it hears so and tries again

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
        if function == "ask":  # a question of its own, answered from what it knows (its tools stay out)
            ask = Context(origin=self.id)
            if context is not None:
                ask.system = [dict(e) for e in context.system]
            ask.add(self.id, "said", str(args.get("text") or ""))
            names = {c.id: c.name for c in self.domain.cards.values()}
            system, chat = conversation(ask, self.id, names)
            payload = ([{"role": "system", "content": system}] if system else []) + chat
            params = {"temperature": float(self.settings.get("temperature") or 0.7), "max_tokens": int(self.settings.get("max_tokens") or 1024)}
            reply, _ = await run(self.core.pool, self.core.pool.entries(self.settings.get("models"), "text"),
                                 lambda model: Client(self.hass, model).chat(payload, **params))
            return reply["content"].strip() if reply else {"error": "no model answered"}
        return await super().call(function, args, context)
