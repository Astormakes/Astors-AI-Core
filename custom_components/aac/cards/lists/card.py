"""Lists: its own lists (shopping, projects, ideas …), entries with fields and sub-entries. A page in the sidebar;
its functions serve models (as tools) and Relay scripts alike."""

from __future__ import annotations

from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.aac.api import Card, Context, new_id, register

VERSION = "1.2.0"  # this file's cards (a newer file replaces an older one in <config>/aac/cards)

ARG_LIST = {"type": "string", "help": "The list's name.", "required": True}


def _find(items: list[dict[str, Any]], key: str) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """An entry by id or text (case does not matter), anywhere in the tree: (its siblings, it)."""
    low = key.strip().lower()
    for it in items:
        if it["id"] == key or it["text"].strip().lower() == low:
            return items, it
        if found := _find(it.get("children") or [], key):
            return found
    return None


def _plain(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"text": it["text"], "done": it.get("done", False), **({"fields": it["fields"]} if it.get("fields") else {}),
             **({"children": _plain(it["children"])} if it.get("children") else {})} for it in items]


@register
class Lists(Card):
    """Lists of anything."""

    type = "lists"
    title = "Lists"
    category = "Data"
    help = "Lists of anything (shopping, projects, ideas), with a page in the sidebar; a model or a Relay's script uses its functions."
    ui = True
    keeps_data = True
    values = {"lists": "the names of its lists", "open": "open entries per list (name → count)", "total": "open entries in all lists"}
    functions = {
        "lists": {"help": "The lists and how many entries each has."},
        "read_list": {"help": "A list with its entries (fields, sub-entries).", "args": {"list": ARG_LIST}},
        "create_list": {"help": "A new, empty list.", "args": {"list": ARG_LIST}},
        "add_items": {"help": "Add entries to a list (under an entry: give 'under').",
                      "args": {"list": ARG_LIST, "items": {"type": "array", "help": "The entries' texts.", "required": True},
                               "under": {"type": "string", "help": "An entry's text: they become its sub-entries."}}},
        "update_item": {"help": "Change an entry: its text, done, or a field.",
                        "args": {"list": ARG_LIST, "item": {"type": "string", "help": "The entry's text or id.", "required": True},
                                 "text": {"type": "string", "help": "New text."}, "done": {"type": "boolean", "help": "Ticked?"},
                                 "field": {"type": "string", "help": "A field's name (e.g. status)."},
                                 "value": {"type": "string", "help": "The field's new value (empty removes it)."}}},
        "remove_items": {"help": "Take entries out (with their sub-entries).",
                         "args": {"list": ARG_LIST, "items": {"type": "array", "help": "The entries' texts or ids.", "required": True}}},
        "delete_list": {"help": "Delete a whole list.", "args": {"list": ARG_LIST}},
        "undo_remove": {"help": "Bring back what was removed last."},
    }

    def current(self) -> dict[str, Any]:
        def count(items): return sum((0 if i.get("done") else 1) + count(i.get("children") or []) for i in items)
        open_ = {li["name"]: count(li["items"]) for li in self.data.get("lists") or []}
        return {"lists": list(open_), "open": open_, "total": sum(open_.values())}

    def dated(self) -> list[dict[str, Any]]:
        """Open entries with a date (a field when, date or due): what a Calendar card shows (any card may offer dated())."""
        def walk(items):
            for it in items:
                yield it
                yield from walk(it.get("children") or [])
        return [{"uid": f"list:{it['id']}", "summary": f"{it['text']} ({li['name']})", "start": value}
                for li in self.data.get("lists") or [] for it in walk(li.get("items") or [])
                if not it.get("done") and (value := next((it.get("fields", {}).get(f) for f in ("when", "date", "due")
                                                          if it.get("fields", {}).get(f)), None))]

    def default_data(self) -> dict[str, Any]:
        return {"lists": []}

    def nav(self) -> dict[str, Any]:
        return {"add": "New list", "entries": [{"id": li["name"], "title": li["name"]} for li in self.data.get("lists") or []]}

    def _list(self, name: str, create: bool = False) -> dict[str, Any]:
        low = str(name).strip().lower()
        found = next((li for li in self.data["lists"] if li["name"].lower() == low or li["id"] == name), None)
        if found is None and create:
            now = dt_util.utcnow().isoformat()
            found = {"id": new_id(), "name": str(name).strip(), "view": "checklist", "created": now, "updated": now, "items": []}
            self.data["lists"].append(found)
        if found is None:
            raise ValueError(f"No list named {name}")
        return found

    @staticmethod
    def _item(text: str) -> dict[str, Any]:
        now = dt_util.utcnow().isoformat()
        return {"id": new_id(), "text": text.strip(), "done": False, "fields": {}, "created": now, "updated": now}

    async def call(self, function: str, args: dict[str, Any], context: Context | None = None) -> Any:
        if function == "lists":
            def count(items): return sum(1 + count(i.get("children") or []) for i in items)
            return [{"name": li["name"], "entries": count(li["items"])} for li in self.data["lists"]]
        if function == "read_list":
            li = self._list(args["list"])
            return {"name": li["name"], "items": _plain(li["items"])}
        if function == "create_list":
            li = self._list(args["list"], create=True)
        elif function == "delete_list":
            li = self._list(args["list"])
            self.data["lists"].remove(li)
            self.data["removed"] = {"list": li}
        elif function == "undo_remove":
            removed = self.data.pop("removed", None)
            if not removed:
                return {"error": "Nothing to bring back."}
            if "list" in removed:
                self.data["lists"].append(removed["list"])
            else:
                li = self._list(removed["from"])
                li["items"].extend(removed["items"])
        else:
            li = self._list(args["list"], create=function == "add_items")
            if function == "add_items":
                where = li["items"]
                if under := args.get("under"):
                    found = _find(li["items"], under)
                    if not found:
                        raise ValueError(f"No entry {under}")
                    where = found[1].setdefault("children", [])
                where.extend(self._item(str(t)) for t in args.get("items") or [] if str(t).strip())
            elif function == "update_item":
                found = _find(li["items"], str(args["item"]))
                if not found:
                    raise ValueError(f"No entry {args['item']}")
                it = found[1]
                if args.get("text"):
                    it["text"] = str(args["text"]).strip()
                if args.get("done") is not None:
                    it["done"] = bool(args["done"])
                if args.get("field"):
                    if str(args.get("value") or "").strip():
                        it.setdefault("fields", {})[args["field"]] = str(args["value"])
                    else:
                        it.get("fields", {}).pop(args["field"], None)
                it["updated"] = dt_util.utcnow().isoformat()
            elif function == "remove_items":
                gone = []
                for key in args.get("items") or []:
                    if found := _find(li["items"], str(key)):
                        found[0].remove(found[1])
                        gone.append(found[1])
                self.data["removed"] = {"from": li["name"], "items": gone}
            else:
                return await super().call(function, args, context)
            li["updated"] = dt_util.utcnow().isoformat()
        await self.save()
        self.core.notify("card", domain=self.domain.id, card=self.id)
        return {"done": function}
