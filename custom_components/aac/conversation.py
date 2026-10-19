"""Conversation agent for AAC – Astor's AI Core."""

from __future__ import annotations

import json
from typing import Any, Literal

import probatio

from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, intent, llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.json import json_dumps

from . import AACConfigEntry
from .const import (
    CONF_MAX_TOKENS,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DOMAIN,
    LOGGER,
    MAX_TOOL_ITERATIONS,
    MAX_TOOL_ITERATIONS_AUTO,
)
from .provider import OpenAICompatProvider, ProviderConnectionError, ProviderError
from .extractor import async_extract
from .home_api import API_ID as HOME_API_ID
from .memory_api import API_ID as MEMORY_API_ID
from .schedule_api import API_ID as SCHEDULE_API_ID
from .setup_api import API_ID as SETUP_API_ID, owner_present
from .domains import Domain
from .jobs import NO_MODEL
from .modules import access as module_access
from .runs import current_run


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AACConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One conversation agent per AAC domain."""
    async_add_entities([AACConversationEntity(entry, dom) for dom in entry.runtime_data.domains.values()])


def _format_tool(tool: llm.Tool, custom_serializer: Any) -> dict[str, Any]:
    function: dict[str, Any] = {
        "name": tool.name,
        "parameters": probatio.to_openapi(
            tool.parameters,
            custom_serializer=custom_serializer,
            openapi_version="3.1.0",
        ),
    }
    if tool.description:
        function["description"] = tool.description
    return {"type": "function", "function": function}


def _content_to_message(content: conversation.Content) -> dict[str, Any] | None:
    """Convert HA chat log content to an OpenAI chat message."""
    if isinstance(content, conversation.ToolResultContent):
        return {
            "role": "tool",
            "tool_call_id": content.tool_call_id,
            "content": json_dumps(
                {"data": content.result.data, "error": content.result.error}
            ),
        }
    if isinstance(content, conversation.AssistantContent):
        message: dict[str, Any] = {"role": "assistant", "content": content.content}
        if content.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": json_dumps(call.tool_args),
                    },
                }
                for call in content.tool_calls
            ]
        return message
    if content.content:
        return {"role": content.role, "content": content.content}
    return None


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
            raise HomeAssistantError(f"Model sent invalid tool arguments: {err}") from err
        result.append(
            llm.ToolInput(id=call.get("id"), tool_name=function["name"], tool_args=args)
        )
    return result


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
DECIDE_NOW = (
    "Now decide: is what you found worth telling the user right now? "
    "Normal states are not."
)


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


FINAL_NUDGE = (
    "Stop using tools now. Give your final answer in plain words "
    "(or exactly NOTHING if there is nothing to say)."
)


def _strip_tool_text(text: str | None) -> str | None:
    """Some models write a tool call as plain text when no tools are offered."""
    if text and "<tool_call>" in text:
        text = text.split("<tool_call>", 1)[0].strip()
    return text or None


def _steps(contents: list[conversation.Content]) -> list[dict[str, Any]]:
    """What the model said and which tools it used in one turn, for the run log."""
    steps: list[dict[str, Any]] = []
    for content in contents:
        # Text next to tool calls = the model's reasoning; the final answer is the result
        if isinstance(content, conversation.AssistantContent) and content.content and content.tool_calls:
            steps.append({"type": "text", "text": content.content})
        if isinstance(content, conversation.AssistantContent) and content.tool_calls:
            steps.extend(
                {"type": "tool_call", "tool": call.tool_name, "args": call.tool_args}
                for call in content.tool_calls
            )
        elif isinstance(content, conversation.ToolResultContent):
            steps.append(
                {
                    "type": "tool_result",
                    "tool": content.tool_name,
                    "error": bool(content.result.error),
                    "data": json_dumps(content.result.data)[:500],
                }
            )
    return steps


class AACConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """The AAC master agent. For now: model + optional HA tools, nothing more."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        """Initialize the agent of one domain (the first keeps the old unique id and entity id)."""
        self.entry = entry
        self.domain = domain
        self._attr_unique_id = domain.unique_id(entry.entry_id)
        if not domain.first:
            self._attr_name = domain.name
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DEFAULT_NAME,
            manufacturer=DEFAULT_NAME,
            model="Master agent",
            sw_version=entry.runtime_data.version,
            entry_type=dr.DeviceEntryType.SERVICE,
        )

    @property
    def supported_features(self) -> conversation.ConversationEntityFeature:
        """CONTROL while the master may use an HA API (settings change live)."""
        settings = self.domain.settings
        if settings.master.get(CONF_LLM_HASS_API) or settings.memory["enabled"]:
            return conversation.ConversationEntityFeature.CONTROL
        return conversation.ConversationEntityFeature(0)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Any language the model speaks."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as an agent."""
        await super().async_added_to_hass()
        if self.domain.first:  # the entry itself stays an agent id (older automations use it)
            conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent."""
        if self.domain.first:
            conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    def _provider(self, endpoint) -> OpenAICompatProvider:
        # Short connect timeout: a machine that is off must not stall the answer
        return OpenAICompatProvider(
            async_get_clientsession(self.hass), endpoint.base_url, endpoint.api_key, connect_timeout=3
        )

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Answer one user message, running tool calls as needed."""
        store = self.domain.settings  # this domain's settings (models and pools are shared)
        broker = self.entry.runtime_data.broker
        options = store.master
        run = current_run.get()
        tier = run.tier if run else "master"
        background = bool(run and run.kind == "auto")  # owner's availability rules apply
        picked = broker.endpoint_for(tier, background)
        if picked is None:
            response = intent.IntentResponse(language=user_input.language)
            response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"{NO_MODEL}. Open the AAC panel (SETTINGS → Pools).",
            )
            return conversation.ConversationResult(
                response=response, conversation_id=chat_log.conversation_id
            )
        model_id, endpoint = picked
        provider = self._provider(endpoint)
        api_ids: list[str] | None = None
        if not run or run.allow_tools:
            api_ids = list(options.get(CONF_LLM_HASS_API) or [])
            api_ids.append(self.domain.api_id(HOME_API_ID))  # the home, within the owner's access list
            if store.memory["enabled"]:
                api_ids.append(self.domain.api_id(MEMORY_API_ID))  # the master always has its own memory
            if store.autonomy.get("schedule", "allowed") != "off":
                api_ids.append(self.domain.api_id(SCHEDULE_API_ID))  # and may plan for itself
            for module in self.entry.runtime_data.modules:  # tools the modules bring (lists, …)
                if (api_id := module.api_ids.get(self.domain.id)) and module_access(
                    self.entry, module.id, self.domain.id
                )["enabled"]:
                    api_ids.append(api_id)
            if await owner_present(self.hass, user_input.context.user_id):
                api_ids.append(self.domain.api_id(SETUP_API_ID))  # with the owner present it helps set itself up
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                api_ids or None,
                # Charter first: the owner's values for every agent, stable (prompt cache)
                f"{store.charter}\n\n{options.get(CONF_PROMPT) or ''}".strip(),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

        # Context engine (first piece): hand over matching memories ourselves, because
        # small models rarely call recall on their own. Attached to the user message,
        # not the system prompt, so the server's prompt cache keeps working.
        memory_hint = None
        if store.memory["enabled"] and self.domain.memory and user_input.text:
            found = await self.domain.memory.async_context(user_input.text)
            if found:
                memory_hint = "Memories that may help (from your long-term memory):\n" + "\n".join(
                    f"- {m.text} ({m.source}{'' if m.confirmed else ', unconfirmed'}, {m.created[:10]})"
                    for m in found
                )
                if series := await self.domain.memory.async_series_for(found):
                    memory_hint += "\nExact values (structured facts):\n" + "\n".join(f"- {line}" for line in series)
                if run:
                    run.context = f"{run.context}\n\n{memory_hint}".strip()

        first_new = len(chat_log.content)
        tools = None
        if chat_log.llm_api:
            tools = [
                _format_tool(tool, chat_log.llm_api.custom_serializer)
                for tool in chat_log.llm_api.tools
            ]

        params = {
            "temperature": float(options.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE)),
            "max_tokens": int(options.get(CONF_MAX_TOKENS, DEFAULT_MAX_TOKENS)),
            # Understood by llama.cpp / vLLM chat templates (e.g. Qwen3)
            "chat_template_kwargs": {
                "enable_thinking": bool(options.get(CONF_THINKING, DEFAULT_THINKING))
            },
        }

        decide = bool(run and run.kind == "auto" and not run.allow_tools)
        if decide:
            params["response_format"] = DECISION_FORMAT
            params["temperature"] = min(params["temperature"], DECISION_TEMPERATURE)
        limit = MAX_TOOL_ITERATIONS_AUTO if run and run.kind == "auto" else MAX_TOOL_ITERATIONS
        for iteration in range(limit + 1):
            # Last round: no tools, so the model has to answer instead of exploring on
            final = iteration == limit
            messages = [
                m for c in chat_log.content if (m := _content_to_message(c)) is not None
            ]
            if memory_hint:
                last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
                messages[last_user] = {
                    **messages[last_user],
                    "content": f"{messages[last_user]['content']}\n\n[{memory_hint}]",
                }
            if final:
                messages.append({"role": "user", "content": FINAL_NUDGE})
            if decide and messages and messages[0]["role"] == "system":
                # Into the first system message: some chat templates allow no other
                messages[0] = {**messages[0], "content": messages[0]["content"] + DECISION_RULE}
            while True:
                try:
                    reply = await provider.chat(
                        endpoint.model, messages, None if final else tools, **params
                    )
                except ProviderConnectionError as err:
                    # Its machine did not answer (e.g. a PC that is off): next in the pool
                    broker.mark_down(model_id, str(err))
                    if (picked := broker.endpoint_for(tier, background)) is None:
                        raise HomeAssistantError(f"{NO_MODEL}: {err}") from err
                    LOGGER.info("Master model %s unreachable, using %s", model_id, picked[0])
                    if run:
                        run.steps.append({"type": "text", "text": f"{model_id} unreachable, switched to {picked[0]}"})
                    model_id, endpoint = picked
                    provider = self._provider(endpoint)
                    continue
                except ProviderError as err:
                    LOGGER.error("Model request failed: %s", err)
                    raise HomeAssistantError(f"Model request failed: {err}") from err
                broker.mark_up(model_id)
                broker.record_speed(model_id, (reply.get("_stats") or {}).get("tokens_per_second"))
                if run:
                    run.model = model_id
                break

            async for _tool_result in chat_log.async_add_assistant_content(
                conversation.AssistantContent(
                    agent_id=self.entity_id,
                    content=_decision_text(reply.get("content"))
                    if decide
                    else _strip_tool_text(reply.get("content"))
                    if final
                    else reply.get("content"),
                    thinking_content=reply.get("reasoning_content"),
                    tool_calls=None if final else _parse_tool_calls(reply),
                )
            ):
                pass

            if not chat_log.unresponded_tool_results:
                break

        if run:
            run.steps.extend(_steps(chat_log.content[first_new:]))

        # Automatic runs that used tools still end in a structured decision: one more
        # short call without tools. Free-text answers ("No urgent updates. The lamp is
        # off.") slipped past the silence check too often.
        if run and run.kind == "auto" and not decide and chat_log.content[-1].role == "assistant":
            messages = [m for c in chat_log.content if (m := _content_to_message(c)) is not None]
            messages.append({"role": "user", "content": DECIDE_NOW + DECISION_RULE})
            try:
                reply = await provider.chat(
                    endpoint.model,
                    messages,
                    None,
                    **{**params, "response_format": DECISION_FORMAT,
                       "temperature": min(params["temperature"], DECISION_TEMPERATURE)},
                )
            except ProviderError as err:
                LOGGER.warning("Decision call failed, keeping the text answer: %s", err)
            else:
                decided = _decision_text(reply.get("content"))
                run.steps.append({"type": "text", "text": f"decision: {decided}"})
                async for _ in chat_log.async_add_assistant_content(
                    conversation.AssistantContent(agent_id=self.entity_id, content=decided)
                ):
                    pass

        # Memory write path: keep what was worth keeping, in the background
        memory = self.domain.memory
        if store.memory["enabled"] and memory and user_input.text and (not run or run.kind == "chat"):
            stored_now = [
                str(call.tool_args.get("text", ""))
                for c in chat_log.content[first_new:]
                if isinstance(c, conversation.AssistantContent) and c.tool_calls
                for call in c.tool_calls
                if call.tool_name.endswith("remember")
            ]
            self.entry.async_create_background_task(
                self.hass,
                async_extract(memory, provider, endpoint.model, user_input.text, stored_now),
                "aac memory extract",
            )

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
