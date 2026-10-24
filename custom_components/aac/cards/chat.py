"""Chat: a chat in the sidebar. It holds a context per chat. What you write is added to it and the whole context
leaves at `said`; a context that comes into `answers` replaces what the chat holds (an answer to what you wrote, or
a turn that started elsewhere, e.g. a spoken line a Relay put on top of this chat's context). What the chat shows is
read from the context it holds. A dotted wire from `provides` lets another card read the current chat's context."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, is_stop, new_id, register

VERSION = "1.1.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

WAIT = 600  # seconds a chat waits for its answer
NO_ANSWER = "(no answer came back: is a model wired back into this chat?)"
SILENT = ("nothing", "nothing.")  # the answer to a line that was not meant for AAC: nothing is kept


@register
class Chat(Card):
    """A chat."""

    type = "chat"
    title = "Chat"
    category = "In and out"
    help = ("A chat in the sidebar. It holds the conversation (a context per chat): what you write is added and the "
            "whole context goes out at 'said'; a context coming into 'answers' replaces what it holds and shows up.")
    inputs = {"answers": "text"}
    outputs = {"said": "text", "provides": "read"}
    ports_help = {"answers": "a context that replaces what the chat holds (the answer to what you wrote, or a spoken "
                             "turn built on this chat's context); its new lines and answers show up in the chat",
                  "said": "what you wrote, with the conversation so far",
                  "provides": "the current chat's context, for cards that read it (dotted): the newest chat, or a new one "
                              "after the quiet time"}
    page = "chat"
    keeps_data = True
    tags = {"said": "what you wrote in this chat", "stop": "you pressed Stop", "error": "nothing came back"}
    settings_schema = {
        "turns": {"type": "number", "title": "Conversation that goes along (turns)", "default": 0,
                  "help": "0 = the whole chat; otherwise only the last turns."},
        "ha_agent": {"type": "bool", "title": "Takes Home Assistant's conversations", "default": False,
                     "help": "When AAC is the agent in an HA Assist pipeline (the HA app, voice devices, automations), each "
                             "conversation becomes a chat here ('Assist · …'). It takes the core's ha_conversation channel."},
        "new_after": {"type": "number", "title": "A new chat after quiet (minutes)", "default": 20,
                      "help": "For cards that read this chat (a spoken line): after this long without a turn, the "
                              "next one starts a new chat."},
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

    async def start(self) -> None:
        if self.settings.get("ha_agent"):  # the core's "ha_conversation" channel: AAC as HA's conversation agent
            self.core.subscribe("ha_conversation", self, self.converse)

    async def converse(self, text: str, conversation_id: str | None, where: str) -> str:
        """One turn of an HA conversation: a chat of its own ("Assist · where · time"), a follow-up continues it."""
        _, answer = await self.send(text, chat_id=conversation_id, create_as=conversation_id,
                                    title=f"Assist · {where} · {dt_util.now().strftime('%H:%M')}", via="Assist", mic_name=where)
        return answer

    # ------------------------------------------------------------------ chats
    def chats(self) -> list[dict[str, Any]]:
        """Newest first, without their contexts."""
        found = [{k: v for k, v in c.items() if k not in ("messages", "entries")} for c in self.data["chats"].values()]
        return sorted(found, key=lambda c: c["updated"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """One chat, with what it shows (read from the context it holds)."""
        chat = self.data["chats"].get(chat_id)
        if chat is not None:
            chat["messages"] = shown(chat["entries"])
        return chat

    def _new(self, title: str, chat_id: str | None = None) -> dict[str, Any]:
        now = dt_util.utcnow().isoformat()
        chat = {"id": chat_id or new_id(), "title": title[:48] or "New chat", "created": now, "updated": now, "entries": []}
        self.data["chats"][chat["id"]] = chat
        return chat

    async def _hold(self, chat: dict[str, Any], entries: list[dict[str, Any]]) -> None:
        """The chat now holds this context (system parts come again from their cards each turn)."""
        chat["entries"] = [e for e in entries if e["kind"] != "system"]
        chat["updated"] = dt_util.utcnow().isoformat()
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])

    def current(self) -> dict[str, Any] | None:
        """The chat that cards reading this one continue: the newest, unless it has been quiet too long."""
        chats = self.chats()
        if not chats:
            return None
        quiet = timedelta(minutes=float(self.settings.get("new_after") or 20))
        return self.get(chats[0]["id"]) if dt_util.utcnow() - datetime.fromisoformat(chats[0]["updated"]) <= quiet else None

    def held(self, port: str) -> Context | None:
        """What a dotted wire from `provides` reads: the current chat's context (empty: the next turn starts a new chat)."""
        if port != "provides":
            return None
        chat = self.current()
        return Context(entries=deepcopy(chat["entries"]) if chat else [], origin=self.id, chat_id=chat["id"] if chat else None)

    async def delete(self, chat_id: str) -> None:
        """Delete a chat."""
        self.data["chats"].pop(chat_id, None)
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id)

    async def send(self, text: str, chat_id: str | None = None, create_as: str | None = None, title: str | None = None,
                   **data: Any) -> tuple[str, str]:
        """Something was written: added to what the chat holds, the context leaves at `said`; returns (chat id, answer).
        create_as / title: the id and title a new chat gets (HA Assist: its conversation id, "Assist · …"); data: on the
        line (where it came from)."""
        chat = (self.get(chat_id) if chat_id else None) or self._new(title or text, create_as)
        entries = list(chat["entries"])
        if (turns := int(self.settings.get("turns") or 0)) > 0:  # only the last turns go along
            said = [i for i, e in enumerate(entries) if e["kind"] == "said"]
            entries = entries[said[-turns]:] if len(said) >= turns else entries
        context = Context(entries=entries, origin=self.id, chat_id=chat["id"])
        line = context.add(self.id, "said", text, ["said"], **data)
        await self._hold(chat, [*chat["entries"], line])
        future = asyncio.get_running_loop().create_future()
        self._waiting[context.id] = future
        try:  # the whole way runs before send returns: no answer by then means none comes (nothing waits forever)
            await asyncio.wait_for(self.domain.flow.send(context, self.id, "said"), WAIT)
        except TimeoutError:
            pass
        finally:
            self._waiting.pop(context.id, None)
        if future.done():
            return chat["id"], future.result()
        await self._hold(chat, [*chat["entries"], {**line, "id": new_id(), "kind": "error", "text": NO_ANSWER, "tags": [self.tag("error")]}])
        return chat["id"], NO_ANSWER

    async def stop_chat(self, chat_id: str) -> None:
        """The chat's Stop button: its context goes out with a stop at the end (a busy model cuts its answer)."""
        chat = self.get(chat_id)
        if chat is None:
            return
        context = Context(entries=list(chat["entries"]), origin=self.id, chat_id=chat_id)
        context.add(self.id, "signal", "stop", ["stop"])
        self.hass.async_create_background_task(self.domain.flow.send(context, self.id, "said"), f"aac {self.id} stop")

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        """A context came in: it replaces what the chat holds (a new chat if it belongs to none yet)."""
        cut = (context.last("answer") or {}).get("state") == "cut"
        if context.stopped and not cut:  # a stop came through and nothing was busy: nothing to keep
            return []
        text = context.answer or ("" if cut else "(no answer)")
        if context.origin == self.id and text.strip().lower() not in SILENT:
            chat = self.get(context.chat_id or "")
            if chat is None:  # a turn built on an empty read (quiet for long): a new chat
                said = context.last("said") or {}
                name = said.get("data", {}).get("mic_name")
                chat = self._new(f"Voice · {name} · {dt_util.now().strftime('%H:%M')}" if name else said.get("text", ""), context.chat_id)
            await self._hold(chat, context.entries)
        if (future := self._waiting.get(context.id)) and not future.done():
            future.set_result(text)
        return []

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "read":
            chat = self.get(context.chat_id) if context and context.chat_id else (self.chats() and self.get(self.chats()[0]["id"]))
            turns = int(args.get("turns") or 10)
            return [{"role": m["role"], "text": m["text"]} for m in (chat or {}).get("messages", [])[-turns:]]
        if function == "post":
            chats = self.chats()
            chat = self.get(chats[0]["id"]) if chats else self._new("From AAC")
            post = Context()
            post.add(self.id, "answer", str(args.get("text") or ""))
            await self._hold(chat, [*chat["entries"], *post.entries])
            return {"posted": True}
        return await super().call(function, args, context)


def shown(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a chat shows, read from its context: what people said, the answers (cut off where a stop came), errors."""
    out: list[dict[str, Any]] = []
    for e in entries:
        if e["kind"] == "said":
            out.append({"role": "user", "text": e["text"], "time": e["time"], **({"mic": e["data"]["mic_name"]} if e["data"].get("mic_name") else {})})
        elif e["kind"] == "answer" and e.get("state") != "open":
            out.append({"role": "aac", "text": e["text"], "time": e["time"], **({"cut": True} if e.get("state") == "cut" else {})})
        elif e["kind"] == "error":
            out.append({"role": "aac", "text": e["text"], "time": e["time"], "error": True})
        elif is_stop(e) and out and out[-1]["role"] == "aac":
            out[-1]["cut"] = True
    return out
