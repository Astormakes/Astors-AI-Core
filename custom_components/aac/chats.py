"""The user's chats with AAC (several, kept across restarts).

One special chat, "From AAC" (id `aac`), holds what AAC says on its own
(heartbeat, event triggers), so proactive messages have a home.
"""

from __future__ import annotations

from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .runs import async_notify

AAC_CHAT_ID = "aac"
MAX_MESSAGES = 200


class ChatStore:
    """Chats with their messages."""

    def __init__(self, hass: HomeAssistant, domain_id: str | None = None, suffix: str = "") -> None:
        """Initialize (suffix: storage per AAC domain; the first domain has none)."""
        self.hass = hass
        self.domain_id = domain_id
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.chats{suffix}")
        self.chats: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load from disk."""
        self.chats = (await self._store.async_load() or {}).get("chats", {})

    @property
    def store(self):
        """The HA storage behind it (to remove a domain's data)."""
        return self._store

    def _save(self) -> None:
        self._store.async_delay_save(lambda: {"chats": self.chats}, 1)

    @staticmethod
    def summary(chat: dict[str, Any]) -> dict[str, Any]:
        """A chat without its messages (for the list)."""
        return {k: v for k, v in chat.items() if k != "messages"}

    def list(self) -> list[dict[str, Any]]:
        """All chats, newest activity first. 'From AAC' only once it has messages."""
        chats = [self.summary(c) for c in self.chats.values()]
        return sorted(chats, key=lambda c: c["updated"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """One chat with messages."""
        return self.chats.get(chat_id)

    @callback
    def create(self, title: str = "New chat", chat_id: str | None = None) -> dict[str, Any]:
        """Start a chat."""
        now = dt_util.utcnow().isoformat()
        chat = {
            "id": chat_id or uuid.uuid4().hex[:12],
            "title": title,
            "created": now,
            "updated": now,
            "conversation_id": None,
            "unread": 0,
            "messages": [],
        }
        self.chats[chat["id"]] = chat
        self._save()
        async_notify(self.hass, "chats", domain=self.domain_id)
        return chat

    @callback
    def add_message(self, chat_id: str, role: str, text: str, **extra: Any) -> dict[str, Any]:
        """Append a message ('user' or 'aac'); AAC's own chat counts unread."""
        chat = self.chats.get(chat_id)
        if chat is None:
            title = "From AAC" if chat_id == AAC_CHAT_ID else "Chat"
            chat = self.create(title, chat_id)
        message = {"role": role, "text": text, "ts": dt_util.utcnow().isoformat(), **extra}
        chat["messages"] = [*chat["messages"], message][-MAX_MESSAGES:]
        chat["updated"] = message["ts"]
        if role == "user" and chat["title"] == "New chat":
            chat["title"] = text[:48] + ("…" if len(text) > 48 else "")
        if role == "aac" and extra.get("proactive"):
            chat["unread"] += 1
        self._save()
        async_notify(self.hass, "chat", chat_id=chat_id, domain=self.domain_id)
        return message

    @callback
    def mark_read(self, chat_id: str) -> None:
        """Clear the unread counter."""
        if (chat := self.chats.get(chat_id)) and chat["unread"]:
            chat["unread"] = 0
            self._save()
            async_notify(self.hass, "chats", domain=self.domain_id)

    @callback
    def history(self, chat_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """The chat so far without the newest message (the one being answered), errors left out."""
        chat = self.chats.get(chat_id)
        if not chat:
            return []
        return [{"role": m["role"], "text": m["text"]} for m in chat["messages"][:-1]
                if not m.get("error") and m.get("text")][-limit:]

    @callback
    def rewind(self, chat_id: str, index: int) -> None:
        """Cut the chat off before message `index` (edit or rerun); HA's conversation starts over."""
        if chat := self.chats.get(chat_id):
            chat["messages"] = chat["messages"][:max(0, index)]
            chat["conversation_id"] = None
            self._save()
            async_notify(self.hass, "chat", chat_id=chat_id, domain=self.domain_id)

    @callback
    def edit(self, chat_id: str, index: int, text: str) -> None:
        """Change a message in place (also AAC's answers); later messages stay, HA's conversation starts over."""
        if (chat := self.chats.get(chat_id)) and 0 <= index < len(chat["messages"]):
            chat["messages"][index] = {**chat["messages"][index], "text": text, "edited": True}
            chat["conversation_id"] = None
            self._save()
            async_notify(self.hass, "chat", chat_id=chat_id, domain=self.domain_id)

    def set_conversation(self, chat_id: str, conversation_id: str | None) -> None:
        """Remember HA's conversation id so the model keeps the context."""
        if chat := self.chats.get(chat_id):
            chat["conversation_id"] = conversation_id
            self._save()

    @callback
    def delete(self, chat_id: str) -> None:
        """Remove a chat."""
        if self.chats.pop(chat_id, None) is not None:
            self._save()
            async_notify(self.hass, "chats", domain=self.domain_id)
