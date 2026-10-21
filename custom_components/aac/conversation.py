"""HA Assist adapter (spec/v1.md): AAC as Home Assistant's conversation agent.

v1: a thin adapter. A message from HA (an Assist pipeline, a voice satellite, an automation) becomes a
request of the domain, runs through the same chain and model node as the panel chat (broker.py →
request.py → model_node.py), and the answer goes back to HA. HA's chat log only keeps the conversation
for HA; AAC no longer uses it to build prompts or call tools.
"""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AACConfigEntry
from .const import DEFAULT_NAME, DOMAIN
from .domains import Domain


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AACConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One conversation agent per AAC domain."""
    async_add_entities([AACConversationEntity(entry, dom) for dom in entry.runtime_data.domains.values()])


class AACConversationEntity(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """A domain's agent as HA sees it."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supports_streaming = False
    # AAC's tools are always there (the home, memory, the modules), so HA lets it control things
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

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

    async def _async_handle_message(
        self,
        user_input: conversation.ConversationInput,
        chat_log: conversation.ChatLog,
    ) -> conversation.ConversationResult:
        """Answer a message from HA through the domain's request chain."""
        from .broker import async_run_request, short  # noqa: PLC0415 - avoid an import cycle

        # The conversation so far as HA kept it (HA forgets it after 5 idle minutes)
        history = [
            {"role": "user" if isinstance(c, conversation.UserContent) else "aac", "text": c.content}
            for c in chat_log.content[:-1]
            if isinstance(c, (conversation.UserContent, conversation.AssistantContent)) and c.content
        ]
        run, answer = await async_run_request(
            self.hass, self.entry, user_input.text,
            kind="chat", trigger="assist", title=f"Assist · {short(user_input.text)}",
            conversation_id=chat_log.conversation_id, extra_system_prompt=user_input.extra_system_prompt,
            user_id=user_input.context.user_id, domain=self.domain, history=history,
            language=user_input.language, device_id=user_input.device_id,
        )
        response = intent.IntentResponse(language=user_input.language)
        if answer is None:
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN, run.result or "No answer")
        else:
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=self.entity_id, content=answer.text))
            response.async_set_speech(answer.text)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
