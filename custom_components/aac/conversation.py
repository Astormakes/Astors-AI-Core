"""Conversation agent for AAC – Astor's AI Core."""

from __future__ import annotations

import json
from typing import Any, Literal

import probatio

from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT, MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, llm
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.json import json_dumps
from homeassistant.loader import async_get_integration

from . import AACConfigEntry
from .const import (
    CONF_MAX_TOKENS,
    CONF_MODEL,
    CONF_TEMPERATURE,
    CONF_THINKING,
    DEFAULT_MAX_TOKENS,
    DEFAULT_NAME,
    DEFAULT_TEMPERATURE,
    DEFAULT_THINKING,
    DOMAIN,
    LOGGER,
    MAX_TOOL_ITERATIONS,
)
from .provider import ProviderError


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AACConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the conversation entity."""
    integration = await async_get_integration(hass, DOMAIN)
    async_add_entities([AACConversationEntity(entry, str(integration.version))])


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


class AACConversationEntity(
    conversation.ConversationEntity, conversation.AbstractConversationAgent
):
    """The AAC agent. For now: model + optional HA tools, nothing more."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False

    def __init__(self, entry: AACConfigEntry, version: str) -> None:
        """Initialize the agent."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=DEFAULT_NAME,
            model=entry.data[CONF_MODEL],
            sw_version=version,
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        if entry.options.get(CONF_LLM_HASS_API):
            self._attr_supported_features = (
                conversation.ConversationEntityFeature.CONTROL
            )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Any language the model speaks."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as an agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister the agent."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Answer one user message, running tool calls as needed."""
        options = self.entry.options
        try:
            await chat_log.async_provide_llm_data(
                user_input.as_llm_context(DOMAIN),
                options.get(CONF_LLM_HASS_API),
                options.get(CONF_PROMPT),
                user_input.extra_system_prompt,
            )
        except conversation.ConverseError as err:
            return err.as_conversation_result()

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

        for _ in range(MAX_TOOL_ITERATIONS):
            messages = [
                m for c in chat_log.content if (m := _content_to_message(c)) is not None
            ]
            try:
                reply = await self.entry.runtime_data.chat(
                    self.entry.data[CONF_MODEL], messages, tools, **params
                )
            except ProviderError as err:
                LOGGER.error("Model request failed: %s", err)
                raise HomeAssistantError(f"Model request failed: {err}") from err

            async for _tool_result in chat_log.async_add_assistant_content(
                conversation.AssistantContent(
                    agent_id=self.entity_id,
                    content=reply.get("content"),
                    thinking_content=reply.get("reasoning_content"),
                    tool_calls=_parse_tool_calls(reply),
                )
            ):
                pass

            if not chat_log.unresponded_tool_results:
                break

        return conversation.async_get_result_from_chat_log(user_input, chat_log)
