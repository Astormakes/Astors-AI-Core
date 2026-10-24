"""Chat: a chat in the sidebar. What you write leaves at `said` with the conversation so far; what comes back into
`answers` is shown and kept. It owns its chats."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.util import dt as dt_util

from ..message import Message, new_id
from . import Card, register

WAIT = 600  # seconds a chat waits for its answer


@register
class Chat(Card):
    """A chat."""

    type = "chat"
    title = "Chat"
    category = "In and out"
    help = "A chat in the sidebar: what you write goes out at 'said', answers wired back into 'answers' show up here."
    inputs = {"answers": "text"}
    outputs = {"said": "text"}
    page = "chat"
    keeps_data = True
    tags = {"said": "what you wrote in this chat", "stop": "you pressed Stop"}
    settings_schema = {
        "turns": {"type": "number", "title": "Conversation that goes along (turns)", "default": 0,
                  "help": "0 = the whole chat; otherwise only the last turns."},
    }
    functions = {
        "read": {"help": "The newest messages of the chat.", "args": {"turns": {"type": "integer", "help": "How many (default 10)."}}},
        "post": {"help": "Write a message into the chat as AAC (a reminder, a finding).",
                 "args": {"text": {"type": "string", "help": "What to write.", "required": True}}},
    }

    def default_data(self) -> dict[str, Any]:
        return {"chats": {}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._waiting: dict[str, asyncio.Future] = {}

    # ------------------------------------------------------------------ chats
    def chats(self) -> list[dict[str, Any]]:
        """Newest first, without their messages."""
        found = [{k: v for k, v in c.items() if k not in ("messages", "entries")} for c in self.data["chats"].values()]
        return sorted(found, key=lambda c: c["updated"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """One chat."""
        return self.data["chats"].get(chat_id)

    def _new(self, title: str, chat_id: str | None = None) -> dict[str, Any]:
        now = dt_util.utcnow().isoformat()
        chat = {"id": chat_id or new_id(), "title": title[:48] or "New chat", "created": now, "updated": now, "messages": [], "entries": []}
        self.data["chats"][chat["id"]] = chat
        return chat

    def _add(self, chat: dict[str, Any], role: str, text: str, **extra: Any) -> None:
        chat["messages"].append({"role": role, "text": text, "time": dt_util.utcnow().isoformat(), **extra})
        chat["updated"] = dt_util.utcnow().isoformat()

    async def delete(self, chat_id: str) -> None:
        """Delete a chat."""
        self.data["chats"].pop(chat_id, None)
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id)

    async def send(self, text: str, chat_id: str | None = None, create_as: str | None = None) -> tuple[str, str]:
        """You wrote something: it leaves at `said` with the conversation so far; returns (chat id, answer).
        create_as: the id a new chat gets (HA Assist: its conversation id)."""
        chat = self.get(chat_id) if chat_id else None
        chat = chat or self._new(text, create_as)
        entries = list(chat["entries"])
        if (turns := int(self.settings.get("turns") or 0)) > 0:  # only the last turns go along
            said = [i for i, e in enumerate(entries) if e["kind"] == "said"]
            entries = entries[said[-turns]:] if len(said) >= turns else entries
        message = Message(entries=[e for e in entries if e["kind"] != "system"], origin=self.id, chat_id=chat["id"])
        message.add(self.id, "said", text, ["said"])
        self._add(chat, "user", text)
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
        future = asyncio.get_running_loop().create_future()
        self._waiting[message.id] = future
        try:  # the whole way runs before send returns: no answer by then means none comes (nothing waits forever)
            await asyncio.wait_for(self.domain.flow.send(message, self.id, "said"), WAIT)
        except TimeoutError:
            pass
        finally:
            self._waiting.pop(message.id, None)
        if future.done():
            return chat["id"], future.result()
        answer = "(no answer came back: is a model wired back into this chat?)"
        self._add(chat, "aac", answer, error=True)
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
        return chat["id"], answer

    async def stop_chat(self, chat_id: str) -> None:
        """The chat's Stop button: its context goes out with a stop at the end (a busy model cuts its answer)."""
        chat = self.get(chat_id)
        if chat is None:
            return
        message = Message(entries=[e for e in chat["entries"] if e["kind"] != "system"], origin=self.id, chat_id=chat_id)
        message.add(self.id, "signal", "stop", ["stop"])
        self.hass.async_create_background_task(self.domain.flow.send(message, self.id, "said"), f"aac {self.id} stop")

    async def handle(self, message: Message, port: str, step) -> list[Any] | None:
        """An answer came back: shown and kept (the chat is the conversation as it now is)."""
        cut = (message.last("answer") or {}).get("state") == "cut"
        if message.stopped and not cut:  # a stop came through and nothing was busy: nothing to keep
            return []
        chat = self.get(message.chat_id or "")
        text = message.answer or ("" if cut else "(no answer)")
        if chat is not None and message.origin == self.id:
            # system parts come again from their cards; the rest only grows at the end (the server's cache)
            chat["entries"] = [e for e in message.entries if e["kind"] != "system"]
            self._add(chat, "aac", text, cut=cut or None)
            await self.save()
            self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
        if (future := self._waiting.get(message.id)) and not future.done():
            future.set_result(text)
        return []

    async def call(self, function: str, args: dict[str, Any], message: Message | None = None) -> Any:
        if function == "read":
            chat = self.get(message.chat_id) if message and message.chat_id else (self.chats() and self.get(self.chats()[0]["id"]))
            turns = int(args.get("turns") or 10)
            return [{"role": m["role"], "text": m["text"]} for m in (chat or {}).get("messages", [])[-turns:]]
        if function == "post":
            chats = self.chats()
            chat = self.get(chats[0]["id"]) if chats else self._new("From AAC")
            self._add(chat, "aac", str(args.get("text") or ""))
            await self.save()
            self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
            return {"posted": True}
        return await super().call(function, args, message)
