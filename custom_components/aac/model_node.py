"""The model card (spec/graph.md): reads the whole document and answers it or picks a node.

The document becomes an ordinary chat with tools (system / user / assistant / tool), so small models see
what they were trained on. Its tools are the nodes wired to its output: calling one writes a `call` entry,
sends the document into that node (flow.Step.call) and goes on when it comes back with the node's result.
Carried over from v0: only as much of the chat as the model holds (context_fit, learning its size), the next
machine of the pool when one is off, tools as a menu per model (now one function per node), a last round
without tools, the structured speak/silent decision of automatic runs, streaming to an open panel, and every
call recorded in the run.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any

import probatio

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm, template
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.json import json_dumps
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    LOGGER,
    MAX_TOOL_ITERATIONS,
    MAX_TOOL_ITERATIONS_AUTO,
)
from .context_fit import estimate, fit
from .jobs import NO_MODEL
from .models import resolve_model
from .provider import OpenAICompatProvider, ProviderConnectionError, ProviderContextError, ProviderError
from .request import Message, new_id
from .runs import Run, async_notify

if TYPE_CHECKING:
    from . import AACConfigEntry

# Automatic runs without tools decide explicitly whether to speak (enforced by the
# server's grammar). Much more reliable than asking for the word NOTHING: tested
# 12/12 on Qwen3-8B and 23/24 on Qwen3.6-35B (alarms vs. a quiet home).
DECISION_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"speak": {"type": "boolean"}, "message": {"type": "string"}},
            "required": ["speak", "message"],
            "additionalProperties": False,
        },
    },
}
DECISION_RULE = (
    '\nAnswer as JSON: {"speak": true or false, "message": "one short sentence, or empty"}. '
    'Not worth mentioning (NOTHING) means "speak": false.'
)
DECISION_TEMPERATURE = 0.3
# Tokens kept free besides the answer (max_tokens): the estimate is rough
CONTEXT_MARGIN = 300
# Models whose server was asked for its context size (once per start)
_CONTEXT_ASKED: set[str] = set()
DECIDE_NOW = (
    "Now decide: is what you found worth telling the user right now? "
    "Normal states are not."
)
FINAL_NUDGE = (
    "Stop using tools now. Give your final answer in plain words "
    "(or exactly NOTHING if there is nothing to say)."
)


class ModelError(HomeAssistantError):
    """The request could not be answered (no model, context too small, server error)."""


def format_tool(tool: llm.Tool, custom_serializer: Any) -> dict[str, Any]:
    """An HA tool in the OpenAI format."""
    function: dict[str, Any] = {
        "name": tool.name,
        "parameters": probatio.to_openapi(tool.parameters, custom_serializer=custom_serializer, openapi_version="3.1.0"),
    }
    if tool.description:
        function["description"] = tool.description
    return {"type": "function", "function": function}


def _parse_tool_calls(message: dict[str, Any]) -> list[llm.ToolInput] | None:
    calls = message.get("tool_calls")
    if not calls:
        return None
    result = []
    for call in calls:
        function = call.get("function", {})
        try:
            args = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as err:
            raise ModelError(f"Model sent invalid tool arguments: {err}") from err
        result.append(llm.ToolInput(id=call.get("id"), tool_name=function["name"], tool_args=args))
    return result


def _decision_text(content: str | None) -> str | None:
    """Turn the JSON decision back into plain text ('NOTHING' when not speaking)."""
    try:
        decision = json.loads(content or "")
    except json.JSONDecodeError:
        return content  # server ignored the format: fall back to the text rules
    if not isinstance(decision, dict):
        return content
    if not decision.get("speak"):
        return "NOTHING"
    return str(decision.get("message") or "").strip() or "NOTHING"


def _strip_tool_text(text: str | None) -> str | None:
    """Some models write a tool call as plain text when no tools are offered."""
    if text and "<tool_call>" in text:
        text = text.split("<tool_call>", 1)[0].strip()
    return text or None


def tool_name(*parts: str) -> str:
    """A function name the model servers accept (letters, digits, _ and -, at most 64)."""
    name = "__".join(re.sub(r"[^A-Za-z0-9_-]", "_", p) for p in parts if p)
    return name[:64]


def _signature(function: dict[str, Any]) -> str:
    """A tool for the menu: its name and arguments (* = required), e.g. get_state(entity_ids*)."""
    params = function.get("parameters") or {}
    required = set(params.get("required") or [])
    args = [f"{k}{'*' if k in required else ''}" for k in (params.get("properties") or {})]
    return f"{function['name']}({', '.join(args)})"


def _args_text(args: dict[str, Any]) -> str:
    """A call's arguments for a reader: its text, else the JSON."""
    if set(args) <= {"text"}:
        return str(args.get("text") or "")
    return json_dumps(args)


