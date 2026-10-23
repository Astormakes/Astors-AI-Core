"""Model: reads the whole document and answers, or calls one of its tools (the cards wired from its output and back
into it). It writes live; the answer goes back where the message came from."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..const import LOGGER
from ..message import Message
from ..models import Client, ModelError, ModelUnreachable
from . import Card, register, tool_spec

MAX_ROUNDS = 8  # model → tool → model … per message


def conversation(message: Message, me: str, names: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    """The document as this model sees it: (system prompt, the chat). Its own answers and calls are the assistant's,
    their results tool messages; others' answers "[X said]"; notes "[…]"; trailers go with the turn they follow."""
    system = "\n\n".join(e["text"] for e in message.entries if e["kind"] == "system" and e["text"].strip())
    chat: list[dict[str, Any]] = []
    results: dict[str, str] = {}
    for e in message.entries:
        kind, by, data = e["kind"], e["by"], e["data"]
        if kind in ("system", "signal") or e.get("state") == "open":  # an answer still being written is not read back
            continue
        if kind == "call" and data.get("model") == me:
            chat.append({"role": "assistant", "content": None, "tool_calls": [{
                "id": data["call_id"], "type": "function",
                "function": {"name": data["function"], "arguments": json.dumps(data.get("args") or {})}}]})
            chat.append({"role": "tool", "tool_call_id": data["call_id"], "content": None})
        elif kind == "result" and data.get("model") == me:
            results[data["call_id"]] = e["text"]
        elif kind == "answer" and by == me:
            chat.append({"role": "assistant", "content": e["text"]})
        elif kind == "answer":
            chat.append({"role": "user", "content": f"[{names.get(by, by)} said] {e['text']}"})
        elif kind == "note":
            chat.append({"role": "user", "content": f"[{e['text']}]"})
        elif kind in ("said", "trailer"):
            chat.append({"role": "user", "content": e["text"]})
    for item in chat:
        if item["role"] == "tool":
            item["content"] = results.get(item["tool_call_id"]) or "done"
    merged: list[dict[str, Any]] = []
    for item in chat:  # some chat templates allow no two user turns in a row
        if merged and item["role"] == "user" and merged[-1]["role"] == "user":
            merged[-1] = {**merged[-1], "content": f"{merged[-1]['content']}\n\n{item['content']}"}
        else:
            merged.append(item)
    return system, merged


@register
class Model(Card):
    """A model."""

    type = "model"
    title = "Model"
    category = "Models"
    help = ("Reads the whole message and answers. Cards wired from its output and back into it are its tools; "
            "wire a Pool into its dotted input to choose the models.")
    inputs = {"in": "text", "pool": "pool"}
    outputs = {"out": "text"}
    square = {"out"}
    tags = {"answer": "its answers", "call": "its tool calls"}
    settings_schema = {
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

    def _models(self, step) -> list[dict[str, Any]]:
        pools = step.wired_into("pool")
        ids = pools[0].settings.get("models") or [] if pools else [m["id"] for m in self.core.pool.models]
        return [m for i in ids if (m := self.core.pool.get(i))]

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        if port != "in" or message.stopped:
            return [] if port != "in" else None
        self._task = asyncio.current_task()
        try:
            await self._answer(message, step)
        except asyncio.CancelledError:
            if (last := message.last("answer")) and last["by"] == self.id:
                last["state"] = "cut"
        finally:
            self._task = None
        # the answer goes back where the message started (if wired), else to every card that is not a tool
        tools = {c.id for c in step.tool_cards()}
        nexts = [c.id for c, _ in step.targets("out") if c.id not in tools]
        if message.origin in nexts:
            return [("out", message.origin)]
        return [("out", n) for n in nexts]

    async def _answer(self, message: Message, step) -> None:
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
        models = [m for m in self._models(step) if self.core.pool.usable(m["id"])] or self._models(step)
        if not models:
            message.add(self.id, "answer", "No model is set up (Settings → Models, and a Pool wired into this card).", ["answer"])
            return
        answer = message.add(self.id, "answer", "", ["answer"])
        answer["state"] = "open"
        for _ in range(MAX_ROUNDS):
            system, chat = conversation(message, self.id, names)
            payload = ([{"role": "system", "content": system}] if system else []) + chat
            reply = None
            for model in models:
                try:
                    reply = await Client(self.hass, model).chat(
                        payload, specs or None, on_delta=lambda t, a=answer: self._live(a, t, step), **params)
                    self.core.pool.mark_up(model["id"])
                    break
                except ModelUnreachable:
                    self.core.pool.mark_down(model["id"])
                except ModelError as err:
                    LOGGER.warning("AAC: model %s failed: %s", model["id"], err)
                    answer["text"] = f"The model failed: {err}"
                    break
            if reply is None:
                answer["text"] = answer["text"] or "No model answered (are the machines on?)."
                break
            if not reply["tool_calls"]:
                answer["text"] = reply["content"].strip()
                break
            message.entries.remove(answer)  # the call comes first, the answer after it
            for call in reply["tool_calls"]:
                fname = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                call_id = call.get("id") or f"c{len(message.entries)}"
                message.add(self.id, "call", "", ["call"], call_id=call_id, function=fname, args=args, model=self.id)
                card, fn = tools.get(fname, (None, None))
                try:
                    result = await card.call(fn, args, message) if card else {"error": f"no tool {fname}"}
                except Exception as err:  # noqa: BLE001 - a failing tool is told to the model
                    result = {"error": str(err)}
                text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
                message.add(card.id if card else self.id, "result", text[:8000], call_id=call_id, model=self.id)
            message.entries.append(answer)
        answer["state"] = "closed"

    def _live(self, answer: dict[str, Any], text: str, step) -> None:
        answer["text"] = text
        step.live(text)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        if function == "ask":
            ask = Message(origin=self.id)
            ask.add(message.last("said")["by"] if message and message.last("said") else self.id, "said", str(args.get("text") or ""))
            from ..flow import Step  # noqa: PLC0415
            await self._answer(ask, Step(self.domain.flow, self, "in", ask))
            return ask.answer
        return await super().call(function, args, message)
