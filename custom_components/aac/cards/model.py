"""Model: reads the context as it is (docs/v2 page 3: system, messages, tools) and answers. Its tools are the cards
with functions in its loop: a call travels the shortest way along the wires there and its result the shortest way back,
then the model goes on. The answer goes back where the context came from."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from custom_components.aac.api import (Card, Client, Context, ModelError, ModelUnreachable, entry, new_id, register, run,
                                      tool_spec)

VERSION = "2.2.3"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

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
            text = m["text"] + (f" …[cut off: {data.get('cut') or 'stopped'}]" if m.get("state") == "cut" else "")
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
    help = ("Reads the context and answers, with the first free model of its list. Cards with functions in its loop "
            "(wired from its output back into it, straight or round through other cards) are its tools: a call goes "
            "there along the wires and the result comes back. "
            "While it works, every finished sentence goes out at once (a Chat shows it, Text to speech speaks it); the "
            "finished answer follows.")
    outputs = {"out": "text"}
    square = {"out"}
    uses_tools = True
    tags = {"answer": "its answers (with the calls it made; its thinking in data.thinking; why one was cut off in data.cut)",
            "stop": "it was stopped (its answer is cut off)", "context_full": "its note to the model that the context is nearly full",
            "error": "no answer came (no model available, a model failed); models never read these"}
    ports_help = {"in": "a context arrives: the model reads it and answers; a tool's result comes back here too (a stop "
                        "while it writes cuts the answer)",
                  "out": "square: a call goes the shortest way to the called card; the answer goes back to the card the "
                         "context started at if that is wired from here, else to every wired card but those wired "
                         "straight back (pure tools). Every card with functions in its loop is a tool (For models)"}
    settings_schema = {
        "models": {"type": "modellist", "tag": "text", "title": "Models (in order)", "default": [], "help": "The list is run through from the top: a free model takes the context; a busy one queues it if its Queue is ticked, then the next is tried; the first that gets free takes it. Empty: every model with the tag, queued."},
        "temperature": {"type": "number", "title": "Temperature", "default": 0.7, "help": "0 = always the same, higher = more varied."},
        "max_tokens": {"type": "number", "title": "Longest answer (tokens)", "default": 0,
                       "help": "0 or empty: no limit (only the model's context). A limit keeps answers short (voice); thinking counts too."},
        "max_rounds": {"type": "number", "title": "Most tool rounds without an answer", "default": 8,
                       "help": "Model → tools → model … in one turn before it gives up. 0 or empty: no limit (Stop still ends it)."},
        "context_note": {"type": "bool", "title": "Tell the model when its context is nearly full", "default": True,
                         "help": "From 80 % on, a short note is attached to the turn (visible in Context): keep it short, older parts may soon not fit."},
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
        self._context_size: dict[str, int | None] = {}  # model → the tokens its context holds (the server tells)
        self._used: dict[str, tuple[int, int]] = {}  # chat → (tokens used last time, context size)

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
                last["state"], last["data"]["cut"] = "cut", "stopped"
            stop = self._stop or entry(self.id, "signal", "stop", [self.tag("stop")])
            context.add(self.id, "signal", "stop", stop["tags"], **{**stop["data"], "done": True})  # history, no longer a running stop
        finally:
            self._task = None
            if self._sent:  # the snapshots first, then the finished turn
                await self._sent
            self._sent, self._upto = None, {}
        return routes or self._routes(context, step)

    def _routes(self, context: Context, step) -> list[Any]:
        """The answer goes back where the context started (if wired from here, even when that card is a tool too), else
        to every wired card but those wired straight back (they only take calls: Lists, Home …)."""
        targets = [c.id for c, _ in step.targets("out")]
        if context.origin in targets:
            return [("out", context.origin)]
        back = {w["from"].split(".")[0] for w in self.domain.wires if w["to"].split(".")[0] == self.id}
        return [("out", n) for n in targets if n not in back]

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
        if (most := int(self.settings.get("max_rounds") or 0)) > 0 and rounds >= most:
            context.add(self.id, "error", f"Error: {most} rounds of tool calls without an answer.", ["error"])
            return None
        tools = self._tools(context)
        specs = [tool_spec(t["name"], {"help": t["description"], "args": t["args"]}) for t in tools.values()]
        names = {c.id: c.name for c in self.domain.cards.values()}
        params = {"temperature": float(self.settings.get("temperature") or 0.7),
                  **({"max_tokens": n} if (n := int(self.settings.get("max_tokens") or 0)) > 0 else {})}
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
        used = self._used.get(context.chat_id or context.origin or "")  # how full the context was last time
        full = round(100 * used[0] / used[1]) if used and used[1] else 0
        if self.settings.get("context_note", True) and not rounds:
            context.put(self.id, "trailer", f"(This conversation fills {full} % of your context: keep answers short; older "
                        "parts may soon not fit.)" if full >= 80 else "", "context_full")
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
                    client = Client(self.hass, pool.get(model_id))
                    reply = await client.chat(payload, specs or None, on_delta=lambda t, th, a=answer: self._live(a, t, th, step), **params)
                    pool.mark_up(model_id)
                    usage = await self._usage(context, client, model_id, reply)
                    context.request = {"model": model_id, "body": {"messages": payload, **({"tools": specs} if specs else {}), **params},
                                       "reply": {"content": reply["content"], "calls": [  # what it wrote
                                           f"{c['function']['name']}({c['function']['arguments']})" for c in reply["tool_calls"]]},
                                       **({"usage": usage} if usage else {})}
                    break
                except ModelUnreachable as err:
                    pool.mark_down(model_id, str(err))
                    tried.add(model_id)
                except ModelError as err:
                    self.log(f"Model {pool.name(model_id)} failed: {err}", "warning", what="model")
                    pool.heard_from(model_id, str(err))
                    too_long = "context" in str(err).lower() and any(w in str(err).lower() for w in ("exceed", "too long", "n_ctx"))
                    failed = ("Error: this chat is too long for the model's context. Start a new chat, or let fewer turns go "
                              "along (the Chat card's setting)." if too_long else f"Error: the model failed ({err}).")
                    break
        if reply is None:
            if dropped:
                self.log("Every model was busy and none queues: the context was dropped", "warning", what="dropped")
            failed = failed or ("Error: every model was busy and none queues, so this was dropped." if dropped
                                else "Error: no model was available.")
            if answer["text"]:  # it had begun: what came stays, cut off
                answer["state"], answer["data"]["cut"] = "cut", "the model failed"
            else:
                context.remove(answer)
            context.add(self.id, "error", failed, ["error"])  # shown and spoken, never read by a model
            return None
        answer["text"], answer["state"] = reply["content"].strip(), "closed"
        if reply.get("thinking"):
            answer["data"]["thinking"] = reply["thinking"].strip()
        if reply.get("finish") == "length":  # it did not finish: why
            u = (context.request or {}).get("usage") or {}
            answer["state"] = "cut"
            answer["data"]["cut"] = ("context full" if u.get("context") and u.get("total_tokens", 0) >= u["context"] - 32
                                     else f"Longest answer reached ({params['max_tokens']} tokens)" if params.get("max_tokens")
                                     else "length limit")
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

    async def _usage(self, context: Context, client: Client, model_id: str, reply: dict[str, Any]) -> dict[str, Any] | None:
        """The tokens this request used, and how many the model's context holds (asked once per model)."""
        if not (usage := reply.get("usage")):
            return None
        if model_id not in self._context_size:
            self._context_size[model_id] = await client.context_size()
        usage = {**usage, "context": self._context_size[model_id]}
        self._used[context.chat_id or context.origin or ""] = (usage.get("total_tokens") or 0, usage["context"] or 0)
        return usage

    def _live(self, answer: dict[str, Any], text: str, thinking: str, step) -> None:
        answer["text"] = text
        if thinking:
            answer["data"]["thinking"] = thinking
        step.live(text, thinking)
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
            params = {"temperature": float(self.settings.get("temperature") or 0.7),
                      **({"max_tokens": n} if (n := int(self.settings.get("max_tokens") or 0)) > 0 else {})}
            reply, _ = await run(self.core.pool, self.core.pool.entries(self.settings.get("models"), "text"),
                                 lambda model: Client(self.hass, model).chat(payload, **params))
            return reply["content"].strip() if reply else {"error": "no model answered"}
        return await super().call(function, args, context)