def build_conversation(message: Message, me: str, titles: dict[str, str]) -> tuple[list[str], list[dict[str, Any]]]:
    """The document as one model sees it: (system parts, the chat). Its own answers and calls are the assistant's,
    the results of its calls are tool results, a call to it is a question to it, another model's answer is
    "… said", what belongs to other models' loops is left out; notes go with the user turn."""
    entries = message.entries
    mine = {e["data"].get("call_id") for e in entries if e["kind"] == "call" and e["data"].get("model") == me}
    to_me = {e["data"].get("call_id") for e in entries if e["kind"] == "call" and e["data"].get("node") == me}
    system = [e["text"] for e in entries if e["kind"] == "system" and e["text"].strip()]
    conv: list[dict[str, Any]] = []
    results: dict[str, list[str]] = {}
    for e in entries:
        kind, cid, by = e["kind"], e["data"].get("call_id"), e["by"]
        if kind == "system":
            continue
        if kind == "answer" and by == me:
            conv.append({"role": "assistant", "content": e["text"]})
            continue
        if kind == "call":
            if e["data"].get("model") == me:
                conv.append({"role": "assistant", "content": e["data"].get("say") or None, "tool_calls": [
                    {"id": cid, "type": "function", "function": {"name": e["data"]["function"],
                                                                 "arguments": json_dumps(e["data"].get("fargs") or {})}}]})
                conv.append({"role": "tool", "tool_call_id": cid, "content": None})
            elif cid in to_me:
                who = titles.get(e["data"].get("model"), e["data"].get("model"))
                conv.append({"role": "user", "content": f"[{who} hands this to you] {_args_text(e['data'].get('args') or {})}".strip()})
            continue
        if cid in mine:
            text = e["text"] if kind in ("result", "answer") else f"[{e['text']}]"
            results.setdefault(cid, []).append(text)
            continue
        if cid and cid not in to_me:
            continue  # inside another model's loop
        if kind == "user":
            conv.append({"role": "user", "content": e["text"]})
        elif kind == "note":
            conv.append({"role": "user", "content": f"[{e['text']}]"})
        elif kind == "answer":
            conv.append({"role": "user", "content": f"[{titles.get(by, by)} said] {e['text']}"})
    for item in conv:
        if item["role"] == "tool":
            item["content"] = "\n\n".join(results.get(item["tool_call_id"], [])) or "done"
    merged: list[dict[str, Any]] = []
    for item in conv:  # some chat templates allow no two user turns in a row
        if merged and item["role"] == "user" and merged[-1]["role"] == "user":
            merged[-1] = {**merged[-1], "content": f"{merged[-1]['content']}\n\n{item['content']}"}
        else:
            merged.append(item)
    return system, merged


