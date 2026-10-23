"""The user's chats with AAC (several, kept across restarts).

A chat is a document (spec/graph.md): `entries` is the whole conversation as the graph wrote it (calls and
results included), `messages` what the panel shows (each pointing at its entry). Editing a message edits
the document; the next message sends the edited document.
Automatic cards (Heartbeat, Triggers, Speech to text) may keep chats of their own, marked `auto` (listed under
Auto in the sidebar), so proactive messages have a home.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .runs import async_notify

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
        return {k: v for k, v in chat.items() if k not in ("messages", "entries")}

    def list(self) -> list[dict[str, Any]]:
        """All chats, newest activity first."""
        chats = [self.summary(c) for c in self.chats.values()]
        return sorted(chats, key=lambda c: c["updated"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """One chat with messages."""
        return self.chats.get(chat_id)

    @callback
    def create(self, title: str = "New chat", chat_id: str | None = None, auto: bool = False) -> dict[str, Any]:
        """Start a chat (auto: an automatic card's, listed under Auto in the sidebar)."""
        now = dt_util.utcnow().isoformat()
        chat = {
            "id": chat_id or uuid.uuid4().hex[:12],
            "title": title,
            "created": now,
            "updated": now,
            "conversation_id": None,
            "unread": 0,
            "messages": [],
            **({"auto": True} if auto else {}),
        }
        self.chats[chat["id"]] = chat
        self._save()
        async_notify(self.hass, "chats", domain=self.domain_id)
        return chat

    @callback
    def add_message(self, chat_id: str, role: str, text: str, **extra: Any) -> dict[str, Any]:
        """Append a message ('user' or 'aac'); AAC's own messages count unread."""
        chat = self.chats.get(chat_id)
        if chat is None:
            chat = self.create("Chat", chat_id)
        message = {"role": role, "text": text, "ts": dt_util.utcnow().isoformat(), **extra}
        chat.pop("archived", None)  # a new message brings an archived chat back
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

    def document(self, chat_id: str) -> list[dict[str, Any]]:
        """The chat's conversation as a document (a copy, to send through the graph)."""
        chat = self.chats.get(chat_id)
        return deepcopy(chat.get("entries") or []) if chat else []

    @callback
    def set_entries(self, chat_id: str, entries: list[dict[str, Any]]) -> None:
        """The conversation as the graph left it."""
        if chat := self.chats.get(chat_id):
            chat["entries"] = deepcopy(entries)
            self._save()

    @callback
    def rewind(self, chat_id: str, index: int) -> None:
        """Cut the chat off before message `index` (edit or rerun), the document before that message's entry."""
        if chat := self.chats.get(chat_id):
            cut = chat["messages"][index] if 0 <= index < len(chat["messages"]) else None
            chat["messages"] = chat["messages"][:max(0, index)]
            entries = chat.get("entries") or []
            at = next((i for i, e in enumerate(entries) if cut and e["id"] == cut.get("entry")), None)
            if at is not None:
                chat["entries"] = entries[:at]
            elif not chat["messages"]:
                chat["entries"] = []
            chat["conversation_id"] = None
            self._save()
            async_notify(self.hass, "chat", chat_id=chat_id, domain=self.domain_id)

    @callback
    def edit(self, chat_id: str, index: int, text: str) -> None:
        """Change a message in place (also AAC's answers) and its entry in the document; later ones stay."""
        if (chat := self.chats.get(chat_id)) and 0 <= index < len(chat["messages"]):
            message = chat["messages"][index] = {**chat["messages"][index], "text": text, "edited": True}
            for item in chat.get("entries") or []:
                if item["id"] == message.get("entry"):
                    item["text"] = text
                    item["data"].pop("own_words", None)
            chat["conversation_id"] = None
            self._save()
            async_notify(self.hass, "chat", chat_id=chat_id, domain=self.domain_id)

    def set_conversation(self, chat_id: str, conversation_id: str | None) -> None:
        """Remember HA's conversation id so the model keeps the context."""
        if chat := self.chats.get(chat_id):
            chat["conversation_id"] = conversation_id
            self._save()

    @callback
    def archive(self, chat_id: str, archived: bool = True) -> None:
        """Move a chat into the archive (the owner's bin: kept, out of the way) or back."""
        if (chat := self.chats.get(chat_id)) is None:
            return
        if archived:
            chat["archived"] = True
        else:
            chat.pop("archived", None)
        self._save()
        async_notify(self.hass, "chats", domain=self.domain_id)

    @callback
    def delete(self, chat_id: str) -> None:
        """Remove a chat."""
        if self.chats.pop(chat_id, None) is not None:
            self._save()
            async_notify(self.hass, "chats", domain=self.domain_id)


def spoken(message: dict[str, Any]) -> str:
    """A message as the model sees it: the owner's spoken lines say so (per message, chats can mix).
    AAC's own answers stay plain: marked ones were copied by the model into its next answers."""
    if message.get("via") != "voice" or message["role"] != "user":
        return strip_marks(message["text"]) if message["role"] == "aac" else message["text"]
    how = f"by {message['voice']}" if message.get("voice") else ""
    return f"[spoken at the microphone '{message.get('mic') or '?'}'{f' {how}' if how else ''}] {message['text']}"


# Notes a model copied from marked history ("[you said this aloud]", "(you said this out loud)")
_MARKS = re.compile(r"^\s*[\[(]\s*(you said this (aloud|out loud)|said aloud|spoken[^\])]*)\s*[\])]\s*", re.IGNORECASE)


def strip_marks(text: str) -> str:
    """An answer without such copied notes at its start."""
    return _MARKS.sub("", text or "", count=1)
