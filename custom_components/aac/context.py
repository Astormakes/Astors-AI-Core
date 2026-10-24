"""The context (docs/v2 page 1): a document of entries in time order, each written by a card.

An entry: {id, by (card id), kind, text, tags (`<card>.<tag>`), time, data, state}. Kinds: system (a system
prompt part), note (a side remark before the turn), trailer (after the newest turn), said (a person), answer (a
model), call / result (a tool loop), signal (no content: `<card>.stop`). State: open while it grows,
closed when done, cut when a stop ended it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Any
import uuid

from homeassistant.util import dt as dt_util

KINDS = ("system", "note", "trailer", "said", "answer", "call", "result", "signal")


def is_stop(e: dict[str, Any]) -> bool:
    """A stop signal entry."""
    return e["kind"] == "signal" and e["text"] == "stop"


def new_id() -> str:
    """A short id."""
    return uuid.uuid4().hex[:10]


def entry(by: str, kind: str, text: str = "", tags: list[str] | None = None, **data: Any) -> dict[str, Any]:
    """One entry (its tags always carry the card in front)."""
    tags = [t if "." in t else f"{by}.{t}" for t in tags or []]
    return {"id": new_id(), "by": by, "kind": kind, "text": text, "tags": tags, "time": dt_util.utcnow().isoformat(),
            "data": data, "state": "closed"}


@dataclass
class Context:
    """A context on its way through the cards."""

    entries: list[dict[str, Any]] = field(default_factory=list)
    origin: str = ""  # the card it started at (an answer goes back there)
    chat_id: str | None = None  # the chat it belongs to (a Chat card keeps the conversation)
    id: str = field(default_factory=new_id)
    way: list[str] = field(default_factory=list)  # the cards it passed

    def add(self, by: str, kind: str, text: str = "", tags: list[str] | None = None, **data: Any) -> dict[str, Any]:
        """Append an entry."""
        item = entry(by, kind, text, tags, **data)
        self.entries.append(item)
        return item

    def put(self, by: str, kind: str, text: str, tag: str, replace_it: bool = True) -> dict[str, Any] | None:
        """A card's standing text (a Relay, a Buffer): replaced in place (a system part stays where it is, so the
        model server's cache keeps working), or a new entry every time. Empty text removes it."""
        tag = tag if "." in tag else f"{by}.{tag}"
        old = next((e for e in self.entries if e["by"] == by and tag in e["tags"] and e["kind"] == kind), None)
        if not text.strip():
            if old and replace_it:
                self.entries.remove(old)
            return None
        if old and replace_it:
            if kind != "trailer":
                old["text"] = text
                return old
            self.entries.remove(old)  # a trailer that replaces moves to the newest turn
        item = entry(by, kind, text, [tag])
        if kind == "system":  # system parts stay together at the start
            at = next((i for i, e in enumerate(self.entries) if e["kind"] != "system"), len(self.entries))
            self.entries.insert(at, item)
        else:
            self.entries.append(item)
        return item

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
        """It is a stop: its newest entry is a stop signal (an old stop further up is only history)."""
        return bool(self.entries) and is_stop(self.entries[-1]) and not self.entries[-1]["data"].get("done")

    def branch(self) -> Context:
        """A copy for one of several wires."""
        return replace(self, entries=deepcopy(self.entries), way=list(self.way))

    def summary(self) -> dict[str, Any]:
        """For the panel (the monitor)."""
        return {"id": self.id, "origin": self.origin, "chat_id": self.chat_id, "way": self.way,
                "entries": [{k: (v[:4000] if k == "text" else v) for k, v in e.items()} for e in self.entries[-60:]]}
