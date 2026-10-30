"""Chat: a chat in the sidebar. It holds a context per chat (its messages and events, docs/v2 page 3; the newest turn's
system parts and tools, and the request the model got: its page shows the context and the text as the model read it). What you write is added to it and the whole context leaves
at `said`; a context that comes into `answers` replaces what the chat holds (an answer to what you wrote, or
a turn that started elsewhere, e.g. a spoken line a Relay put on top of this chat's context). What the chat shows is
read from the context it holds. A dotted wire from `provides` lets another card read the current chat's context."""

from __future__ import annotations

import asyncio
import re
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Client, Context, is_stop, new_id, register

VERSION = "2.1.1"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

WAIT = 600  # seconds a chat waits for its answer
NO_ANSWER = "(no answer came back: is a model wired back into this chat?)"
SILENT = ("nothing", "nothing.")  # the answer to a line that was not meant for AAC: nothing is kept


ARCHIVE = "archive"  # the sidebar entry (and page) of the archived chats

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
    ui = True
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
        "read": {"help": "The newest messages of a chat (default: this one).",
                 "args": {"turns": {"type": "integer", "help": "How many (default 10)."},
                          "chat": {"type": "string", "help": "A chat's id (from chats)."}}},
        "chats": {"help": "The chats, newest first: id, title, when, the first thing said (archived ones are marked; they "
                          "cannot be read or continued); with a search only those that mention it.", "args": {"search": {"type": "string", "help": "Words to look for (optional)."}}},
        "continue": {"help": "Switch to an older chat: when this turn is done, that chat is the current one and this turn "
                             "(the asking and the switch) is not kept; the next turns (spoken ones too) go on there.",
                     "args": {"chat": {"type": "string", "help": "Its id (from chats).", "required": True}}},
        "post": {"help": "Write a message into the chat as AAC (a reminder, a finding).",
                 "args": {"text": {"type": "string", "help": "What to write.", "required": True}}},
    }

    def default_data(self) -> dict[str, Any]:
        return {"chats": {}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._waiting: dict[str, asyncio.Future] = {}
        self._switch: dict[str, str] = {}  # turn → the chat it switches to (continue: done when the turn is finished)
        self._switched: dict[str, str] = {}  # turn → the chat it switched to (send returns that one)

    async def start(self) -> None:
        if self.settings.get("ha_agent"):  # the core's "ha_conversation" channel: AAC as HA's conversation agent
            self.core.subscribe("ha_conversation", self, self.converse)

    async def converse(self, text: str, conversation_id: str | None, where: str) -> str:
        """One turn of an HA conversation: a chat of its own ("Assist · where · time"), a follow-up continues it."""
        _, answer = await self.send(text, chat_id=conversation_id, create_as=conversation_id,
                                    title=f"Assist · {where} · {dt_util.now().strftime('%H:%M')}", via="Assist", mic_name=where)
        return answer

    # ------------------------------------------------------------------ chats
    def nav(self) -> dict[str, Any]:
        """Its chats (each with delete, archive, rename), and the archived ones in "Archive (n)" at the end."""
        acts = lambda archived: [{"id": "delete", "label": "Delete", "icon": "🗑"},  # noqa: E731
                                 {"id": "restore" if archived else "archive", "label": "Restore" if archived else "Archive"},
                                 {"id": "rename", "label": "Rename…", "ask": "New name"}]
        chats = self.chats()
        live = [{"id": c["id"], "title": c["title"], "actions": acts(False)} for c in chats if not c.get("archived")]
        old = [c for c in chats if c.get("archived")]
        return {"add": "New chat", "entries": live + ([{"id": ARCHIVE, "title": f"Archive ({len(old)})"}] if old else [])}

    async def ui_call(self, action: str, args: dict[str, Any]) -> Any:
        if action == "nav_action":  # from its sidebar entries: delete, archive, restore, rename
            chat = self.data["chats"].get(str(args.get("entry") or ""))
            what = args.get("action")
            if chat is None:
                raise ValueError("no such chat")
            if what == "delete":
                return await self.delete(chat["id"])
            if what in ("archive", "restore"):
                chat["archived"] = what == "archive"
            elif what == "rename" and str(args.get("value") or "").strip():
                chat["title"] = str(args["value"]).strip()[:48]
            await self.save()
            self.core.notify("chat", domain=self.domain.id, card=self.id)
            return {"done": what}
        if action == "archived":  # the Archive page: archived chats, newest first
            return [c for c in self.chats() if c.get("archived")]
        if action == "as_read":  # Plain text: the newest request as the model read it
            return await self.as_read(str(args.get("chat_id") or ""))
        if action == "get":  # its page: one chat, writing, deleting, Stop
            return self.get(args["chat_id"])
        if action == "send":
            chat_id, answer = await self.send(str(args["text"]), args.get("chat_id"))
            return {"chat_id": chat_id, "answer": answer}
        if action == "delete":
            return await self.delete(args["chat_id"])
        if action == "stop":
            return await self.stop_chat(args["chat_id"])
        return await super().ui_call(action, args)

    def chats(self) -> list[dict[str, Any]]:
        """Newest first, without their contexts."""
        found = [{k: v for k, v in c.items() if k not in ("context", "last")} for c in self.data["chats"].values()]
        return sorted(found, key=lambda c: c["updated"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """One chat, with what it shows (read from the context it holds)."""
        chat = self.data["chats"].get(chat_id)
        if chat is None:
            return None
        context, last = self._context(chat), chat.get("last") or {}
        behind = {**context.summary(), "system": last.get("system", []), "tools": last.get("tools", [])}  # the newest turn's
        return {**{k: v for k, v in chat.items() if k not in ("context", "last")}, "messages": shown(context), "behind": behind}

    async def as_read(self, chat_id: str) -> dict[str, Any]:
        """The chat's newest request as the model read it: the model server writes it with the model's chat template
        (llama-server); a server that cannot: the request as plain text."""
        request = ((self.data["chats"].get(chat_id) or {}).get("last") or {}).get("request")
        if not request:
            return {"text": None}
        model = self.core.pool.get(request["model"]) or {}
        text = await Client(self.hass, model).template(request["body"]) if model else None
        return {"text": text or plain(request["body"]), "template": bool(text), "model": model.get("name") or request["model"],
                "reply": request.get("reply")}

    def _context(self, chat: dict[str, Any]) -> Context:
        """What a chat holds, as a context (a copy)."""
        held = deepcopy(chat["context"])
        return Context(messages=held["messages"], events=held["events"], origin=self.id, chat_id=chat["id"])

    def _new(self, title: str, chat_id: str | None = None) -> dict[str, Any]:
        now = dt_util.utcnow().isoformat()
        chat = {"id": chat_id or new_id(), "title": title[:48] or "New chat", "created": now, "updated": now,
                "context": {"messages": [], "events": []}}
        self.data["chats"][chat["id"]] = chat
        return chat

    async def _hold(self, chat: dict[str, Any], context: Context) -> None:
        """The chat now holds this context: its messages and events (a turn that went out with only the last turns
        keeps the older ones), and the newest turn's system parts and tools."""
        if (chat := self.data["chats"].get(chat["id"])) is None:  # the stored one (get() gives a copy); deleted meanwhile: gone
            return
        held, kept = context.held(), chat["context"]["messages"]
        ids = [m["id"] for m in kept]
        if held["messages"] and held["messages"][0]["id"] in ids[1:]:  # it went out with only the last turns
            start = ids.index(held["messages"][0]["id"])
            held["messages"] = [*deepcopy(kept[:start]), *held["messages"]]
            held["events"] = [{**e, "at": e.get("at", 0) + start} for e in held["events"]]
        chat["context"] = held
        if context.system or context.tools or context.request:
            chat["last"] = {"system": deepcopy(context.system), "tools": deepcopy(context.tools),
                            **({"request": deepcopy(context.request)} if context.request else {})}
        chat["updated"] = dt_util.utcnow().isoformat()
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])

    def current(self) -> dict[str, Any] | None:
        """The chat that cards reading this one continue: the newest, unless it has been quiet too long."""
        chats = [c for c in self.chats() if not c.get("archived")]  # an archived chat is never the current one
        if not chats:
            return None
        quiet = timedelta(minutes=float(self.settings.get("new_after") or 20))
        return self.get(chats[0]["id"]) if dt_util.utcnow() - datetime.fromisoformat(chats[0]["updated"]) <= quiet else None

    def held(self, port: str) -> Context | None:
        """What a dotted wire from `provides` reads: the current chat's context (empty: the next turn starts a new chat)."""
        if port != "provides":
            return None
        chat = self.current()
        return self._context(self.data["chats"][chat["id"]]) if chat else Context(origin=self.id)

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
        chat = self.data["chats"].get(chat_id or "") or self._new(title or text, create_as)
        held = self._context(chat)
        held.add(self.id, "said", text, ["said"], **data)
        await self._hold(chat, held)
        context = held.branch()
        if (turns := int(self.settings.get("turns") or 0)) > 0:  # only the last turns go along
            said = [i for i, m in enumerate(context.messages) if m["kind"] == "said"]
            if len(said) > turns:
                start = said[-turns]
                context.messages = context.messages[start:]
                context.events = [{**e, "at": e.get("at", 0) - start} for e in context.events if e.get("at", 0) >= start]
        future = asyncio.get_running_loop().create_future()
        self._waiting[context.id] = future
        try:  # the whole way runs before send returns: no answer by then means none comes (nothing waits forever)
            await asyncio.wait_for(self.domain.flow.send(context, self.id, "said"), WAIT)
        except TimeoutError:
            pass
        finally:
            self._waiting.pop(context.id, None)
        if future.done():
            return self._switched.pop(context.id, chat["id"]), future.result()
        held = self._context(chat)
        held.add(self.id, "error", NO_ANSWER, ["error"])
        await self._hold(chat, held)
        return chat["id"], NO_ANSWER

    async def stop_chat(self, chat_id: str) -> None:
        """The chat's Stop button: its context goes out with a stop at the end (a busy model cuts its answer)."""
        if (chat := self.data["chats"].get(chat_id)) is None:
            return
        context = self._context(chat)
        context.add(self.id, "signal", "stop", ["stop"])
        self.hass.async_create_background_task(self.domain.flow.send(context, self.id, "said"), f"aac {self.id} stop")

    async def progress(self, context: Context, port: str) -> list[Any] | None:
        """A turn still being worked on: the chat shows it at once (a spoken question too; the answer grows live in
        the panel). A turn that ends in "nothing" goes again when it is finished."""
        if context.origin != self.id or port != "answers" or context.id in self._switch:  # a switching turn is not kept
            return []
        await self._keep_turn(context)
        return []

    async def _keep_turn(self, context: Context) -> None:
        chat = self.data["chats"].get(context.chat_id or context.id)  # a turn without a chat gets one named after it (snapshots too)
        if chat is None:  # a turn built on an empty read (quiet for long): a new chat
            said = context.last("said") or {}
            name = said.get("data", {}).get("mic_name")
            chat = self._new(f"Voice · {name} · {dt_util.now().strftime('%H:%M')}" if name else said.get("text", ""), context.chat_id or context.id)
        await self._hold(chat, context)

    async def _do_switch(self, context: Context, target: str) -> None:
        """A turn asked to go on in another chat: the chat it came from drops the turn (and goes if nothing is left),
        the other chat is the current one."""
        here = self.data["chats"].get(context.chat_id or context.id)
        if here is not None and here["id"] != target:
            self._drop_turn(here, context)
        self._switched[context.id] = target
        while len(self._switched) > 20:
            self._switched.pop(next(iter(self._switched)))
        await self._make_current(target)

    def _drop_turn(self, chat: dict[str, Any], context: Context) -> None:
        """The chat forgets a turn it showed (from its newest said line on), and goes if nothing is left."""
        said = next((m["id"] for m in reversed(context.messages) if m["kind"] == "said"), None)
        held = chat["context"]
        ids = [m["id"] for m in held["messages"]]
        if said in ids:
            cut = ids.index(said)
            held["messages"] = held["messages"][:cut]
            held["events"] = [e for e in held["events"] if e.get("at", 0) <= cut]
        if not any(m["kind"] == "said" for m in held["messages"]):
            self.data["chats"].pop(chat["id"])

    async def _make_current(self, chat_id: str) -> None:
        self.data["chats"][chat_id]["updated"] = dt_util.utcnow().isoformat()  # the newest: the current one
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, switched=chat_id)

    async def handle(self, context: Context, port: str, step) -> list[Any] | None:
        """A context came in: it replaces what the chat holds (a new chat if it belongs to none yet)."""
        if (target := self._switch.pop(context.id, None)) is not None:
            await self._do_switch(context, target)
            if (future := self._waiting.get(context.id)) and not future.done():
                future.set_result(context.answer)
            return []
        cut = (context.last("answer") or {}).get("state") == "cut"
        if context.stopped and not cut:  # a stop came through and nothing was busy: nothing to keep
            return []
        error = next((e["text"] for e in reversed(context.entries) if e["kind"] == "error"), "")
        text = context.answer or error or ("" if cut else "(no answer)")
        if context.origin == self.id and text.strip().lower() not in SILENT:
            await self._keep_turn(context)
        elif context.origin == self.id and (chat := self.data["chats"].get(context.chat_id or context.id)) is not None:
            self._drop_turn(chat, context)  # "nothing": it was not meant for AAC, the chat shows it no longer
            await self.save()
            self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])
        if (future := self._waiting.get(context.id)) and not future.done():
            future.set_result(text)
        return []

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "chats":
            plain = lambda t: re.sub(r"[^\w]+", " ", t.lower())  # noqa: E731 - "13.13" finds "13:13"
            words = plain(str(args.get("search") or "")).split()
            found = []
            for c in self.chats():
                held = self.data["chats"][c["id"]]["context"]["messages"]
                said = next((m["text"] for m in held if m["kind"] == "said"), "")
                text = plain(" ".join([c["title"], *(m["text"] for m in held)]))
                if all(w in text for w in words):
                    found.append({"id": c["id"], "title": c["title"], "updated": c["updated"], "first": said[:200],
                                  **({"archived": True} if c.get("archived") else {})})
            return found[:20]
        if function == "continue":
            chat = self.data["chats"].get(str(args.get("chat") or ""))
            if chat is None:
                return {"error": "no such chat (see chats)"}
            if chat.get("archived"):
                return {"error": "that chat is archived (the owner can restore it)"}
            if context is None:  # not from a turn (a Relay's script without one): it is the current chat now
                await self._make_current(chat["id"])
            else:
                self._switch[context.id] = chat["id"]  # done when this turn comes back finished
                while len(self._switch) > 20:  # turns that never came back
                    self._switch.pop(next(iter(self._switch)))
            return {"switching_to": chat["title"]}
        if function == "read":
            if args.get("chat") and (self.data["chats"].get(str(args["chat"])) or {}).get("archived"):
                return {"error": "that chat is archived (the owner can restore it)"}
            chat = (self.get(str(args["chat"])) if args.get("chat") else None) or \
                (self.get(context.chat_id) if context and context.chat_id else (self.chats() and self.get(self.chats()[0]["id"])))
            turns = int(args.get("turns") or 10)
            return [{"role": m["role"], "text": m["text"]} for m in (chat or {}).get("messages", [])[-turns:]]
        if function == "post":
            chats = self.chats()
            chat = self.data["chats"][chats[0]["id"]] if chats else self._new("From AAC")
            held = self._context(chat)
            held.add(self.id, "answer", str(args.get("text") or ""))
            await self._hold(chat, held)
            return {"posted": True}
        return await super().call(function, args, context)


