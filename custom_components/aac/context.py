"""The context (docs/v2 page 3): shaped like a model request. Every card puts what it adds where a model expects it.

    system    the system prompt: parts in order, each from a card
    messages  the conversation: said (user), answer (assistant, with the calls it made), result (tool)
              a message carries attachments: note (before its text) and trailer (after), added for that turn
    tools     what a model may call this turn (the Model card fills it from the cards wired to it and back)
    events    stop, cuts, choices, errors: for cards and the panel, never sent to a model

An entry: {id, by (card id), kind, text, tags (`<card>.<tag>`), time, data, state}. State: open while it grows,
closed when done, cut when a stop ended it. An answer's calls: data["calls"] = [{id, card, function, name, args}];
a result: data["call_id"].
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any
import uuid

from homeassistant.util import dt as dt_util

MESSAGES = ("said", "answer", "result")
ATTACHMENTS = ("note", "trailer")
EVENTS = ("signal", "error")
KINDS = ("system", *MESSAGES, *ATTACHMENTS, *EVENTS)


def is_stop(e: dict[str, Any]) -> bool:
    """A stop signal entry."""
    return e["kind"] == "signal" and e["text"] == "stop"


def new_id() -> str:
    """A short id."""
    return uuid.uuid4().hex[:10]


def entry(by: str, kind: str, text: str = "", tags: list[str] | None = None, **data: Any) -> dict[str, Any]:
    """One entry (its tags always carry the card in front)."""
    if kind not in KINDS:
        raise ValueError(f"no kind {kind}")
    tags = [t if "." in t else f"{by}.{t}" for t in tags or []]
    return {"id": new_id(), "by": by, "kind": kind, "text": text, "tags": tags, "time": dt_util.utcnow().isoformat(),
            "data": data, "state": "closed"}


@dataclass
class Context:
    """A context on its way through the cards."""

    system: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)  # {name, card, function, description, args}
    events: list[dict[str, Any]] = field(default_factory=list)
    origin: str = ""  # the card it started at (an answer goes back there)
    chat_id: str | None = None  # the chat it belongs to (a Chat card keeps the conversation)
    id: str = field(default_factory=new_id)
    way: list[str] = field(default_factory=list)  # the cards it passed
    progress: bool = False  # a snapshot of a turn still being worked on (the same turn comes again, finished)
    request: dict[str, Any] | None = None  # the newest request a Model sent: {model, body, reply} (a chat page's Plain text)

    # ------------------------------------------------------------------ adding
    def add(self, by: str, kind: str, text: str = "", tags: list[str] | None = None, **data: Any) -> dict[str, Any]:
        """A new entry, where its kind belongs: system, a message, an attachment on the newest user message (on the
        newest message when nobody said anything; a system part when there is no message at all), or an event."""
        item = entry(by, kind, text, tags, **data)
        self._place(item)
        return item

    def _place(self, item: dict[str, Any], to: dict[str, Any] | None = None) -> None:
        kind = item["kind"]
        if kind == "system":
            self.system.append(item)
        elif kind in MESSAGES:
            self.messages.append(item)
        elif kind in EVENTS:
            self.events.append({**item, "at": len(self.messages)})  # after how many messages it happened
        elif (to := to or self.last("said") or (self.messages[-1] if self.messages else None)) is not None:
            to.setdefault("attachments", []).append(item)
        else:
            self.system.append(item)

    def put(self, by: str, kind: str, text: str, tag: str, replace_it: bool = True) -> dict[str, Any] | None:
        """A card's standing text (a Relay's @new, a Buffer, Memory's matches): a system part is replaced in place (the
        model server's cache keeps working); an attachment that replaces moves to the newest user message (only one);
        replace_it=False: a new one each time. Empty text removes it."""
        tag = tag if "." in tag else f"{by}.{tag}"
        old = [e for e in self.entries if e["by"] == by and tag in e["tags"] and e["kind"] == kind]
        if replace_it and old:
            if kind == "system" and text.strip():
                old[-1]["text"] = text
                return old[-1]
            for e in old:
                self.remove(e)
        if not text.strip():
            return None
        item = entry(by, kind, text, [tag])
        self._place(item)
        return item

    def remove(self, item: dict[str, Any]) -> None:
        """Take an entry out, wherever it is."""
        for box in (self.system, self.messages, self.events, *(m.get("attachments", []) for m in self.messages)):
            box[:] = [e for e in box if e is not item and e["id"] != item["id"]]

    # ------------------------------------------------------------------ reading
    @property
    def entries(self) -> list[dict[str, Any]]:
        """Every entry in order: the system parts, then each message with its attachments (notes before it, trailers
        after), the events where they happened."""
        return list(self._walk())

    def _walk(self) -> Iterator[dict[str, Any]]:
        yield from self.system
        events = sorted(self.events, key=lambda e: e.get("at", 0))
        n = 0
        for i, m in enumerate(self.messages):
            while n < len(events) and events[n].get("at", 0) <= i:
                yield events[n]
                n += 1
            attached = m.get("attachments", [])
            yield from (a for a in attached if a["kind"] == "note")
            yield m
            yield from (a for a in attached if a["kind"] != "note")
        yield from events[n:]

    def tagged(self, tag: str) -> list[dict[str, Any]]:
        """The entries with a tag (oldest first)."""
        return [e for e in self.entries if tag in e["tags"]]

    def last(self, kind: str | None = None, tag: str | None = None) -> dict[str, Any] | None:
        """The newest entry of a kind and/or with a tag."""
        return next((e for e in reversed(self.entries)
                     if (kind is None or e["kind"] == kind) and (tag is None or tag in e["tags"])), None)

    @property
    def text(self) -> str:
        """What was said last (the newest said entry)."""
        said = self.last("said")
        return said["text"] if said else ""

    @property
    def answer(self) -> str:
        """The newest answer."""
        found = self.last("answer")
        return found["text"] if found else ""

    @property
    def stopped(self) -> bool:
        """It is a stop: its newest event is a running stop with nothing said or answered after it."""
        last = self.events[-1] if self.events else None
        return bool(last) and is_stop(last) and not last["data"].get("done") and last.get("at", 0) >= len(self.messages)

    def open_calls(self, by: str) -> list[dict[str, Any]]:
        """The calls of a card's newest answer that have no result yet (in the order it made them)."""
        mine = next((m for m in reversed(self.messages) if m["kind"] == "answer" and m["by"] == by), None)
        if mine is None:
            return []
        done = {m["data"].get("call_id") for m in self.messages if m["kind"] == "result"}
        return [c for c in mine["data"].get("calls") or [] if c["id"] not in done]

    def branch(self) -> Context:
        """A copy for one of several wires."""
        return replace(self, system=deepcopy(self.system), messages=deepcopy(self.messages), tools=deepcopy(self.tools),
                       events=deepcopy(self.events), way=list(self.way))

    # ------------------------------------------------------------------ keeping and showing
    def held(self) -> dict[str, Any]:
        """What a chat keeps: the messages and events (system parts and tools come from their cards each turn)."""
        return {"messages": deepcopy(self.messages), "events": deepcopy(self.events)}

    def summary(self) -> dict[str, Any]:
        """For the panel: every container, long texts shortened."""
        def short(e: dict[str, Any]) -> dict[str, Any]:
            out = {k: (v[:4000] if k == "text" else v) for k, v in e.items() if k != "attachments"}
            return {**out, "attachments": [short(a) for a in e["attachments"]]} if e.get("attachments") else out
        return {"id": self.id, "origin": self.origin, "chat_id": self.chat_id, "way": self.way,
                "system": [short(e) for e in self.system], "messages": [short(e) for e in self.messages[-60:]],
                "tools": self.tools, "events": [short(e) for e in self.events[-30:]]}
