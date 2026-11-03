"""AAC as an HA conversation agent: each turn (the HA app's Assist, voice devices, automations) goes to the card on the
core's "ha_conversation" channel (a Chat card with its tick: a chat "Assist · where · time"; or a card of your own)."""

from __future__ import annotations

from typing import Literal

from homeassistant.components import conversation
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, intent
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    """The agent."""
    async_add_entities([AACAgent(entry)])


class AACAgent(conversation.ConversationEntity, conversation.AbstractConversationAgent):
    """AAC in HA's conversation list."""

    _attr_name = "AAC"
    _attr_supported_features = conversation.ConversationEntityFeature.CONTROL

    def __init__(self, entry) -> None:
        """Initialize (the entry id as unique id)."""
        self.entry = entry
        self._attr_unique_id = entry.entry_id

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Any language the models speak."""
        return MATCH_ALL

    async def async_added_to_hass(self) -> None:
        """Register as an agent."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister."""
        conversation.async_unset_agent(self.hass, self.entry)
        await super().async_will_remove_from_hass()

    async def _async_handle_message(self, user_input: conversation.ConversationInput,
                                    chat_log: conversation.ChatLog) -> conversation.ConversationResult:
        """Hand the turn to the card on the core's "ha_conversation" channel (a Chat with its tick, or any card)."""
        response = intent.IntentResponse(language=user_input.language)
        device = dr.async_get(self.hass).async_get(user_input.device_id) if user_input.device_id else None
        where = (device.name_by_user or device.name) if device else "Home Assistant"
        answer = await self.hass.data[DOMAIN].layer_ask("converse", {"text": user_input.text, "where": where,
                                                                     "conversation_id": chat_log.conversation_id})
        if answer is None:
            response.async_set_error(intent.IntentResponseErrorCode.UNKNOWN,
                                     "No card in AAC takes Home Assistant's conversations (tick it on a Chat card).")
        else:
            chat_log.async_add_assistant_content_without_tools(
                conversation.AssistantContent(agent_id=self.entity_id, content=answer))
            response.async_set_speech(answer)
        return conversation.ConversationResult(response=response, conversation_id=chat_log.conversation_id)
