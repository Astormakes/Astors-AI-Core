"""The model node (spec/v1.md): a prepared request in, an answer out, with AAC's own tool loop.

v1: AAC talks to the model server itself instead of going through Home Assistant's conversation chat log.
The tools stay HA LLM APIs (the modules' tool sets, the home, setup, HA's own Assist tools): the merged
API of the request's tool ids gives the tools, their names (`aac-home__control`) and how to call them.
Carried over from v0's conversation agent, unchanged in behaviour: only as much of the chat as the model
holds (context_fit, learning its size), the next machine of the pool when one is off, tools as a menu per
model, a last round without tools, the structured speak/silent decision of automatic runs, streaming to an
open panel, and every call recorded in the run.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import probatio
import slugify as unicode_slug

from homeassistant.components import conversation
from homeassistant.const import CONF_PROMPT
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm, template
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.json import json_dumps

from .const import (
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DOMAIN,
    LOGGER,
    MAX_TOOL_ITERATIONS,
    MAX_TOOL_ITERATIONS_AUTO,
)
from .context_fit import estimate, fit
from .jobs import NO_MODEL
from .menu_api import AREA_INFO, MENU_API_ID, build_menu, current_menu
from .models import resolve_model
from .provider import OpenAICompatProvider, ProviderConnectionError, ProviderContextError, ProviderError
from .request import ModelRequest
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


def _assistant_message(content: str | None, calls: list[llm.ToolInput] | None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.tool_name, "arguments": json_dumps(c.tool_args)}} for c in calls]
    return message


def _steps(turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the model said and which tools it used in one turn, for the run log (menu calls unwrapped:
    use_tool shows the tool it called, open_area a short note). turn = [{"say", "thinking", "calls"},
    {"result": (call, ToolResult)}, …]."""
    steps: list[dict[str, Any]] = []
    inner: dict[str, str | None] = {}  # tool call id → the tool use_tool called (None: open_area, no result shown)
    for item in turn:
        if "result" in item:
            call, result = item["result"]
            if call.id in inner and inner[call.id] is None:
                continue
            steps.append({"type": "tool_result", "tool": inner.get(call.id) or call.tool_name,
                          "error": bool(result.error), "data": json_dumps(result.data)[:500]})
            continue
        # thinking = the model's hidden reasoning; say = what it wrote for the reader before calling tools
        if item.get("thinking"):
            steps.append({"type": "thinking", "text": item["thinking"]})
        if item.get("say") and item.get("calls") and \
                item["say"].strip() not in [s["text"].strip() for s in steps if s["type"] == "say"]:
            steps.append({"type": "say", "text": item["say"]})  # a model stuck in a loop repeats itself
        for call in item.get("calls") or []:
            if call.tool_name.endswith("__use_tool"):
                inner[call.id] = str(call.tool_args.get("tool", "?"))
                steps.append({"type": "tool_call", "tool": inner[call.id], "args": call.tool_args.get("arguments") or {}})
            elif call.tool_name.endswith("__open_area"):
                inner[call.id] = None
                steps.append({"type": "note", "text": f"opened {call.tool_args.get('area', '?')}"})
            else:
                steps.append({"type": "tool_call", "tool": call.tool_name, "args": call.tool_args})
    return steps


