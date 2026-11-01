"""The context (docs/v2 page 5): a list of entries in order, nothing else.

Always first `context.system` (the system prompt: its text), second `context.tools` (what a model may call: its
data["tools"]), then the conversation. Entries are read one after the other, and added, changed and removed anywhere.

An entry: {id, tag, text, data, time, state}. The tag is `<card>.<tag>` (optionally `.<extra>…`): the card in front
wrote it. State: streaming (still being written), ended, cut (a stop ended it). Calls are in an entry's
data["calls"] = [{id, card, function, name, args}]; a result is an entry with data["call_id"].
"""

from __future__ import annotations

from copy import deepcopy
import fnmatch
from typing import Any
import uuid

from homeassistant.util import dt as dt_util

SYSTEM = "context.system"
TOOLS = "context.tools"
STATES = ("streaming", "ended", "cut")


def new_id() -> str:
    """A short id."""
    return uuid.uuid4().hex[:10]


def entry(tag: str, text: str = "", **data: Any) -> dict[str, Any]:
    """One entry."""
    if "." not in tag:
        raise ValueError(f"a tag has the card in front: <card>.{tag}")
    return {"id": new_id(), "tag": tag, "text": text, "data": data, "time": dt_util.utcnow().isoformat(), "state": "ended"}


def matches(e: dict[str, Any], pattern: str) -> bool:
    """An entry's tag fits a pattern (* stands for anything: `*.said`)."""
    return fnmatch.fnmatchcase(e["tag"], pattern)


class Context:
    """A context on its way through the cards."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        """A new context, or one made from stored entries (the first two always system and tools)."""
        entries = list(entries or [])
        first = {e["tag"]: e for e in entries[:2]}
        self.entries = [first.get(SYSTEM) or entry(SYSTEM), first.get(TOOLS) or entry(TOOLS, tools=[]),
                        *(e for e in entries if e["tag"] not in (SYSTEM, TOOLS))]

    # ------------------------------------------------------------------ the parts
    @property
    def system(self) -> dict[str, Any]:
        """The system prompt's entry."""
        return self.entries[0]

    @property
    def tools(self) -> dict[str, Any]:
        """The tools' entry."""
        return self.entries[1]

    @property
    def conversation(self) -> list[dict[str, Any]]:
        """Every entry after system and tools."""
        return self.entries[2:]

    @property
    def newest(self) -> dict[str, Any] | None:
        """The newest entry of the conversation."""
        return self.entries[-1] if len(self.entries) > 2 else None

    @property
    def waiting(self) -> bool:
        """It waits for a call's result (on its way to the card the call names)."""
        return self.next_call() is not None

    @property
    def streaming(self) -> bool:
        """Something in it is still being written (a snapshot: the same context comes again)."""
        return any(e["state"] == "streaming" for e in self.entries)

    # ------------------------------------------------------------------ reading
    def find(self, pattern: str) -> list[dict[str, Any]]:
        """The entries whose tag fits (oldest first)."""
        return [e for e in self.entries if matches(e, pattern)]

    def get(self, pattern: str) -> dict[str, Any] | None:
        """The newest entry whose tag fits."""
        return next((e for e in reversed(self.entries) if matches(e, pattern)), None)

    def by_id(self, entry_id: str) -> dict[str, Any] | None:
        """The entry with that id."""
        return next((e for e in self.entries if e["id"] == entry_id), None)

    def next_call(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """The call to make now: (the entry that made it, the call). Only while the context waits for results: its
        newest entry with calls is followed by nothing but results; then the first of its calls without one."""
        for i in range(len(self.entries) - 1, 1, -1):
            e = self.entries[i]
            if e["data"].get("calls"):
                done = {r["data"].get("call_id") for r in self.entries[i + 1:]}
                if any("call_id" not in r["data"] for r in self.entries[i + 1:]):
                    return None
                return next(((e, c) for c in e["data"]["calls"] if c["id"] not in done), None)
            if "call_id" not in e["data"]:
                return None
        return None

    # ------------------------------------------------------------------ writing
    def add(self, tag: str, text: str = "", at: str = "last", **data: Any) -> dict[str, Any]:
        """A new entry at the end of the conversation (at="first": at its beginning)."""
        item = entry(tag, text, **data)
        if at == "first":
            self.entries.insert(2, item)
        elif at == "last":
            self.entries.append(item)
        else:
            raise ValueError('at="first" or at="last"')
        return item

    def remove(self, item: dict[str, Any]) -> None:
        """Take an entry out (system and tools always stay: change their text instead)."""
        if item["tag"] in (SYSTEM, TOOLS):
            raise ValueError(f"{item['tag']} is always there: change its text instead")
        self.entries = [e for e in self.entries if e["id"] != item["id"]]

    def copy(self) -> Context:
        """A copy (for one of several wires)."""
        return Context(deepcopy(self.entries))

    # ------------------------------------------------------------------ keeping and showing
    def to_dict(self) -> dict[str, Any]:
        """As stored and sent to the panel."""
        return {"entries": deepcopy(self.entries)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Context:
        """From what to_dict gave."""
        return cls(deepcopy((data or {}).get("entries") or []))

    def summary(self) -> dict[str, Any]:
        """For the panel: the entries, long texts shortened (the newest 80)."""
        def short(e: dict[str, Any]) -> dict[str, Any]:
            return {**e, "text": e["text"][:4000]}
        return {"entries": [short(e) for e in self.entries[:2]] + [short(e) for e in self.conversation[-80:]]}