def shown(context: Context) -> list[dict[str, Any]]:
    """What a chat shows, read from its context: what people said, the answers (cut off where a stop came), the tools
    a model called with what came back, errors."""
    out: list[dict[str, Any]] = []
    results = {m["data"].get("call_id"): m["text"] for m in context.messages if m["kind"] == "result"}
    for e in context.entries:
        if e["kind"] == "said":
            out.append({"role": "user", "text": e["text"], "time": e["time"], **({"mic": e["data"]["mic_name"]} if e["data"].get("mic_name") else {})})
        elif e["kind"] == "answer" and e.get("state") != "open":
            calls = [{"name": c["name"], "args": c.get("args") or {}, "result": results.get(c["id"])} for c in e["data"].get("calls") or []]
            out.append({"role": "aac", "text": e["text"], "time": e["time"], "by": e["by"],
                        **({"calls": calls} if calls else {}), **({"cut": True} if e.get("state") == "cut" else {})})
        elif e["kind"] == "error":
            out.append({"role": "aac", "text": e["text"], "time": e["time"], "error": True})
        elif is_stop(e) and out and out[-1]["role"] == "aac":
            out[-1]["cut"] = True
    return out


def plain(body: dict[str, Any]) -> str:
    """A request as plain text (for a server that cannot show it with the model's template)."""
    out = []
    for m in body.get("messages") or []:
        calls = "".join(f"\n  calls {c['function']['name']}({c['function']['arguments']})" for c in m.get("tool_calls") or [])
        out.append(f"[{m['role']}{' ' + m['name'] if m.get('name') else ''}]\n{m.get('content') or ''}{calls}")
    tools = [f"  {t['function']['name']}: {t['function'].get('description', '')}" for t in body.get("tools") or []]
    if tools:  # after the system part, as chat templates put them
        at = 1 if (body.get("messages") or [{}])[0].get("role") == "system" else 0
        out.insert(at, "[tools]\n" + "\n".join(tools))
    return "\n\n".join(out)