class ModelNode:
    """Answers prepared requests of one AAC entry."""

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

    def _describe_area(self, area: str, names: list[str]) -> str | None:
        """One line for an area of the tool menu."""
        for key, text in AREA_INFO.items():
            if area == key or area.startswith(f"{key}-"):
                return text
        for module in self.entry.runtime_data.modules:  # a module's area: its own description
            if area.startswith(unicode_slug.slugify(f"AAC {module.manifest.get('name', module.id).lower()}")):
                return (module.manifest.get("description", "").split(". ")[0][:140]) or None
        return None

    async def _system_prompt(self, request: ModelRequest, llm_context: llm.LLMContext, api: llm.APIInstance | None,
                             api_prompt: str | None) -> str:
        """The system prompt as v0 had it from HA: the stable parts (rendered as a template, like HA's
        prompt), the tools' instructions (or the menu), the date and time, then this request's instructions."""
        user_name = None
        if request.user_id and (user := await self.hass.auth.async_get_user(request.user_id)):
            user_name = user.name
        variables = {"ha_name": self.hass.config.location_name, "user_name": user_name, "llm_context": llm_context}

        def render(text: str) -> str:
            try:
                return template.Template(text, self.hass).async_render(variables, parse_result=False)
            except Exception as err:  # noqa: BLE001 - a broken template must not stop the answer
                LOGGER.error("AAC: error rendering a prompt part: %s", err)
                return text

        parts = [render("\n\n".join(p for p in request.system if p.strip()) or llm.DEFAULT_INSTRUCTIONS_PROMPT)]
        if api_prompt:
            parts.append(api_prompt)
        if not any(t.name.endswith("GetDateTime") for t in (api.tools if api else [])):
            parts.append(render(llm.DATE_TIME_PROMPT))
        if request.instructions:
            parts.append(request.instructions)
        return "\n".join(parts)

    async def _call_tools(self, api: llm.APIInstance, calls: list[llm.ToolInput]) -> list[llm.ToolResult]:
        """Run a turn's tool calls at the same time (as HA did), errors as results the model can read."""

        async def one(call: llm.ToolInput) -> llm.ToolResult:
            try:
                result = await api.async_call_tool(call)
            except (HomeAssistantError, probatio.Invalid) as err:
                data: dict[str, Any] = {"error": type(err).__name__}
                if str(err):
                    data["error_text"] = str(err)
                return llm.ToolResult(data=data, error=True)
            return result if isinstance(result, llm.ToolResult) else llm.ToolResult(data=result)

        return list(await asyncio.gather(*(one(c) for c in calls)))

    # ------------------------------------------------------------------ the loop
    async def async_answer(self, request: ModelRequest, run: Run | None) -> tuple[str, dict[str, Any]]:
        """Answer a prepared request. Returns the answer text and what the write path needs
        (provider, model, the texts the model stored itself). Raises ModelError."""
        domain = request.domain
        options = domain.settings.master
        pool = self.entry.runtime_data.pool
        tier = request.tier
        background = request.kind == "auto"  # the owner's availability rules apply
        picked = pool.endpoint_for(tier, background)
        if picked is None:
            raise ModelError(f"{NO_MODEL}. Open the AAC panel (SETTINGS → Compute).")
        model_id, endpoint = picked
        provider = self._provider(endpoint)

        llm_context = llm.LLMContext(platform=DOMAIN, context=Context(user_id=request.user_id), language=request.language,
                                     assistant=conversation.DOMAIN, device_id=request.device_id)
        tool_ids = list(request.tools) if request.allow_tools else []
        # Tools as a menu (per model): areas first, an area's tools when the model opens it
        menu_mode = bool(tool_ids) and pool.traits(model_id)["tool_mode"] == "menu"
        if menu_mode:
            tool_ids.append(domain.api_id(MENU_API_ID))
        api = await llm.async_get_api(self.hass, tool_ids, llm_context) if tool_ids else None
        tools = [format_tool(t, api.custom_serializer) for t in api.tools] if api else None
        api_prompt = api.api_prompt if api else None
        token = None
        if api and menu_mode and (prefix := next((t["function"]["name"].split("__")[0] for t in tools
                                                  if t["function"]["name"].endswith("__open_area")), None)):
            own = [t for t in tools if t["function"]["name"].startswith(f"{prefix}__")]
            menu, menu_text = build_menu(api, prefix, [t for t in tools if t not in own], self._describe_area)
            api_prompt = menu_text  # the areas instead of every tool's instructions
            tools = own  # the two menu tools instead of all tools
            token = current_menu.set(menu)
        try:
            return await self._loop(request, run, model_id, endpoint, provider, api, tools, api_prompt, llm_context,
                                    menu_mode, options, tier, background)
        finally:
            if token is not None:
                current_menu.reset(token)

    async def _loop(self, request, run, model_id, endpoint, provider, api, tools, api_prompt, llm_context,
                    menu_mode, options, tier, background) -> tuple[str, dict[str, Any]]:
        domain = request.domain
        pool = self.entry.runtime_data.pool
        system = await self._system_prompt(request, llm_context, api, api_prompt)
        # AAC's own record of the chat goes in front (HA's chat log is not used any more)
        history = [{"role": "user" if m["role"] == "user" else "assistant", "content": m["text"]}
                   for m in request.history if m.get("text")]
        user_text = request.text
        if request.context:  # with the message, not in the system prompt: the server's prompt cache keeps working
            user_text = f"{user_text}\n\n[" + "\n\n".join(request.context) + "]"
        conv: list[dict[str, Any]] = [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]
        params = {
            "temperature": float(options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)),
            "max_tokens": int(options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)),
            # Understood by llama.cpp / vLLM chat templates (e.g. Qwen3)
            "chat_template_kwargs": {"enable_thinking": bool(options.get(CONF_THINKING, DEFAULT_THINKING))},
        }
        decide = request.kind == "auto" and not request.allow_tools
        if decide:
            params["response_format"] = DECISION_FORMAT
            params["temperature"] = min(params["temperature"], DECISION_TEMPERATURE)
        limit = MAX_TOOL_ITERATIONS_AUTO if request.kind == "auto" else MAX_TOOL_ITERATIONS
        stream_to = None
        if run and request.kind == "chat" and request.chat_id and not decide:
            stream_to = self._stream(run, domain.id)
        turn: list[dict[str, Any]] = []
        stored_now: list[str] = []
        left_out, context_retry, no_tools = 0, 0, False
        answer: str | None = None
        for iteration in range(limit + 1):
            final = iteration == limit  # last round: no tools, so the model has to answer instead of exploring on
            if stream_to:
                stream_to.round = iteration
            while True:
                offered = None if final or no_tools else tools
                # Only as much of the chat as the model holds: the oldest turns go first
                n_ctx, factor = await self._context_of(model_id, provider)
                messages = list(conv)  # a copy: the conversation grows after this call (the run keeps what was sent)
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
                    LOGGER.info("Master model %s unreachable, using %s", model_id, picked[0])
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

            calls = None if final or not api else _parse_tool_calls(reply)
            content = (_decision_text(reply.get("content")) if decide
                       else _strip_tool_text(reply.get("content")) if final else reply.get("content"))
            turn.append({"say": content, "thinking": reply.get("reasoning_content"), "calls": calls})
            conv.append(_assistant_message(content, calls))
            if not calls:
                answer = content
                break
            stored_now += [str(c.tool_args.get("text", "")) for c in calls if c.tool_name.endswith("remember")]
            for call, result in zip(calls, await self._call_tools(api, calls)):
                turn.append({"result": (call, result)})
                conv.append({"role": "tool", "tool_call_id": call.id,
                             "content": json_dumps({"data": result.data, "error": result.error})})

        if run:
            run.steps.extend(_steps(turn))

        # Automatic runs that used tools still end in a structured decision: one more
        # short call without tools. Free-text answers ("No urgent updates. The lamp is
        # off.") slipped past the silence check too often.
        if request.kind == "auto" and not decide and conv[-1]["role"] == "assistant":
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

        return answer or "", {"provider": provider, "model": endpoint.model, "stored_now": stored_now}

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
