"""Lists module: lists of anything, entries with sub-entries and free fields.

Provides the calendar capability: entries with a `when` field (date or date+time)
show up in the unified calendar, and new calendar entries without another home
land in a list called "Calendar".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import probatio
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from ...capabilities import CalendarProvider, in_period, parse_time
from ...const import DOMAIN
from ...runs import async_notify
from .. import ModuleContext
from .store import VIEWS, ListStore, walk

CALENDAR_LIST = "Calendar"


def _compact(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": entry["id"], "text": entry["text"]}
    if entry.get("done"):
        out["done"] = True
    if entry.get("fields"):
        out["fields"] = entry["fields"]
    if entry.get("children"):
        out["children"] = [_compact(c) for c in entry["children"]]
    return out


def _summary(lst: dict[str, Any]) -> dict[str, Any]:
    entries = [e for e, *_ in walk(lst["items"])]
    return {"id": lst["id"], "name": lst["name"], "view": lst["view"], "entries": len(entries),
            "open": sum(not e.get("done") for e in entries), "updated": lst["updated"]}


class _ListTool(llm.Tool):
    integration = DOMAIN

    def __init__(self, lists: ListStore, notify) -> None:
        self.lists = lists
        self.notify = notify

    def _list(self, ref: str) -> dict[str, Any]:
        if (lst := self.lists.get(ref)) is None:
            raise KeyError(f"No list '{ref}'. Existing: {', '.join(lst['name'] for lst in self.lists.lists) or 'none'}")
        return lst

    async def async_call(self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext):
        try:
            result = self.run(tool_input.tool_args)
        except KeyError as err:
            return llm.ToolResult(data={"error": str(err).strip("'\"")}, error=True)
        if not self.read_only:
            self.notify()
        return llm.ToolResult(data=result)

    read_only = False

    def run(self, args: dict) -> dict:
        raise NotImplementedError


class OverviewTool(_ListTool):
    name = "lists"
    description = "All lists with how many entries are open."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    read_only = True

    def run(self, args):
        return {"lists": [_summary(lst) for lst in self.lists.lists]}


class ReadTool(_ListTool):
    name = "read_list"
    description = "One list as a tree (entries with ids, done flags, fields and sub-entries)."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    parameters = probatio.Schema({probatio.Required("list", description="Name or id."): str,
                                  probatio.Optional("open_only", description="Skip done entries."): bool})
    read_only = True

    def run(self, args):
        lst = self._list(args["list"])
        items = [_compact(e) for e in lst["items"] if not (args.get("open_only") and e.get("done"))]
        return {"name": lst["name"], "view": lst["view"], "items": items}


_ITEM = probatio.Any(str, dict)


class CreateTool(_ListTool):
    name = "create_list"
    description = (
        "A new list, optionally with first entries. view: checklist (to tick off) or outline (notes, recipes). "
        "Entries: text or {text, fields, children}; nest sections, e.g. a recipe = [{text: 'Ingredients', "
        "children: [{text: 'Minced beef', fields: {qty: '500 g'}}, …]}, {text: 'Steps', children: [...]}]."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("name"): str, probatio.Optional("view"): probatio.In(list(VIEWS)),
                                  probatio.Optional("items"): [_ITEM]})

    def run(self, args):
        lst = self.lists.create(args["name"], args.get("view", "checklist"), args.get("items"))
        return _summary(lst)


class AddTool(_ListTool):
    name = "add_items"
    description = (
        "Add entries to a list (creates the list if it does not exist). Each entry is a text or "
        "{text, fields, children}; quantities and details into fields (e.g. {text: 'Kidney beans', "
        "fields: {qty: '2 cans'}}). parent = the id or text of an entry to add them below."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("list"): str, probatio.Required("items"): [_ITEM],
                                  probatio.Optional("parent"): str})

    def run(self, args):
        lst = self.lists.get(args["list"]) or self.lists.create(args["list"])
        added = self.lists.add(lst, args["items"], args.get("parent"))
        return {"list": lst["name"], "added": [_compact(e) for e in added]}


class UpdateTool(_ListTool):
    name = "update_item"
    description = (
        "Change one entry (by id or text): new text, done true/false, fields to merge (null removes a field), "
        "or move it below another entry (move_to = entry id/text, 'top' = top level)."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("list"): str, probatio.Required("item"): str,
                                  probatio.Optional("text"): str, probatio.Optional("done"): bool,
                                  probatio.Optional("fields"): dict, probatio.Optional("move_to"): str})

    def run(self, args):
        lst = self._list(args["list"])
        entry = self.lists.update(lst, args["item"], args.get("text"), args.get("done"), args.get("fields"))
        if args.get("move_to"):
            self.lists.move(lst, entry["id"], None if args["move_to"] == "top" else args["move_to"])
        return {"list": lst["name"], "entry": _compact(entry)}


class RemoveTool(_ListTool):
    name = "remove_items"
    description = "Remove entries (by id or text) with their sub-entries."
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=False)
    parameters = probatio.Schema({probatio.Required("list"): str, probatio.Required("items"): [str]})

    def run(self, args):
        lst = self._list(args["list"])
        return {"list": lst["name"], "removed": self.lists.remove(lst, args["items"])}


class DeleteTool(_ListTool):
    name = "delete_list"
    description = "Delete a whole list. Only after the user confirmed it (confirm=true)."
    annotations = llm.ToolAnnotations(read_only=False, destructive=True, open_world=False)
    parameters = probatio.Schema({probatio.Required("list"): str, probatio.Required("confirm"): bool})

    def run(self, args):
        lst = self._list(args["list"])
        if not args["confirm"]:
            return {"error": "Ask the user first, then call again with confirm=true."}
        self.lists.delete(lst)
        return {"deleted": lst["name"]}


@dataclass(kw_only=True)
class ListsAPI(llm.API):
    """The list tools of one domain."""

    lists: ListStore
    notify: Any

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        tools = [OverviewTool, ReadTool, CreateTool, AddTool, UpdateTool, RemoveTool, DeleteTool]
        return llm.APIInstance(api=self, api_prompt="",  # the guard puts in the manifest's (or the owner's) instructions
                               llm_context=llm_context,
                               tools=[t(self.lists, self.notify) for t in tools])


def _calendar_entries(lists: ListStore, start: datetime, end: datetime) -> list[dict[str, Any]]:
    found = []
    for lst in lists.lists:
        for entry, _, _, path in walk(lst["items"]):
            when = entry["fields"].get("when")
            if when and in_period(when, start, end):
                found.append({
                    "key": f"{lst['id']}/{entry['id']}",
                    "name": entry["text"], "start": parse_time(when).isoformat(),
                    "end": (parse_time(entry["fields"].get("end")) or None) and parse_time(entry["fields"]["end"]).isoformat(),
                    "all_day": len(str(when)) == 10, "done": entry.get("done", False),
                    "ref": {"module": "lists", "list": lst["id"], "item": entry["id"]},
                    "data": {k: v for k, v in entry["fields"].items() if k not in ("when", "end")},
                    "tags": [lst["name"], *path],
                })
    return found


async def async_setup_module(ctx: ModuleContext) -> None:
    """Load every domain's lists and hand out the tools, calendar and page."""
    stores: dict[str, ListStore] = {}
    for dom in ctx.domains.values():
        stores[dom.id] = ListStore(ctx.storage(dom, "lists"))
        await stores[dom.id].async_load()
    ctx.hass.data.setdefault(f"{DOMAIN}_lists", {})[ctx.entry.entry_id] = stores

    def notifier(domain_id: str):
        return lambda: async_notify(ctx.hass, "lists", domain=domain_id)

    ctx.add_llm_api(lambda dom, api_id, name: ListsAPI(
        hass=ctx.hass, id=api_id, name=name, lists=stores[dom.id], notify=notifier(dom.id)))

    async def query(domain_id: str, start: datetime, end: datetime):
        if domain_id not in stores or not ctx.setting(domain_id, "in_calendar"):
            return []
        return _calendar_entries(stores[domain_id], start, end)

    async def create(domain_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        lists = stores[domain_id]
        lst = lists.get(CALENDAR_LIST) or lists.create(CALENDAR_LIST, "outline")
        fields = {"when": entry.get("start"), "end": entry.get("end"), **(entry.get("data") or {})}
        added = lists.add(lst, [{"text": entry["name"], "fields": fields}])[0]
        notifier(domain_id)()
        return {"name": added["text"], **added["fields"], "list": lst["name"], "uid": f"lists:{lst['id']}/{added['id']}"}

    def _find(domain_id: str, key: str):
        list_id, _, item_id = key.partition("/")
        lists = stores[domain_id]
        lst = lists.get(list_id)
        if lst is None or lists.find(lst, item_id) is None:
            raise LookupError("That list entry no longer exists")
        return lists, lst, item_id

    async def update(domain_id: str, key: str, changes: dict[str, Any], recurrence_id: str | None = None) -> dict[str, Any]:
        lists, lst, item_id = _find(domain_id, key)
        fields = {}
        if "start" in changes:
            fields["when"] = changes["start"][:10] if changes.get("all_day") else changes["start"]
        if "end" in changes:
            fields["end"] = changes["end"]
        if "notes" in changes:
            fields["notes"] = changes["notes"]
        entry = lists.update(lst, item_id, changes.get("name"), None, fields)
        notifier(domain_id)()
        return {"name": entry["text"], **entry["fields"]}

    async def delete(domain_id: str, key: str, recurrence_id: str | None = None) -> bool:
        # Off the calendar = without a date; only the "Calendar" list's entries go entirely
        lists, lst, item_id = _find(domain_id, key)
        if lst["name"] == CALENDAR_LIST:
            lists.remove(lst, [item_id])
        else:
            lists.update(lst, item_id, fields={"when": None, "end": None})
        notifier(domain_id)()
        return True

    ctx.add_calendar(CalendarProvider("lists", "Lists (entries with a date)", query, create, update, delete))
    ctx.add_ws_commands(ws_lists, ws_lists_op)
    ctx.add_panel_page("lists", "Lists", "aac-lists-page", "page.js")


def _stores(hass: HomeAssistant, connection, msg) -> ListStore | None:
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        connection.send_error(msg["id"], "not_loaded", "AAC is not loaded")
        return None
    entry = entries[0]
    dom = entry.runtime_data.domain(msg.get("domain")) or entry.runtime_data.domain()
    return hass.data[f"{DOMAIN}_lists"][entry.entry_id][dom.id]


@websocket_api.websocket_command({vol.Required("type"): "aac/lists", vol.Optional("domain"): vol.Any(str, None)})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_lists(hass: HomeAssistant, connection, msg) -> None:
    """All lists of a domain, with their entries."""
    if (lists := _stores(hass, connection, msg)) is not None:
        connection.send_result(msg["id"], {"lists": lists.lists})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "aac/lists/op",
        vol.Optional("domain"): vol.Any(str, None),
        vol.Required("op"): vol.In(["create", "rename", "delete", "add", "update", "move", "remove"]),
        vol.Optional("list"): str,
        vol.Optional("name"): str,
        vol.Optional("view"): vol.In(list(VIEWS)),
        vol.Optional("item"): str,
        vol.Optional("parent"): vol.Any(str, None),
        vol.Optional("index"): int,
        vol.Optional("text"): str,
        vol.Optional("done"): bool,
        vol.Optional("fields"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_lists_op(hass: HomeAssistant, connection, msg) -> None:
    """One change from the panel."""
    if (lists := _stores(hass, connection, msg)) is None:
        return
    op = msg["op"]
    try:
        if op == "create":
            lists.create(msg.get("name") or "New list", msg.get("view", "checklist"))
        else:
            if (lst := lists.get(msg.get("list", ""))) is None:
                raise KeyError("No such list")
            if op == "rename":
                lists.rename(lst, msg.get("name", ""), msg.get("view"))
            elif op == "delete":
                lists.delete(lst)
            elif op == "add":
                lists.add(lst, [msg.get("text") or "…"], msg.get("parent"))
            elif op == "update":
                lists.update(lst, msg["item"], msg.get("text"), msg.get("done"), msg.get("fields"))
            elif op == "move":
                lists.move(lst, msg["item"], msg.get("parent"), msg.get("index"))
            elif op == "remove":
                lists.remove(lst, [msg["item"]])
    except KeyError as err:
        connection.send_error(msg["id"], "not_found", str(err).strip("'\""))
        return
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    dom = entries[0].runtime_data.domain(msg.get("domain")) or entries[0].runtime_data.domain()
    async_notify(hass, "lists", domain=dom.id)
    connection.send_result(msg["id"], {"lists": lists.lists})
