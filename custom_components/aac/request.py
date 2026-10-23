"""The message: a document of tagged entries (spec/graph.md).

Owner, 2026-10-23: the message is the whole context; every node writes into it and tags what it wrote, and
the tags are the pointers (what the chat shows, where an inserter puts something, the expert view). A model
reads the whole document; a node it calls writes its result into it and hands it back. Besides the entries the
document carries where it came from and where an answer goes (`origin`, `reply_to`, `chat_id` …), and for
automatic ones how long they may wait for a model.

flow.py moves it through the graph.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any
import uuid

from homeassistant.exceptions import TemplateError
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .domains import Domain

TEXT = "text"  # the port type of messages

SOURCES = {"chat": "Panel chat", "voice": "Voice", "assist": "HA Assist"}
PRIORITY = {"chat": "high", "voice": "high", "assist": "high"}
# system: the system prompt · note: with the next user turn · user · answer · call (a model chose a node) · result
KINDS = ("system", "note", "trailer", "user", "answer", "call", "result")


def source_of(kind: str, trigger: str) -> str:
    """Which node a message starts at."""
    if trigger == "heartbeat":
        return "heartbeat"
    if kind == "auto":
        return "triggers"
    if trigger.startswith("voice"):
        return "voice"
    return "assist" if trigger == "assist" else "chat"


# What every template can use besides the cards' values (the node view's Variables overview)
BUILTIN_VARS = {
    "local_time": "The local date and time, e.g. \"Friday 2026-10-23 12:33\".",
    "message.language": "The message's language (e.g. de, en).",
    "message.user_id": "Who sent it (HA user id; empty for automatic runs).",
    "message.origin": "The card it started at (chat, voice, heartbeat …).",
    "message.trigger": "What started it (chat, heartbeat, a trigger's name …).",
    "message.title": "Its title (an automatic run's name).",
    "message.kind": "chat (someone waits) or auto (nobody asked).",
    "message.device_id": "The device it came from (a satellite), if any.",
}
HA_TEMPLATE_HELP = {
    "now()": "HA: the current time as a date object, e.g. {{ now().strftime('%H:%M') }}.",
    "states('sensor.x')": "HA: any entity's state.",
    "state_attr('light.x', 'brightness')": "HA: an entity's attribute.",
    "is_state('person.x', 'home')": "HA: true or false, for {% if … %}.",
}


def render(hass, text: str, message: Message) -> str:
    """A card's text through HA's own template engine ({{ now() }}, {{ states('sensor.x') }}, {{ local_time }}, the
    cards' values); an error shows in the text instead of breaking the message."""
    if "{" not in text:
        return text
    try:
        return str(Template(text, hass).async_render(message.template_vars(), parse_result=False)).strip()
    except TemplateError as err:
        return f"{text}\n[template error: {err}]"


def new_id() -> str:
    """An id for an entry or a call."""
    return uuid.uuid4().hex[:10]


def entry(by: str, kind: str, text: str = "", tags: list[str] | None = None, **data: Any) -> dict[str, Any]:
    """One entry of a document."""
    return {"id": new_id(), "by": by, "kind": kind, "text": text, "tags": list(dict.fromkeys([by, *(tags or [])])),
            "time": dt_util.utcnow().isoformat(), "data": data}


@dataclass
class Message:
    """A message on its way through the graph."""

    domain: Domain
    entries: list[dict[str, Any]] = field(default_factory=list)
    kind: str = "chat"  # chat (someone waits) · auto (nobody asked)
    trigger: str = "chat"
    title: str = ""
    origin: str = "chat"  # the node it started at
    reply_to: str | None = None  # where an answer goes by default (None: back to the origin)
    chat_id: str | None = None
    conversation_id: str | None = None
    user_id: str | None = None
    language: str | None = None
    device_id: str | None = None
    tier: str = "master"
    allow_tools: bool = True
    priority: str = "high"  # high · normal · low
    trace: list[dict[str, Any]] = field(default_factory=list)  # the way through the graph, each node's part
    calls: list[dict[str, Any]] = field(default_factory=list)  # open calls, newest last: {call_id, model, node}
    id: str = field(default_factory=new_id)
    # Automatic messages (heartbeat, triggers): how they wait for a model (jobs.py) and where a finding goes
    due_minutes: int | None = None
    critical: bool = False
    fallback: str = ""
    coalesce: bool = False
    keep: bool = False
    tags: list[str] = field(default_factory=list)
    context_parts: list[str] = field(default_factory=list)  # built when a model really starts: time, home, open_loops
    chat: str = "aac"  # an automatic finding: "aac" = the From AAC chat, "own" = a chat of its own
    chat_prefix: str | None = None
    now: bool = False  # automatic but at once, without the queue ("Run once now")
    # Values cards hand on (owner, 2026-10-23): card id → {name: value}; the text cards read them with HA's
    # template syntax, e.g. {{ kitchen_buffer.lines }}
    vars: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------ writing
    def add(self, by: str, kind: str, text: str = "", tags: list[str] | None = None, **data: Any) -> dict[str, Any]:
        """Append an entry; while a model waits for a loop, it belongs to that call."""
        if self.calls and kind not in ("call",) and "call_id" not in data:
            data["call_id"] = self.calls[-1]["call_id"]
        item = entry(by, kind, text, tags, **data)
        self.entries.append(item)
        return item

    def put(self, by: str, kind: str, text: str, tag: str | None = None, replace: bool = True) -> dict[str, Any] | None:
        """A node's standing entry (its prompt, its context): replaced in place when it is there, so the start
        of the document stays the same for the model server's cache; a new system entry goes after the other
        system entries at the start. Empty text removes it. A trailer that replaces moves to the newest turn.
        replace=False (owner, 2026-10-23: a "Replace" tick per card): a new entry every time, the old ones stay."""
        tag = tag or by
        old = next((e for e in self.entries if e["by"] == by and tag in e["tags"] and e["kind"] == kind), None)
        if not text.strip():
            if old and replace:
                self.entries.remove(old)
            return None
        if old and replace:
            if kind == "trailer":  # after the newest turn, not where it was written first
                self.entries.remove(old)
            else:
                if old["text"] != text:
                    old["text"] = text
                return old
        item = entry(by, kind, text, [tag])
        if kind == "system":
            at = next((i for i, e in enumerate(self.entries) if e["kind"] != "system"), len(self.entries))
            self.entries.insert(at, item)
        else:
            self.entries.append(item)
        return item

    def set_vars(self, by: str, **values: Any) -> None:
        """A card's values for the cards after it ({{ <card id>.<name> }})."""
        self.vars.setdefault(by, {}).update(values)

    def template_vars(self) -> dict[str, Any]:
        """What a template sees: every card's values, `local_time`, and `message` (its own pointers)."""
        return {**self.vars, "local_time": dt_util.now().strftime("%A %Y-%m-%d %H:%M"), "message": {"language": self.language, "user_id": self.user_id, "origin": self.origin,
                                         "trigger": self.trigger, "title": self.title, "kind": self.kind,
                                         "device_id": self.device_id}}

    # ------------------------------------------------------------------ reading
    def last(self, kind: str | None = None, tag: str | None = None) -> dict[str, Any] | None:
        """The newest entry of a kind and/or with a tag."""
        return next((e for e in reversed(self.entries) if (kind is None or e["kind"] == kind)
                     and (tag is None or tag in e["tags"])), None)

    @property
    def text(self) -> str:
        """The newest thing said: the last answer after a model, else the last user line."""
        found = next((e for e in reversed(self.entries) if e["kind"] in ("user", "answer")), None)
        return found["text"] if found else ""

    @property
    def words(self) -> str:
        """The person's own words of the newest user entry (a voice line comes with the room's lines)."""
        found = self.last("user")
        return (found["data"].get("own_words") or found["text"]) if found else ""

    @property
    def answer(self) -> str | None:
        """The newest answer, if the last thing said was one."""
        found = next((e for e in reversed(self.entries) if e["kind"] in ("user", "answer")), None)
        return found["text"] if found and found["kind"] == "answer" else None

    @property
    def source(self) -> str:
        """Where it started (the run log's word)."""
        return self.origin

    def branch(self) -> Message:
        """A copy for one wire of several (entries copied, the way so far kept)."""
        return replace(self, entries=deepcopy(self.entries), trace=list(self.trace), calls=list(self.calls),
                       tags=list(self.tags), context_parts=list(self.context_parts), vars=deepcopy(self.vars))


ModelRequest = Message


def from_messages(by: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A document from a plain chat history ([{"role": "user"|"aac", "text"}]), e.g. HA's conversation."""
    return [entry(by, "user" if m["role"] == "user" else "answer", m["text"]) for m in messages if m.get("text")]