class ModelNode:
    """Answers messages of one AAC entry (one model card's turn at a time)."""

    def __init__(self, hass: HomeAssistant, entry: AACConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry

    # ------------------------------------------------------------------ helpers
    def _provider(self, endpoint) -> OpenAICompatProvider:
        # Short connect timeout: a machine that is off must not stall the answer
        return OpenAICompatProvider(async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key, connect_timeout=3)

    def _label(self, model_id: str) -> str:
        """A model's name for messages to the owner."""
        store = self.entry.runtime_data.store
        endpoint = resolve_model(self.hass, store, model_id)
        return next((m["name"] for m in store.models if m["id"] == model_id), endpoint.model if endpoint else model_id)

    async def _context_of(self, model_id: str, provider: OpenAICompatProvider) -> tuple[int | None, float]:
        """The model's context size (asked once from its server, else learned from errors) and token factor."""
        pool = self.entry.runtime_data.pool
        traits = pool.traits(model_id)
        if traits["n_ctx"] is None and model_id not in _CONTEXT_ASKED:
            _CONTEXT_ASKED.add(model_id)
            if n_ctx := await provider.context_size():
                pool.learn_context(model_id, n_ctx)
                traits = pool.traits(model_id)
        return traits["n_ctx"], traits["token_factor"]

    async def _system_prompt(self, message: Message, parts: list[str], tool_prompts: list[str]) -> str:
        """The stable parts (rendered as a template, like HA's prompt), then the tools' instructions."""
        user_name = None
        if message.user_id and (user := await self.hass.auth.async_get_user(message.user_id)):
            user_name = user.name
        variables = {"ha_name": self.hass.config.location_name, "user_name": user_name}

        def render(text: str) -> str:
            try:
                return template.Template(text, self.hass).async_render(variables, parse_result=False)
            except Exception as err:  # noqa: BLE001 - a broken template must not stop the answer
                LOGGER.error("AAC: error rendering a prompt part: %s", err)
                return text

        return "\n\n".join([render("\n\n".join(parts) or llm.DEFAULT_INSTRUCTIONS_PROMPT), *tool_prompts])

    async def _tools(self, message: Message, targets: list, menu: bool) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        """The functions for the wired nodes: (OpenAI tools, their instructions, name → (node, port, tool)).
        A node with tools of its own offers them (as a menu: one function per node), any other node one
        function that hands the message there."""
        offered, prompts, names = [], [], {}
        # The owner can name a node's input for the models (owner, 2026-10-23: "naming inputs so models can use
        # them as custom tools"): a charter's input named "yes" is the tool "yes" for a deciding model
        named = self.entry.runtime_data.store.graph(message.domain.id).get("names") or {}
        for node, port in targets:
            own = named.get(f"{node.id}.{port}") or {}
            if node.tools is not None:
                specs, prompt = await node.tools(message)
            else:
                specs, prompt = [{"type": "function", "function": {
                    "name": "send", "description": own.get("description") or (f"Hand the conversation to {node.title}"
                    + (f": {node.description.split('. ')[0]}" if node.description else "") + "."),
                    "parameters": {"type": "object", "properties": {"text": {
                        "type": "string", "description": "What you want from it (optional)"}}}}}], None
            if not specs:
                continue  # nothing to offer right now (setup without the owner)
            if prompt:
                prompts.append(prompt)
            base = own.get("name") or node.id
            if menu and len(specs) > 1:
                fname = tool_name(base)
                listing = "\n".join(f"- {_signature(s['function'])}: {(s['function'].get('description') or '').split(chr(10))[0][:160]}"
                                    for s in specs)
                offered.append({"type": "function", "function": {
                    "name": fname, "description": f"{own.get('description') or node.title}. Its tools:\n{listing}",
                    "parameters": {"type": "object", "properties": {
                        "tool": {"type": "string", "enum": [s["function"]["name"] for s in specs]},
                        "arguments": {"type": "object", "description": "The tool's arguments"}},
                        "required": ["tool"]}}})
                names[fname] = (node, port, None, {s["function"]["name"]: s for s in specs})
                continue
            for spec in specs:
                fname = tool_name(base, spec["function"]["name"]) if node.tools else tool_name(base)
                offered.append({"type": "function", "function": {**spec["function"], "name": fname}})
                names[fname] = (node, port, spec["function"]["name"], None)
        return offered, prompts, names

    # ------------------------------------------------------------------ one turn
    async def async_answer(self, message: Message, run: Run | None, node, tier: str, targets: list, step) -> Message:
        """One turn of a model card: answer the document, calling its wired nodes as tools; the answer is an
        `answer` entry. Returns the message (a loop may hand back a copy). Raises ModelError."""
        domain = message.domain
        options = domain.settings.master
        pool = self.entry.runtime_data.pool
        background = message.kind == "auto"  # the owner's availability rules apply
        picked = pool.endpoint_for(tier, background)
        if picked is None:
            raise ModelError(f"{NO_MODEL}. Open the AAC panel (SETTINGS → Compute).")
        model_id, endpoint = picked
        provider = self._provider(endpoint)
        me = node.id
        titles = {n.id: n.title for n in self.entry.runtime_data.bus.nodes_in(domain.id).values()}
        menu = pool.traits(model_id)["tool_mode"] == "menu"
        tools, tool_prompts, names = await self._tools(message, targets, menu)
        params = {
            "temperature": float(options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)),
            "max_tokens": int(options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)),
            # Understood by llama.cpp / vLLM chat templates (e.g. Qwen3)
            "chat_template_kwargs": {"enable_thinking": bool(options.get(CONF_THINKING, DEFAULT_THINKING))},
        }
        decide = message.kind == "auto" and not tools
        if decide:
            params["response_format"] = DECISION_FORMAT
            params["temperature"] = min(params["temperature"], DECISION_TEMPERATURE)
        limit = MAX_TOOL_ITERATIONS_AUTO if message.kind == "auto" else MAX_TOOL_ITERATIONS
        stream_to = None
        if run and message.origin == "chat" and message.chat_id and not decide:
            run.chat_id = message.chat_id
            stream_to = self._stream(run, domain.id)
        left_out, context_retry, no_tools = 0, 0, False
        answer: str | None = None
        now_note = f"[Local time: {dt_util.now().strftime('%A %Y-%m-%d %H:%M')}]"
        for iteration in range(limit + 1):
            final = iteration == limit  # last round: no tools, so the model has to answer instead of exploring on
            if stream_to:
                stream_to.round = iteration
            parts, conv = build_conversation(message, me, titles)
            system = await self._system_prompt(message, parts, [] if final or no_tools else tool_prompts)
            if conv and conv[-1]["role"] == "user":  # the time with the newest turn (not in the system prompt: the cache)
                conv[-1] = {**conv[-1], "content": f"{conv[-1]['content']}\n\n{now_note}"}
            conv = [{"role": "system", "content": system}, *conv]
            while True:
                offered = None if final or no_tools or not tools else tools
                # Only as much of the chat as the model holds: the oldest turns go first
                n_ctx, factor = await self._context_of(model_id, provider)
                messages = list(conv)
                if n_ctx:
                    messages, dropped, _ = fit(conv, offered, n_ctx - params["max_tokens"] - CONTEXT_MARGIN, factor)
                    if dropped > left_out:
                        left_out = dropped
                        if run:
                            run.steps.append({"type": "note", "text": f"Context full: the first {dropped} messages "
                                              f"of this chat were left out ({self._label(model_id)} holds {n_ctx} tokens)."})
                if final:
                    messages = [*messages, {"role": "user", "content": FINAL_NUDGE}]
                if decide and messages and messages[0]["role"] == "system":
                    # Into the first system message: some chat templates allow no other
                    messages = [{**messages[0], "content": messages[0]["content"] + DECISION_RULE}, *messages[1:]]
                try:
                    if stream_to:  # the owner watches the panel: show the answer while it is written
                        reply = await provider.chat_stream(endpoint.model, messages, offered, on_delta=stream_to, **params)
                    else:
                        reply = await provider.chat(endpoint.model, messages, offered, **params)
                except ProviderContextError as err:
                    # Longer than the model holds: learn its size (and how far off the estimate was), fit again;
                    # if even the tool descriptions do not fit (a small model, many tools): answer without tools
                    sent = estimate(messages, offered)
                    if context_retry == 1 and offered:
                        context_retry, no_tools = 2, True
                        if run:
                            run.steps.append({"type": "note", "text": f"Context full: {self._label(model_id)} has too "
                                              "little room for the tools, so this answer is without them."})
                        continue
                    if context_retry or not err.n_ctx:
                        raise ModelError(
                            f"This chat no longer fits into {self._label(model_id)} ({err.n_ctx or '?'} tokens of "
                            f"context, the request needed {err.n_prompt or '?'}). Start a new chat, or give the "
                            "model's server a bigger context (llama.cpp: -c)."
                        ) from err
                    context_retry = 1
                    pool.learn_context(model_id, err.n_ctx, err.n_prompt / sent if err.n_prompt and sent else None)
                    continue
                except ProviderConnectionError as err:
                    # Its machine did not answer (e.g. a PC that is off): next in the pool
                    pool.mark_down(model_id, str(err))
                    if (picked := pool.endpoint_for(tier, background)) is None:
                        raise ModelError(f"{NO_MODEL}: {err}") from err
                    LOGGER.info("Model %s unreachable, using %s", model_id, picked[0])
                    if run:
                        run.steps.append({"type": "note", "text": f"{model_id} unreachable, switched to {picked[0]}"})
                    model_id, endpoint = picked
                    provider = self._provider(endpoint)
                    continue
                except ProviderError as err:
                    LOGGER.error("Model request failed: %s", err)
                    raise ModelError(f"Model request failed: {err}") from err
                pool.mark_up(model_id)
                pool.record_speed(model_id, (reply.get("_stats") or {}).get("tokens_per_second"))
                if run:
                    run.model = model_id
                    domain.runs.record_request(run, model_id, messages, offered, left_out=left_out,
                                               tokens=estimate(messages, offered), n_ctx=n_ctx)
                break

            calls = None if final or not offered else _parse_tool_calls(reply)
            content = (_decision_text(reply.get("content")) if decide
                       else _strip_tool_text(reply.get("content")) if final else reply.get("content"))
            if run and reply.get("reasoning_content"):
                run.steps.append({"type": "thinking", "text": reply["reasoning_content"]})
            if not calls:
                answer = content
                break
            if run and content and content.strip():
                run.steps.append({"type": "say", "text": content})
            for i, call in enumerate(calls):
                message = await self._call(message, run, me, names, call, content if i == 0 else None, step)

        # Automatic runs that used tools still end in a structured decision: one more short call without
        # tools. Free-text answers ("No urgent updates. The lamp is off.") slipped past the silence check too often.
        if message.kind == "auto" and not decide and tools:
            parts, conv = build_conversation(message, me, titles)
            system = await self._system_prompt(message, parts, [])
            conv = [{"role": "system", "content": system}, *conv]
            if answer:
                conv.append({"role": "assistant", "content": answer})
            try:
                reply = await provider.chat(
                    endpoint.model, [*conv, {"role": "user", "content": DECIDE_NOW + DECISION_RULE}], None,
                    **{**params, "response_format": DECISION_FORMAT,
                       "temperature": min(params["temperature"], DECISION_TEMPERATURE)},
                )
            except ProviderError as err:
                LOGGER.warning("Decision call failed, keeping the text answer: %s", err)
            else:
                answer = _decision_text(reply.get("content"))
                if run:
                    run.steps.append({"type": "note", "text": f"decision: {answer}"})
        message.add(me, "answer", answer or "")
        return message

    async def _call(self, message: Message, run: Run | None, me: str, names: dict, call: llm.ToolInput,
                    say: str | None, step) -> Message:
        """One tool call: the document goes into that node and comes back with its result."""
        call_id = new_id()  # our own: models reuse ids ("call_0" every turn)
        found = names.get(call.tool_name)
        node, port, tool, menu = found if found else (None, None, None, None)
        args = dict(call.tool_args or {})
        if menu is not None:  # a menu function: which of the node's tools, with which arguments
            tool, args = str(args.get("tool") or ""), dict(args.get("arguments") or {})
            if tool not in menu:
                node = None
        title = node.title if node else call.tool_name
        message.add(me, "call", f"{title} · {tool or 'send'}" + (f": {_args_text(args)}" if args else ""),
                    call_id=call_id, node=node.id if node else None, tool=tool, args=args, model=me,
                    function=call.tool_name, fargs=call.tool_args, say=say)
        if run:
            run.steps.append({"type": "tool_call", "tool": f"{call.tool_name}.{tool}" if menu is not None else call.tool_name,
                              "node": title, "args": args})
        if node is None:
            message.add(me, "result", json_dumps({"error": f"No tool {call.tool_name!r} (or {tool!r})."}),
                        call_id=call_id, error=True)
            if run:
                run.steps.append({"type": "tool_result", "tool": call.tool_name, "error": True, "data": "no such tool"})
            return message
        message.calls.append({"call_id": call_id, "model": me, "node": node.id})
        back = await step.call(node, port, message)
        if back is None:  # it never came back: the model reads why
            back = message
            back.add(me, "result", json_dumps({"error": f"{title} did not hand the conversation back (check its wires)."}),
                     call_id=call_id, error=True)
        if back.calls and back.calls[-1]["call_id"] == call_id:
            back.calls.pop()
        if run:
            got = [e for e in back.entries if e["data"].get("call_id") == call_id and e["kind"] != "call"]
            run.steps.append({"type": "tool_result", "tool": call.tool_name, "node": title,
                              "error": any(e["data"].get("error") for e in got),
                              "data": "\n".join(e["text"] for e in got)[:500]})
        return back

    def _stream(self, run: Run, domain_id: str):
        """on_delta for a panel chat: the text so far (and a tool being called) to open panels, throttled."""
        last = [0.0, None]

        def send(text: str, tool: str | None) -> None:
            now = time.monotonic()
            if tool == last[1] and now - last[0] < 0.08:
                return
            last[0], last[1] = now, tool
            async_notify(self.hass, "delta", chat_id=run.chat_id, domain=domain_id, round=send.round,
                         text=_strip_tool_text(text) or "", tool=tool.split("__")[-1] if tool else None)

        send.round = 0  # one round per model call: the panel folds finished rounds away
        return send
