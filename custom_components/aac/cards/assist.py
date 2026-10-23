"""HA Assist: AAC as Home Assistant's conversation agent (Assist, HA's voice satellites, automations). Each HA
conversation is a chat of this card (in the sidebar); what HA sends leaves at `said`, the answer comes back."""

from __future__ import annotations

from . import register
from .chat import Chat


@register
class Assist(Chat):
    """HA Assist."""

    type = "assist"
    title = "HA Assist"
    help = ("AAC as Home Assistant's conversation agent: what Assist, a voice satellite or an automation says leaves at "
            "'said'; wire the answer back into 'answers'. Pick AAC as the agent in an Assist pipeline to use it.")
    tags = {"said": "what Home Assistant sent"}

    async def ask(self, text: str, conversation_id: str | None) -> str:
        """One turn of an HA conversation (a follow-up continues the same chat)."""
        _, answer = await self.send(text, chat_id=conversation_id, create_as=conversation_id)
        return answer
