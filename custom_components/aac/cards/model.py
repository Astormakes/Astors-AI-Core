"""Model (docs/v2 page 5): acts when the newest entry is not its own, reads the context and answers.

What the model gets, from the tags: `context.system` is the system prompt; its own `<id>.said` entries are its answers
(with their calls); the results of its calls are tool messages; every other entry with text is a user message (two in
a row are one). Its answer is a `<id>.said` entry, sent at `out` (while it is written, after every finished sentence,
with the state streaming). Its calls go out at `calls` to every card wired there; the card a call names answers, and
the result comes back into `in`, one call after the other. An answer that is nothing (or only the word in "Means no
answer") is no answer: nothing is sent.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from custom_components.aac.api import (Card, Client, Context, ModelError, ModelUnreachable, new_id, register, run,
                                      tool_spec)

VERSION = "4.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

SENTENCE = re.compile(r"[.!?…:](?=\s)|\n")  # where a finished sentence ends in text still being written


def conversation(context: Context, me: str) -> tuple[str, list[dict[str, Any]]]:
    """What the model gets: (system prompt, messages), read from the tags."""
    said, chat, mine = f"{me}.said", [], set()
    for e in context.conversation:
        if e["tag"] == said:
            if e["state"] == "streaming":
                continue
            calls = e["data"].get("calls") or []
            mine |= {c["id"] for c in calls}
            text = e["text"] + (f" …[cut off: {e['data'].get('cut') or 'stopped'}]" if e["state"] == "cut" else "")
            chat.append({"role": "assistant", "content": text or None,
                         **({"tool_calls": [{"id": c["id"], "type": "function", "function": {
                             "name": c["name"], "arguments": json.dumps(c.get("args") or {})}} for c in calls]} if calls else {})})
        elif e["data"].get("call_id") in mine:
            chat.append({"role": "tool", "tool_call_id": e["data"]["call_id"], "content": e["text"]})
        elif e["text"].strip():
            chat.append({"role": "user", "content": e["text"]})
    out: list[dict[str, Any]] = []
    for item in chat:
        if item["role"] == "user" and out and out[-1]["role"] == "user":  # chat templates allow no two user turns in a row
            out[-1] = {"role": "user", "content": f"{out[-1]['content']}\n\n{item['content']}"}
        else:
            out.append(item)
        if item.get("tool_calls"):  # every call needs its tool message (a stop can leave one without)
            answered = {m.get("tool_call_id") for m in chat}
            out += [{"role": "tool", "tool_call_id": c["id"], "content": "(no result: stopped)"}
                    for c in item["tool_calls"] if c["id"] not in answered]
    return context.system["text"].strip(), out


@register
class Model(Card):
    """A model."""

    type = "model"
    title = "Model"
    category = "Models"
    help = ("Answers when the newest entry is not its own, with the first free model of its list. Its answer goes out "
            "at 'out' (while it is written, after every finished sentence). Cards with functions wired from 'calls' "
            "(straight or through other cards, and back into 'in') are its tools. No answer: nothing is sent.")
    outputs = {"out": "text", "calls": "text"}
    calls_from = ("calls",)
    uses_tools = True
    tags = {"said": "its answers (its calls in data.calls, its thinking in data.thinking, why one was cut in data.cut; "
                    "the newest has the request in data.request)",
            "context_full": "its note to the model that the context is nearly full",
            "error": "no answer came (no model available, a model failed)"}
    ports_help = {"in": "a context arrives: when its newest entry is not the model's own, it answers; a tool's result "
                        "comes back here too",
                  "out": "its answer, to every card wired here (while it is written, after every finished sentence)",
                  "calls": "its calls, to every card wired here: the card a call names answers (For models)"}
    settings_schema = {
        "models": {"type": "modellist", "tag": "text", "title": "Models (in order)", "default": [], "help": "The list is run through from the top: a free model takes the context; a busy one queues it if its Queue is ticked, then the next is tried; the first that gets free takes it. Empty: every model with the tag, queued."},
        "temperature": {"type": "number", "title": "Temperature", "default": 0.7, "help": "0 = always the same, higher = more varied."},
        "max_tokens": {"type": "number", "title": "Longest answer (tokens)", "default": 0,
                       "help": "0 or empty: no limit (only the model's context). A limit keeps answers short (voice); thinking counts too."},
        "max_rounds": {"type": "number", "title": "Most tool rounds without an answer", "default": 8,
                       "help": "Model → tools → model … in one go before it gives up. 0 or empty: no limit (Stop still ends it)."},
        "silent": {"type": "text", "title": "Means no answer", "default": "nothing",
                   "help": "An answer that is only this word (or nothing at all) is no answer: nothing is sent. For models "
                           "that cannot stay silent. Empty: only an empty answer."},
        "context_note": {"type": "bool", "title": "Tell the model when its context is nearly full", "default": True,
                         "help": "From 80 % on, a short note goes at the end (visible in Context): keep it short, older parts may soon not fit."},
        "thinking": {"type": "bool", "title": "Thinking", "default": False, "help": "Let models that can, think first (slower)."},
    }
    functions = {
        "ask": {"help": "Ask another model something; it answers from what it knows (without its own tools).",
                "args": {"text": {"type": "string", "help": "The question.", "required": True}}},
    }

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._writing: set[asyncio.Task] = set()  # its answers being written (a stop cuts them)
        self._context_size: dict[str, int | None] = {}  # model → the tokens its context holds (the server tells)

    @property
    def busy(self) -> bool:
        return bool(self._writing)

    async def handle(self, context: Context, port: str) -> None:
        newest = context.newest
        if port != "in" or newest is None or newest["tag"].startswith(f"{self.id}.") or context.streaming:
            return  # nothing for it
        call = context.next_call()
        if call is not None:  # a call still waits: its own goes out (another card's is not for it)
            if call[0]["tag"] == self.tag("said"):
                await self.send(context, "calls")
            return
        task = asyncio.create_task(self._answer(context))  # its own task: a stop cuts only the writing
        self._writing.add(task)
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():  # the whole chain is being cancelled, not a stop
                raise
            if (said := context.get(self.tag("said"))) is not None and said["state"] == "streaming":
                said["state"], said["data"]["cut"] = "cut", "stopped"
                await self.send(context, "out")
        finally:
            self._writing.discard(task)

    # ------------------------------------------------------------------ answering
    def _rounds(self, context: Context) -> int:
        """Its answers since the newest entry that is neither its own nor a result."""
        rounds = 0
        for e in reversed(context.conversation):
            if e["tag"] == self.tag("said"):
                rounds += 1
            elif not e["tag"].startswith(f"{self.id}.") and "call_id" not in e["data"]:
                break
        return rounds

    def _tools(self, context: Context) -> dict[str, dict[str, Any]]:
        """The tools this time (into context.tools): the functions of the cards wired to it and back, as For models
        names them; a name two cards offer gets the card's name in front."""
        found = [(card, fn, spec) for card in self.tools() for fn, spec in card.offered().items()]
        names = [spec["name"] for _, _, spec in found]
        tools = {}
        for card, fn, spec in found:
            name = spec["name"] if names.count(spec["name"]) == 1 else f"{re.sub(r'[^a-z0-9]+', '_', card.name.lower()).strip('_')}_{spec['name']}"
            tools[name] = {"name": name, "card": card.id, "function": fn, "description": spec["help"], "args": spec["args"]}
        context.tools["data"]["tools"] = list(tools.values())
        return tools

    def _note(self, context: Context) -> None:
        """Its note when the conversation fills 80 % of the model's context (as its newest answer used it)."""
        for e in context.find(self.tag("context_full")):
            context.remove(e)
        usage = ((context.get(self.tag("said")) or {}).get("data") or {}).get("usage") or {}
        full = round(100 * usage["total_tokens"] / usage["context"]) if usage.get("context") and usage.get("total_tokens") else 0
        if self.settings.get("context_note", True) and full >= 80:
            context.add(self.tag("context_full"), f"(This conversation fills {full} % of your context: keep answers "
                        "short; older parts may soon not fit.)")

    def _silent(self, text: str) -> bool:
        """An answer that is no answer."""
        word = (self.settings.get("silent") or "").strip().lower()
        said = text.strip().lower().rstrip(".!…")
        return not said or bool(word) and said == word

    async def _answer(self, context: Context) -> None:
        """Ask the model; its answer goes out at `out`, its calls at `calls`."""
        if (most := int(self.settings.get("max_rounds") or 0)) > 0 and self._rounds(context) >= most:
            context.add(self.tag("error"), f"Error: {most} rounds of tool calls without an answer.")
            await self.send(context, "out")
            return
        entries = self.core.pool.entries(self.settings.get("models"), "text")
        if not entries:
            context.add(self.tag("error"), "Error: no model for text is set up (Models in the sidebar).")
            await self.send(context, "out")
            return
        tools = self._tools(context)
        specs = [tool_spec(t["name"], {"help": t["description"], "args": t["args"]}) for t in tools.values()]
        params = {"temperature": float(self.settings.get("temperature") or 0.7),
                  **({"max_tokens": n} if (n := int(self.settings.get("max_tokens") or 0)) > 0 else {})}
        if not self.settings.get("thinking"):
            params["chat_template_kwargs"] = {"enable_thinking": False}
        self._note(context)
        for e in context.find(self.tag("said")):  # only the newest answer keeps its request
            e["data"].pop("request", None)
        system, chat = conversation(context, self.id)
        payload = ([{"role": "system", "content": system}] if system else []) + chat
        said = context.add(self.tag("said"))
        said["state"] = "streaming"
        sent: list[asyncio.Future] = []  # its snapshots on their way (they go out in order)
        upto = [0]

        def live(text: str, thinking: str) -> None:
            said["text"] = text
            if thinking:
                said["data"]["thinking"] = thinking
            self.live(context, text, thinking)
            ends = [m.end() for m in SENTENCE.finditer(text)]
            if ends and ends[-1] > upto[0] and not self._silent(text[:ends[-1]]):  # a finished sentence goes out
                upto[0] = ends[-1]
                snap, before = context.copy(), sent[-1] if sent else None

                async def in_order() -> None:
                    if before:
                        await before
                    await self.send(snap, "out")
                sent.append(asyncio.ensure_future(in_order()))

        try:
            reply, failed = await self._ask(entries, payload, specs, params, live, said)
        finally:
            if sent:
                await sent[-1]
        if reply is None:
            if said["text"]:  # it had begun: what came stays, cut off
                said["state"], said["data"]["cut"] = "cut", "the model failed"
            else:
                context.remove(said)
            context.add(self.tag("error"), failed)
            await self.send(context, "out")
            return
        said["text"], said["state"] = reply["content"].strip(), "ended"
        if reply.get("thinking"):
            said["data"]["thinking"] = reply["thinking"].strip()
        if reply.get("finish") == "length":  # it did not finish: why
            u = (said["data"].get("request") or {}).get("usage") or {}
            said["state"] = "cut"
            said["data"]["cut"] = ("context full" if u.get("context") and u.get("total_tokens", 0) >= u["context"] - 32
                                   else f"Longest answer reached ({params['max_tokens']} tokens)" if params.get("max_tokens")
                                   else "length limit")
        if not reply["tool_calls"]:
            if self._silent(said["text"]):  # no answer: nothing is sent
                return
            await self.send(context, "out")
            return
        calls = []
        for call in reply["tool_calls"]:
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            tool = tools.get(call["function"]["name"])
            calls.append({"id": call.get("id") or new_id(), "name": call["function"]["name"], "args": args,
                          "card": tool["card"] if tool else None, "function": tool["function"] if tool else None})
        said["data"]["calls"] = calls
        if said["text"].strip():  # what it said before calling goes out (a chat shows it, it is spoken)
            await self.send(context, "out")
        for call in calls:
            if call["card"] is None:  # a tool it does not have: told at once, nothing travels
                context.add(self.tag("result"), json.dumps({"error": f"there is no tool {call['name']}"}), call_id=call["id"],
                            name=call["name"])
        if context.next_call() is not None:
            await self.send(context, "calls")
        else:  # only unknown tools: it hears so and tries again
            await self._answer(context)

    async def _ask(self, entries: list[dict[str, Any]], payload: list[dict[str, Any]], specs: list[dict[str, Any]],
                   params: dict[str, Any], live, said: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """Down the list (a free model, else the queues); one that does not answer is skipped. (reply, why it failed)."""
        pool, tried = self.core.pool, set()
        while True:
            left = [e for e in entries if e["model"] not in tried]
            async with pool.slot(left) as model_id:
                if model_id is None:
                    if any(pool.usable(e["model"]) for e in left):
                        self.log("Every model was busy and none queues: the context was dropped", "warning", what="dropped")
                        return None, "Error: every model was busy and none queues, so this was dropped."
                    return None, "Error: no model was available."
                try:
                    client = Client(self.hass, pool.get(model_id))
                    reply = await client.chat(payload, specs or None, on_delta=live, **params)
                    pool.mark_up(model_id)
                    usage = await self._usage(client, model_id, reply)
                    said["data"]["request"] = {"model": model_id, "body": {"messages": payload, **({"tools": specs} if specs else {}), **params},
                                               "reply": {"content": reply["content"], "calls": [
                                                   f"{c['function']['name']}({c['function']['arguments']})" for c in reply["tool_calls"]]},
                                               **({"usage": usage} if usage else {})}
                    if usage:
                        said["data"]["usage"] = usage
                    return reply, ""
                except ModelUnreachable as err:
                    pool.mark_down(model_id, str(err))
                    tried.add(model_id)
                except ModelError as err:
                    self.log(f"Model {pool.name(model_id)} failed: {err}", "warning", what="model")
                    pool.heard_from(model_id, str(err))
                    too_long = "context" in str(err).lower() and any(w in str(err).lower() for w in ("exceed", "too long", "n_ctx"))
                    return None, ("Error: this chat is too long for the model's context. Start a new chat." if too_long
                                  else f"Error: the model failed ({err}).")

    async def _usage(self, client: Client, model_id: str, reply: dict[str, Any]) -> dict[str, Any] | None:
        """The tokens this request used, and how many the model's context holds (asked once per model)."""
        if not (usage := reply.get("usage")):
            return None
        if model_id not in self._context_size:
            self._context_size[model_id] = await client.context_size()
        return {**usage, "context": self._context_size[model_id]}

    async def stop(self) -> None:
        for task in list(self._writing):
            task.cancel()

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "ask":  # a question of its own, answered from what it knows (its tools stay out)
            ask = Context()
            if context is not None:
                ask.system["text"] = context.system["text"]
            ask.add(self.tag("asked"), str(args.get("text") or ""))
            system, chat = conversation(ask, self.id)
            payload = ([{"role": "system", "content": system}] if system else []) + chat
            params = {"temperature": float(self.settings.get("temperature") or 0.7),
                      **({"max_tokens": n} if (n := int(self.settings.get("max_tokens") or 0)) > 0 else {})}
            reply, _ = await run(self.core.pool, self.core.pool.entries(self.settings.get("models"), "text"),
                                 lambda model: Client(self.hass, model).chat(payload, **params))
            return reply["content"].strip() if reply else {"error": "no model answered"}
        return await super().call(function, args, context)
