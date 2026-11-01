"""Chat (docs/v2 page 5): chats in the sidebar; each holds a context.

What you write is added (`chat.said`) and the chat's context goes out at `said`. Every context that comes into
`answers` is kept by the chat it belongs to: the one whose entry `<this card>.id.<chat id>` it holds. A context without
one is filed once it is finished (nothing streaming, no call waiting): a new chat, which writes its entry; until then
it shows under "Being answered…" (one at a time). A dotted wire from `provides` reads the current chat's context: a turn built on it goes on there. The chat shows
the entries its "Shown" setting lists; those "Shown as you" on your side.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import re
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Client, Context, matches, new_id, register

VERSION = "3.0.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

WAIT = 600  # seconds what you write may take to be answered
NOT_WIRED = "Nothing is wired to this chat's output 'said': what you write goes nowhere."
ARCHIVE = "archive"  # the sidebar entry (and page) of the archived chats
ANSWERING = "answering"  # the sidebar entry of a turn that belongs to no chat yet, while it is being answered


def patterns(text: str) -> list[str]:
    """A setting's tags, comma separated."""
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


@register
class Chat(Card):
    """Chats."""

    type = "chat"
    title = "Chat"
    category = "In and out"
    help = ("Chats in the sidebar, each holding a context. What you write is added and the chat's context goes out at "
            "'said'. Every context coming into 'answers' is kept: by the chat whose id entry it holds, else as a new chat "
            "once it is finished (until then under Being answered…). It shows the entries its Shown setting lists.")
    inputs = {"answers": "text"}
    outputs = {"said": "text", "provides": "read"}
    ports_help = {"answers": "every context that comes in is kept: by the chat whose id entry (<this card>.id.<chat id>) "
                             "it holds, else as a new chat once it is finished (nothing streaming, no call waiting)",
                  "said": "what you wrote, with the chat's context",
                  "provides": "the current chat's context, for cards that read it (dotted): the newest chat, or an empty "
                              "context after the quiet time"}
    ui = True
    keeps_data = True
    tags = {"id.<chat id>": "the chat a context belongs to (one per chat, no text)", "said": "what you wrote",
            "post": "written into a chat by a model or a script", "error": "nothing is wired to 'said'"}
    settings_schema = {
        "shown": {"type": "text", "title": "Shown", "default": "*.said, *.post, *.error",
                  "help": "The entries the chat shows (tags, comma separated; * stands for anything). The Context view "
                          "shows every entry."},
        "as_you": {"type": "text", "title": "Shown as you", "default": "chat.said, speech_to_text.said, phrase_joiner.said",
                   "help": "Of those, the ones on your side (what you typed or said); the others on the other side."},
        "ha_agent": {"type": "bool", "title": "Takes Home Assistant's conversations", "default": False,
                     "help": "When AAC is the agent in an HA Assist pipeline (the HA app, voice devices, automations), each "
                             "conversation becomes a chat here ('Assist · …'). It takes the core's ha_conversation channel."},
        "new_after": {"type": "number", "title": "A new chat after quiet (minutes)", "default": 20,
                      "help": "For cards that read this chat (a spoken line): after this long without anything new, they "
                              "read an empty context and their turn begins a new chat."},
    }
    functions = {
        "read": {"help": "The newest messages of a chat (default: this one).",
                 "args": {"turns": {"type": "integer", "help": "How many (default 10)."},
                          "chat": {"type": "string", "help": "A chat's id (from chats)."}}},
        "chats": {"help": "The chats, newest first: id, title, when, the first thing said (archived ones are marked; they "
                          "cannot be read); with a search only those that mention it.",
                  "args": {"search": {"type": "string", "help": "Words to look for (optional)."}}},
        "post": {"help": "Write a message into the chat as AAC (a reminder, a finding).",
                 "args": {"text": {"type": "string", "help": "What to write.", "required": True}}},
    }

    def default_data(self) -> dict[str, Any]:
        return {"chats": {}}

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self._answering: Context | None = None  # a turn of no chat yet, until it is finished

    async def start(self) -> None:
        if self.settings.get("ha_agent"):  # the core's "ha_conversation" channel: AAC as HA's conversation agent
            self.core.subscribe("ha_conversation", self, self.converse)

    async def converse(self, text: str, conversation_id: str | None, where: str) -> str:
        """One turn of an HA conversation: a chat of its own ("Assist · where · time"), a follow-up continues it."""
        _, answer = await self.write(text, chat_id=conversation_id, create_as=conversation_id,
                                     title=f"Assist · {where} · {dt_util.now().strftime('%H:%M')}", via="Assist", mic_name=where)
        return answer

    # ------------------------------------------------------------------ which chat
    def _id_entry(self, context: Context) -> dict[str, Any] | None:
        return context.get(f"{self.id}.id.*")

    def chat_of(self, context: Context) -> dict[str, Any] | None:
        """The chat a context belongs to: the one its id entry names."""
        mine = self._id_entry(context)
        return self.data["chats"].get(mine["tag"][len(self.id) + 4:]) if mine is not None else None

    def _new(self, title: str, chat_id: str | None = None) -> dict[str, Any]:
        now = dt_util.utcnow().isoformat()
        chat = {"id": chat_id or new_id(), "title": title[:48] or "New chat", "created": now, "updated": now,
                "context": Context().to_dict()}
        self.data["chats"][chat["id"]] = chat
        return chat

    def _held(self, chat: dict[str, Any]) -> Context:
        """What a chat holds (a copy)."""
        return Context.from_dict(chat["context"])

    async def _keep(self, chat: dict[str, Any], context: Context) -> None:
        """The chat holds this context now (with its id entry); written to disk once nothing is being written."""
        if self.data["chats"].get(chat["id"]) is not chat:  # deleted meanwhile: gone
            return
        if self._id_entry(context) is None:
            context.add(self.tag(f"id.{chat['id']}"), at="first")
        chat["context"] = context.to_dict()
        chat["updated"] = dt_util.utcnow().isoformat()
        if not context.streaming:
            await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"])

    def current(self) -> dict[str, Any] | None:
        """The chat that cards reading this one continue: the newest, unless it has been quiet too long."""
        chats = [c for c in self.chats() if not c.get("archived")]
        if not chats:
            return None
        quiet = timedelta(minutes=float(self.settings.get("new_after") or 20))
        return chats[0] if dt_util.utcnow() - datetime.fromisoformat(chats[0]["updated"]) <= quiet else None

    def held(self, port: str) -> Context | None:
        """What a dotted wire from `provides` reads: the current chat's context (empty: the next turn begins a new chat)."""
        if port != "provides":
            return None
        chat = self.current()
        return self._held(self.data["chats"][chat["id"]]) if chat else Context()

    # ------------------------------------------------------------------ a context comes in
    async def handle(self, context: Context, port: str) -> None:
        if port != "answers":
            return
        if (chat := self.chat_of(context)) is not None:
            await self._keep(chat, context)
            return
        if context.streaming or context.waiting:  # no chat yet: shown while it is answered, filed once it is finished
            self._answering = context
            self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=ANSWERING)
            return
        self._answering = None
        chat = self._new(self._title(context))
        await self._keep(chat, context)
        self.core.notify("chat", domain=self.domain.id, card=self.id, chat_id=chat["id"], was=ANSWERING)

    def _title(self, context: Context) -> str:
        """A new chat's title: "Voice · <satellite> · HH:MM" for a heard line, else its first text."""
        name = next((e["data"]["mic_name"] for e in context.conversation if e["data"].get("mic_name")), None)
        first = next((e["text"] for e in context.conversation if self._shown(e) and e["text"].strip()), "")
        return f"Voice · {name} · {dt_util.now().strftime('%H:%M')}" if name else first

    # ------------------------------------------------------------------ writing in a chat
    async def write(self, text: str, chat_id: str | None = None, create_as: str | None = None, title: str | None = None,
                    **data: Any) -> tuple[str, str]:
        """Something was written: added to the chat, its context goes out at `said`; returns (chat id, answer).
        create_as / title: the id and title a new chat gets (HA Assist: its conversation id, "Assist · …"); data: on the
        line (where it came from)."""
        chat = self.data["chats"].get(chat_id or "") or self._new(title or text, create_as)
        held = self._held(chat)
        line = held.add(self.tag("said"), text, **data)
        await self._keep(chat, held)
        return chat["id"], await self._turn(chat, held, line["id"])

    async def _turn(self, chat: dict[str, Any], held: Context, line: str) -> str:
        """The chat's context goes out at `said`; the answer is what it shows after the line, not as you."""
        if not self.wired("said"):
            held.add(self.tag("error"), NOT_WIRED)
            await self._keep(chat, held)
            return NOT_WIRED
        try:  # the whole way runs before send returns: no answer by then means none came
            await asyncio.wait_for(self.send(held, "said"), WAIT)
        except TimeoutError:
            pass
        now = self._held(chat)
        after = next((i for i, e in enumerate(now.entries) if e["id"] == line), len(now.entries))
        found = [e for e in now.entries[after + 1:] if self._shown(e) and not self._as_you(e) and e["text"].strip()]
        return found[-1]["text"] if found else ""

    async def edit(self, chat_id: str, entry_id: str, text: str, resend: bool = False) -> dict[str, Any]:
        """The pencil: an entry the chat shows gets a new text. resend (only one of yours): what came after it goes and
        it goes out again from there."""
        if (chat := self.data["chats"].get(chat_id)) is None:
            raise ValueError("no such chat")
        held = self._held(chat)
        line = held.by_id(entry_id)
        if line is None or not self._shown(line) or not text.strip():
            raise ValueError("only something the chat shows can be edited, and not to nothing")
        line["text"], line["data"]["edited"] = text.strip(), True
        if not (resend and self._as_you(line)):
            await self._keep(chat, held)
            return {"chat_id": chat_id}
        held.entries = held.entries[:held.entries.index(line) + 1]
        await self._keep(chat, held)
        return {"chat_id": chat_id, "answer": await self._turn(chat, held, line["id"])}

    async def delete(self, chat_id: str) -> None:
        """Delete a chat."""
        self.data["chats"].pop(chat_id, None)
        await self.save()
        self.core.notify("chat", domain=self.domain.id, card=self.id)

    # ------------------------------------------------------------------ what it shows
    def _shown(self, e: dict[str, Any]) -> bool:
        return any(matches(e, p) for p in patterns(self.settings.get("shown")))

    def _as_you(self, e: dict[str, Any]) -> bool:
        return any(matches(e, p) for p in patterns(self.settings.get("as_you")))

    def shown(self, context: Context) -> list[dict[str, Any]]:
        """What a chat shows: the entries its settings list, on your side or the other; an entry's calls with their
        results; why one was cut."""
        results = {e["data"]["call_id"]: e["text"] for e in context.conversation if "call_id" in e["data"]}
        names = {c.id: c.name for c in self.all_cards()}
        out = []
        for e in context.conversation:
            calls = e["data"].get("calls") or []
            if not self._shown(e) or not (e["text"].strip() or calls or e["state"] == "cut"):
                continue
            mine, card = self._as_you(e), e["tag"].split(".", 1)[0]
            out.append({"id": e["id"], "role": "user" if mine else "aac", "text": e["text"], "time": e["time"],
                        "by": names.get(card, card),
                        **({"mic": e["data"]["mic_name"]} if mine and e["data"].get("mic_name") else {}),
                        **({"calls": [{"name": c["name"], "args": c.get("args") or {}, "result": results.get(c["id"])} for c in calls]} if calls else {}),
                        **({"thinking": e["data"]["thinking"]} if e["data"].get("thinking") else {}),
                        **({"cut": e["data"].get("cut") or "stopped"} if e["state"] == "cut" else {}),
                        **({"error": True} if e["tag"].endswith(".error") else {})})
        return out

    def chats(self) -> list[dict[str, Any]]:
        """Newest first, without their contexts."""
        found = [{k: v for k, v in c.items() if k != "context"} for c in self.data["chats"].values()]
        return sorted(found, key=lambda c: c["updated"], reverse=True)

    def get(self, chat_id: str) -> dict[str, Any] | None:
        """One chat, with what it shows and its whole context."""
        if chat_id == ANSWERING:
            context = self._answering or Context()
            return {"id": ANSWERING, "title": "Being answered…", "messages": self.shown(context), "behind": context.summary()}
        chat = self.data["chats"].get(chat_id)
        if chat is None:
            return None
        context = self._held(chat)
        request = self._request(context)
        return {**{k: v for k, v in chat.items() if k != "context"}, "messages": self.shown(context),
                "behind": context.summary(), **({"usage": request["usage"]} if request and request.get("usage") else {})}

    @staticmethod
    def _request(context: Context) -> dict[str, Any] | None:
        """The newest request a model wrote down (in its answer's data)."""
        return next((e["data"]["request"] for e in reversed(context.conversation) if e["data"].get("request")), None)

    async def as_read(self, chat_id: str) -> dict[str, Any]:
        """The chat's newest request as the model read it: the model server writes it with the model's chat template
        (llama-server); a server that cannot: the request as plain text; no model read it: the entries themselves."""
        chat = self.data["chats"].get(chat_id)
        if chat is None:
            return {"text": None}
        context = self._held(chat)
        request = self._request(context)
        if not request:
            return {"raw": True, "text": bool(context.conversation) or None, "entries": context.entries}
        model = self.core.pool.get(request["model"]) or {}
        text = await Client(self.hass, model).template(request["body"]) if model else None
        answers = [m["content"] for m in request["body"].get("messages") or [] if m["role"] == "assistant" and m.get("content")]
        return {"text": text or plain(request["body"]), "template": bool(text), "model": model.get("name") or request["model"],
                "reply": request.get("reply"), "answers": answers, "usage": request.get("usage")}

    # ------------------------------------------------------------------ its page and the sidebar
    def nav(self) -> dict[str, Any]:
        """Its chats (each with delete, archive, rename), and the archived ones in "Archive (n)" at the end."""
        acts = lambda archived: [{"id": "delete", "label": "Delete", "icon": "🗑"},  # noqa: E731
                                 {"id": "restore" if archived else "archive", "label": "Restore" if archived else "Archive"},
                                 {"id": "rename", "label": "Rename…", "ask": "New name"}]
        chats = self.chats()
        live = [{"id": c["id"], "title": c["title"], "actions": acts(False)} for c in chats if not c.get("archived")]
        old = [c for c in chats if c.get("archived")]
        answering = [{"id": ANSWERING, "title": "Being answered…"}] if self._answering is not None else []
        return {"add": "New chat", "fold": "arrow", "entries": answering + live + ([{"id": ARCHIVE, "title": f"Archive ({len(old)})"}] if old else [])}

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
        if action == "edit":  # the pencil
            return await self.edit(str(args["chat_id"]), str(args["id"]), str(args.get("text") or ""), bool(args.get("resend")))
        if action == "get":
            return self.get(args["chat_id"])
        if action == "send":
            chat_id, answer = await self.write(str(args["text"]), args.get("chat_id"))
            return {"chat_id": chat_id, "answer": answer}
        if action == "delete":
            return await self.delete(args["chat_id"])
        return await super().ui_call(action, args)

    # ------------------------------------------------------------------ for models
    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "chats":
            plain_ = lambda t: re.sub(r"[^\w]+", " ", t.lower())  # noqa: E731 - "13.13" finds "13:13"
            words = plain_(str(args.get("search") or "")).split()
            found = []
            for c in self.chats():
                held = self._held(self.data["chats"][c["id"]])
                first = next((e["text"] for e in held.conversation if self._as_you(e)), "")
                if all(w in plain_(" ".join([c["title"], *(e["text"] for e in held.conversation)])) for w in words):
                    found.append({"id": c["id"], "title": c["title"], "updated": c["updated"], "first": first[:200],
                                  **({"archived": True} if c.get("archived") else {})})
            return found[:20]
        if function == "read":
            wanted = self.data["chats"].get(str(args.get("chat") or ""))
            if wanted and wanted.get("archived"):
                return {"error": "that chat is archived (the owner can restore it)"}
            chat = wanted or (self.chat_of(context) if context is not None else None) or self.current()
            turns = int(args.get("turns") or 10)
            shown = self.shown(self._held(self.data["chats"][chat["id"]])) if chat else []
            return [{"role": m["role"], "text": m["text"]} for m in shown[-turns:]]
        if function == "post":
            text = str(args.get("text") or "")
            if context is not None and self.chat_of(context) is not None:  # during a turn of one of its chats: into it
                context.add(self.tag("post"), text)
                return {"posted": True}
            chats = self.chats()
            chat = self.data["chats"][chats[0]["id"]] if chats else self._new("From AAC")
            held = self._held(chat)
            held.add(self.tag("post"), text)
            await self._keep(chat, held)
            return {"posted": True}
        return await super().call(function, args, context)


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
