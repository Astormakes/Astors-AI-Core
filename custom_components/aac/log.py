"""AAC's own log (spec/cards.md "The log"): one per domain, in its data folder (`log.jsonl`), so it moves with a rename
and goes with the domain. It rotates in two lanes, so a busy card cannot push the rest out: the newest KEEP of what
passed the wires (level flow) stay, and the newest KEEP of everything else. What passes a card's wires is written as its Log
setting says; a whole context stays with its entry only CONTEXT_FOR seconds and is never written to disk (privacy,
space). Cards write through `Card.log` and read through `Card.read_log` (a permission); the core writes what goes wrong
(a card that fails, a model that does not answer) and what the owner changes. HA's own log only hears about AAC
running on HA.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HassJob
from homeassistant.helpers.event import async_call_later

if TYPE_CHECKING:
    from .core import Domain

KEEP = 5000  # entries per lane (what passed the wires; everything else); the oldest go
CONTEXT_FOR = 600  # seconds a whole context stays with its entry (only in memory)
LEVELS = ("error", "warning", "info", "flow")  # flow: what passed a card's wires
TEXT_MAX = 600
WRITE_AFTER = 2  # seconds: new entries are written together


class DomainLog:
    """The log of one domain."""

    def __init__(self, domain: Domain) -> None:
        """Initialize (empty until load)."""
        self.domain = domain
        self.entries: list[dict[str, Any]] = []
        self._contexts: dict[int, tuple[float, dict[str, Any]]] = {}  # entry id → (when, the whole context)
        self._new: list[dict[str, Any]] = []  # entries (or a repeat's newer count) not on disk yet
        self._lines = 0  # lines in the file (it is written anew once it holds far more than both lanes)
        self._rewrite = False
        self._last_id = 0
        self._flows = 0  # entries in the flow lane (the rest are the other lane)
        self._lock = asyncio.Lock()
        self._timer: CALLBACK_TYPE | None = None
        self.closed = False  # its domain stopped: nothing more is written (its folder may move)

    @property
    def path(self) -> Path:
        """<config>/aac/data/<domain>/log.jsonl."""
        return self.domain.core.folder(self.domain.id) / "log.jsonl"

    async def load(self) -> None:
        """Read what the file holds (a repeat's line came again with a newer count: the last one counts)."""
        self.entries, self._lines = await self.domain.hass.async_add_executor_job(self._read, self.path)
        self._last_id = max((e["id"] for e in self.entries), default=0)
        self._flows = sum(e["level"] == "flow" for e in self.entries)

    @staticmethod
    def _read(path: Path) -> tuple[list[dict[str, Any]], int]:
        if not path.is_file():
            return [], 0
        found: dict[int, dict[str, Any]] = {}
        lines = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            lines += 1
            try:
                entry = json.loads(line)
                found[int(entry["id"])] = entry
            except (ValueError, KeyError, TypeError):
                continue
        entries = sorted(found.values(), key=lambda e: e["id"])
        flow = [e for e in entries if e["level"] == "flow"][-KEEP:]
        rest = [e for e in entries if e["level"] != "flow"][-KEEP:]
        return sorted(flow + rest, key=lambda e: e["id"]), lines

    def write(self, level: str, text: str, card: str | None = None, part: str | None = None, what: str = "",
              context: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        """One entry: from a card (its id) or a part of AAC itself ("core", "audio", "models"). The same entry again
        right after it only counts up (a card that repeats itself takes one line)."""
        now = time.time()
        text = str(text)[:TEXT_MAX]
        if self.closed:
            return {"time": now, "level": level, "what": what, "text": text}
        last = self.entries[-1] if self.entries else None
        if (last and last["text"] == text and last["level"] == level and last.get("card") == card and last.get("part") == part
                and last["what"] == what and all(last.get(k) == v for k, v in extra.items())):
            last["time"], last["n"] = now, last.get("n", 1) + 1
            entry = last
        else:
            self._last_id += 1
            entry = {"id": self._last_id, "time": now, "level": level if level in LEVELS else "info", "what": what, "text": text,
                     **({"card": card} if card else {"part": part or "core"}), **{k: v for k, v in extra.items() if v is not None}}
            self.entries.append(entry)
            flow = entry["level"] == "flow"
            self._flows += flow
            while (self._flows if flow else len(self.entries) - self._flows) > KEEP:  # its lane is full: its oldest go
                old = next(i for i, e in enumerate(self.entries) if (e["level"] == "flow") == flow)
                self._contexts.pop(self.entries.pop(old)["id"], None)
                self._flows -= flow
        if context is not None:
            self._contexts[entry["id"]] = (now, context)
        self._forget_contexts(now)
        self._new.append(entry)
        self._soon()
        self.domain.core.notify("log", domain=self.domain.id, entry=self._out(entry))
        return entry

    def _forget_contexts(self, now: float) -> None:
        for entry_id in [i for i, (at, _) in self._contexts.items() if now - at > CONTEXT_FOR]:
            del self._contexts[entry_id]

    def _out(self, entry: dict[str, Any]) -> dict[str, Any]:
        kept = self._contexts.get(entry["id"])
        return {**entry, "context": kept[1]} if kept else dict(entry)

    def read(self, card: str | None = None, part: str | None = None, port: str | None = None, what: str | None = None,
             levels: list[str] | None = None, search: str = "", before: int | None = None, last: int = 200) -> list[dict[str, Any]]:
        """The newest entries that match (oldest first), with their whole context while it is kept (part "*": any part
        of AAC itself; before: only entries older than that id)."""
        self._forget_contexts(time.time())
        word, found = search.lower().strip(), []
        for entry in reversed(self.entries):
            if ((before is not None and entry["id"] >= before) or (card and entry.get("card") != card)
                    or (part and entry.get("part") != part and not (part == "*" and entry.get("part")))
                    or (port and entry.get("port") != port) or (what and entry["what"] != what)
                    or (levels and entry["level"] not in levels) or (word and word not in f"{entry['what']} {entry['text']}".lower())):
                continue
            found.append(self._out(entry))
            if len(found) >= last:
                break
        return found[::-1]

    async def clear(self) -> None:
        """Every entry goes."""
        self.entries, self._contexts, self._new, self._flows = [], {}, [], 0
        self._rewrite = True
        await self.flush()

    def renamed(self, old: str, new: str) -> None:
        """A card's id moved (its name did): its entries are under the new one."""
        changed = False
        for entry in self.entries:
            if entry.get("card") == old:
                entry["card"], changed = new, True
            if any(t.startswith(f"{old}.") for t in entry.get("to") or []):
                entry["to"], changed = [f"{new}{t[len(old):]}" if t.startswith(f"{old}.") else t for t in entry["to"]], True
        if changed:
            self._rewrite = True
            self._soon()

    def _soon(self) -> None:
        if self._timer is None:  # (HA stopping drops the wait: at most WRITE_AFTER seconds of entries)
            self._timer = async_call_later(self.domain.hass, WRITE_AFTER, HassJob(self._flush_later, cancel_on_shutdown=True))

    async def _flush_later(self, _now: Any) -> None:
        self._timer = None
        await self.flush()

    async def close(self) -> None:
        """Its domain stops: what is new is written, then nothing more."""
        await self.flush()
        self.closed = True

    async def flush(self) -> None:
        """What is new goes to the file (the file is written anew once it holds far more lines than entries kept)."""
        if self._timer is not None:
            self._timer()
            self._timer = None
        async with self._lock:
            whole = self._rewrite or self._lines + len(self._new) > KEEP * 2.4
            new = list({e["id"]: e for e in self._new}.values())  # a repeat counted up twice is one line
            lines = [json.dumps(e, ensure_ascii=False) for e in (self.entries if whole else new)]
            self._new, self._rewrite = [], False
            if not whole and not lines:
                return
            await self.domain.hass.async_add_executor_job(self._write_file, self.path, lines, whole)
            self._lines = len(lines) if whole else self._lines + len(lines)

    @staticmethod
    def _write_file(path: Path, lines: list[str], whole: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "".join(f"{line}\n" for line in lines)
        if whole:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        else:
            with path.open("a", encoding="utf-8") as file:
                file.write(text)
