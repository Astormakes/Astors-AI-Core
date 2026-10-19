"""Lists of one AAC domain: trees of entries, text first, anything else optional.

    list:  {id, name, view: checklist | outline, items: [entry], created, updated}
    entry: {id, text, done, fields: {qty, store, when, url, …anything…}, children: [entry], created, updated}
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import uuid

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

VIEWS = ("checklist", "outline")


def _now() -> str:
    return dt_util.utcnow().isoformat()


def new_entry(text: str, fields: dict | None = None, children: list | None = None, done: bool = False) -> dict[str, Any]:
    """An entry (children may be texts or dicts)."""
    now = _now()
    return {
        "id": uuid.uuid4().hex[:8], "text": str(text).strip(), "done": bool(done),
        "fields": {k: v for k, v in (fields or {}).items() if v not in (None, "")},
        "children": [from_input(c) for c in children or [] if _has_text(c)], "created": now, "updated": now,
    }


def _has_text(item: Any) -> bool:
    text = item if isinstance(item, str) else (item or {}).get("text", "")
    return bool(str(text).strip())


def from_input(item: Any) -> dict[str, Any]:
    """'Milk' or {"text": "Milk", "fields": {...}, "children": [...]} → an entry."""
    if isinstance(item, str):
        return new_entry(item)
    return new_entry(item.get("text", ""), item.get("fields"), item.get("children"), item.get("done", False))


def walk(entries: list[dict], parent: dict | None = None, path: tuple = ()) -> Iterator[tuple[dict, list[dict], dict | None, tuple]]:
    """Every entry with the list it sits in, its parent and its path of texts."""
    for entry in entries:
        yield entry, entries, parent, path
        yield from walk(entry["children"], entry, (*path, entry["text"]))


class ListStore:
    """All lists of one domain."""

    def __init__(self, store: Store) -> None:
        """Initialize."""
        self._store = store
        self.lists: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        """Read from storage."""
        self.lists = (await self._store.async_load() or {}).get("lists", [])

    def save(self) -> None:
        """Write (debounced)."""
        self._store.async_delay_save(lambda: {"lists": self.lists}, 1)

    # ------------------------------------------------------------ finding
    def get(self, ref: str) -> dict[str, Any] | None:
        """A list by id or (case-insensitive) name."""
        ref = str(ref).strip()
        return next((lst for lst in self.lists if lst["id"] == ref), None) or next(
            (lst for lst in self.lists if lst["name"].lower() == ref.lower()), None
        )

    def find(self, lst: dict, ref: str) -> tuple[dict, list[dict], dict | None] | None:
        """An entry by id, else by exact text (case-insensitive), else by unique text start."""
        ref = str(ref).strip()
        hits = [(e, c, p) for e, c, p, _ in walk(lst["items"]) if e["id"] == ref]
        hits = hits or [(e, c, p) for e, c, p, _ in walk(lst["items"]) if e["text"].lower() == ref.lower()]
        if not hits:
            starts = [(e, c, p) for e, c, p, _ in walk(lst["items"]) if e["text"].lower().startswith(ref.lower())]
            hits = starts if len(starts) == 1 else []
        return hits[0] if hits else None

    # ------------------------------------------------------------ changing
    def create(self, name: str, view: str = "checklist", items: list | None = None) -> dict[str, Any]:
        """A new list (an existing one with that name is returned instead)."""
        if existing := self.get(name):
            return existing
        now = _now()
        lst = {"id": uuid.uuid4().hex[:8], "name": name.strip(), "view": view if view in VIEWS else "checklist",
               "items": [from_input(i) for i in items or [] if _has_text(i)], "created": now, "updated": now}
        self.lists.append(lst)
        self.save()
        return lst

    def add(self, lst: dict, items: list, parent: str | None = None) -> list[dict[str, Any]]:
        """Add entries (at the top level, or below a parent entry)."""
        target = lst["items"]
        if parent:
            found = self.find(lst, parent)
            if found is None:
                raise KeyError(f"No entry '{parent}' in {lst['name']}")
            target = found[0]["children"]
        added = [from_input(i) for i in items if _has_text(i)]
        target.extend(added)
        lst["updated"] = _now()
        self.save()
        return added

    def update(self, lst: dict, ref: str, text: str | None = None, done: bool | None = None,
               fields: dict | None = None) -> dict[str, Any]:
        """Change an entry; fields are merged, a null value removes a field."""
        found = self.find(lst, ref)
        if found is None:
            raise KeyError(f"No entry '{ref}' in {lst['name']}")
        entry = found[0]
        if text is not None and text.strip():
            entry["text"] = text.strip()
        if done is not None:
            entry["done"] = bool(done)
        for key, value in (fields or {}).items():
            if value in (None, ""):
                entry["fields"].pop(key, None)
            else:
                entry["fields"][key] = value
        entry["updated"] = lst["updated"] = _now()
        self.save()
        return entry

    def move(self, lst: dict, ref: str, parent: str | None, index: int | None = None) -> dict[str, Any]:
        """Move an entry below another one (None = top level), at a position."""
        found = self.find(lst, ref)
        if found is None:
            raise KeyError(f"No entry '{ref}' in {lst['name']}")
        entry, container, _ = found
        target = lst["items"]
        if parent:
            dest = self.find(lst, parent)
            if dest is None or dest[0] is entry or any(e is dest[0] for e, *_ in walk(entry["children"])):
                raise KeyError("Cannot move an entry into itself")
            target = dest[0]["children"]
        container.remove(entry)
        target.insert(len(target) if index is None else max(0, min(index, len(target))), entry)
        lst["updated"] = _now()
        self.save()
        return entry

    def remove(self, lst: dict, refs: list[str]) -> list[str]:
        """Remove entries (with their sub-entries); returns the removed texts."""
        removed = []
        for ref in refs:
            if found := self.find(lst, ref):
                found[1].remove(found[0])
                removed.append(found[0]["text"])
        if removed:
            lst["updated"] = _now()
            self.save()
        return removed

    def delete(self, lst: dict) -> None:
        """Remove a whole list."""
        self.lists.remove(lst)
        self.save()

    def rename(self, lst: dict, name: str, view: str | None = None) -> None:
        """Rename a list or change its view."""
        if name.strip():
            lst["name"] = name.strip()
        if view in VIEWS:
            lst["view"] = view
        lst["updated"] = _now()
        self.save()
