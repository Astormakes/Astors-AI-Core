"""AAC's own tools for the home, within the owner's access list (entities.py).

list_entities shows everything that exists (name, room, type) but states only where
AAC may read; control switches only entities at "control" (sensitive ones such as
locks only with the owner in the chat); request_access files a request the owner
decides outside the chat (requests.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import probatio

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .const import DOMAIN
from .context import _state_text
from .entities import SENSITIVE_DOMAINS, async_set_access, domain_view, entities_of, info, integrations, level, visible_ids

if TYPE_CHECKING:
    from . import AACConfigEntry
    from .domains import Domain

API_ID = "aac_home"
API_PROMPT = (
    "You know only the entities the owner lets this domain see, and may read or switch only those allowed. "
    "home_overview shows every installed integration and how many of its entities you can see, so you can "
    "tell the user what exists even when it is invisible to you (never claim something is missing just "
    "because list_entities did not find it). To get more, ask: request_visibility for an integration's "
    "entities, request_access for reading/switching entities you can see, each with a short reason; the owner "
    "decides outside the chat and you hear the outcome in 'From AAC'. Ask only for what the task needs. "
    "The home changes during a chat: when asked to check again, use the tools again instead of repeating "
    "what you found earlier."
)


class _HomeTool(llm.Tool):
    integration = DOMAIN

    def __init__(self, entry: AACConfigEntry, domain: Domain) -> None:
        self._entry = entry
        self._domain = domain

    def _level(self, hass: HomeAssistant, entity_id: str) -> str:
        return level(hass, self._domain.settings, entity_id)


class ListEntitiesTool(_HomeTool):
    name = "list_entities"
    description = (
        "The entities you may know about: name, room, type, integration and your access (hidden = no read / read / "
        "control); the state only where you may read. Filter by room, type (light, sensor, …), integration "
        "(e.g. steam_online, from home_overview) or a search word (it also matches the integration)."
    )
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    parameters = probatio.Schema({probatio.Optional("area"): str, probatio.Optional("type"): str,
                                  probatio.Optional("integration"): str, probatio.Optional("search"): str,
                                  probatio.Optional("only_accessible"): bool})

    async def async_call(self, hass, tool_input, llm_context):
        args = tool_input.tool_args
        found = []
        for e in domain_view(hass, self._domain.settings):
            if args.get("only_accessible") and e["level"] == "hidden":
                continue
            if args.get("area") and (e["area"] or "").lower() != args["area"].lower():
                continue
            if args.get("type") and e["domain"] != args["type"] and e.get("device_class") != args["type"]:
                continue
            if args.get("integration") and args["integration"].lower() not in (
                (e.get("integration") or "").lower(), (e.get("integration_title") or "").lower()):
                continue
            haystack = f"{e['name']} {e['entity_id']} {e.get('integration') or ''} {e.get('integration_title') or ''}".lower()
            if (word := (args.get("search") or "").lower()) and word not in haystack:
                continue
            if e["level"] != "hidden" and (state := hass.states.get(e["entity_id"])):
                e = {**e, "state": _state_text(state)}
            found.append({k: v for k, v in e.items() if v not in (None, "")})
        return llm.ToolResult(data={"entities": found[:150], "more": max(0, len(found) - 150)})


class OverviewTool(_HomeTool):
    name = "home_overview"
    description = (
        "Everything installed in Home Assistant: each integration with its number of entities and how many of "
        "them you can see, plus totals. Use it before saying something does not exist."
    )
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)

    async def async_call(self, hass, tool_input, llm_context):
        found = integrations(hass, self._domain.settings)
        return llm.ToolResult(data={
            "entities_total": len(hass.states.async_entity_ids()),
            "entities_you_can_see": sum(1 for e in domain_view(hass, self._domain.settings)),
            "integrations": [{k: v for k, v in i.items()} for i in found],
        })


class RequestVisibilityTool(_HomeTool):
    name = "request_visibility"
    description = (
        "Ask the owner to let you see the entities of integrations (ids from home_overview), with a short reason; "
        "with_read=true asks to read their states too. The owner decides outside the chat."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("integrations"): [str], probatio.Required("reason"): str,
                                  probatio.Optional("with_read"): bool})

    async def async_call(self, hass, tool_input, llm_context):
        args = tool_input.tool_args
        known = {i["id"]: i for i in integrations(hass, self._domain.settings)}
        by_name = {i["integration"]: i for i in known.values()}
        items = []
        for ref in dict.fromkeys(args["integrations"][:20]):
            item = known.get(ref) or by_name.get(ref)
            if item and item["visible"] < item["entities"]:
                items.append({"id": item["id"], "label": item["title"],
                              "detail": f"{item['integration']} · {item['entities'] - item['visible']} entities you would add"})
        if not items:
            return llm.ToolResult(data={"note": "You can already see those (or they do not exist)."})
        request = await self._entry.runtime_data.requests.async_file(
            self._domain.id, "visibility", f"Let \"{self._domain.name}\" see {', '.join(i['label'] for i in items)}",
            args["reason"], items, {"with_read": bool(args.get("with_read"))},
        )
        return llm.ToolResult(data={"request": request["id"], "status": "waiting for the owner"})


class GetStateTool(_HomeTool):
    name = "get_state"
    description = "Current state and attributes of entities you may read."
    annotations = llm.ToolAnnotations(read_only=True, open_world=False)
    parameters = probatio.Schema({probatio.Required("entity_ids"): [str]})

    async def async_call(self, hass, tool_input, llm_context):
        out = {}
        for entity_id in tool_input.tool_args["entity_ids"][:30]:
            state = hass.states.get(entity_id)
            if state is None:
                out[entity_id] = {"error": "does not exist"}
            elif (lvl := self._level(hass, entity_id)) == "invisible":
                out[entity_id] = {"error": "does not exist"}
            elif lvl == "hidden":
                out[entity_id] = {"error": "no read access (request_access)"}
            else:
                attrs = {k: v for k, v in state.attributes.items() if k not in ("friendly_name", "icon", "entity_picture")}
                out[entity_id] = {"state": _state_text(state), "attributes": attrs, "changed": state.last_changed.isoformat()}
        return llm.ToolResult(data=out)


class ControlTool(_HomeTool):
    name = "control"
    description = (
        "Do something with an entity you may control: action = an HA action of its type (turn_on, turn_off, "
        "toggle, set_temperature, open_cover, set_value, …), data = its fields (brightness_pct, temperature, …)."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("entity_id"): str, probatio.Required("action"): str,
                                  probatio.Optional("data"): dict})

    async def async_call(self, hass, tool_input, llm_context):
        from .setup_api import owner_present  # noqa: PLC0415

        args = tool_input.tool_args
        entity_id, action = args["entity_id"], args["action"]
        if (state := hass.states.get(entity_id)) is None or self._level(hass, entity_id) == "invisible":
            return llm.ToolResult(data={"error": "That entity does not exist."}, error=True)
        if self._level(hass, entity_id) != "control":
            return llm.ToolResult(data={"error": "You may not control it; ask with request_access."}, error=True)
        if not hass.services.has_service(state.domain, action):
            return llm.ToolResult(data={"error": f"{state.domain} has no action '{action}'."}, error=True)
        if state.domain in SENSITIVE_DOMAINS and not await owner_present(
            hass, llm_context.context.user_id if llm_context.context else None
        ):
            return llm.ToolResult(data={"error": "That needs the owner in the chat (locks, alarms, doors)."}, error=True)
        try:
            await hass.services.async_call(state.domain, action, {**(args.get("data") or {}), "entity_id": entity_id},
                                           blocking=True, context=llm_context.context)
        except (HomeAssistantError, ValueError) as err:
            return llm.ToolResult(data={"error": str(err)}, error=True)
        new = hass.states.get(entity_id)
        return llm.ToolResult(data={"entity_id": entity_id, "state": _state_text(new) if new else None})


class RequestAccessTool(_HomeTool):
    name = "request_access"
    description = (
        "Ask the owner for access to entities (level read = see the state, control = also switch it), with a short "
        "reason. The owner decides outside the chat; you hear the outcome in 'From AAC'. Tell the user you asked."
    )
    annotations = llm.ToolAnnotations(read_only=False, destructive=False, open_world=False)
    parameters = probatio.Schema({probatio.Required("entity_ids"): [str], probatio.Required("level"): probatio.In(["read", "control"]),
                                  probatio.Required("reason"): str})

    async def async_call(self, hass, tool_input, llm_context):
        args = tool_input.tool_args
        rank = {"hidden": 0, "read": 1, "control": 2}
        items = []
        for entity_id in dict.fromkeys(args["entity_ids"][:50]):
            if (state := hass.states.get(entity_id)) is None or self._level(hass, entity_id) == "invisible":
                continue  # it may only ask about what its domain can see
            if rank[self._level(hass, entity_id)] >= rank[args["level"]]:
                continue  # already allowed
            e = info(hass, state)
            items.append({"id": entity_id, "label": e["name"], "detail": " · ".join(x for x in (e["area"], entity_id) if x)})
        if not items:
            return llm.ToolResult(data={"note": "You already have that access (or the entities do not exist)."})
        verb = "see" if args["level"] == "read" else "control"
        request = await self._entry.runtime_data.requests.async_file(
            self._domain.id, "entities", f"Let AAC {verb} {len(items)} entit{'y' if len(items) == 1 else 'ies'}",
            args["reason"], items, {"level": args["level"]},
        )
        return llm.ToolResult(data={"request": request["id"], "status": "waiting for the owner", "entities": [i["id"] for i in items]})


async def apply_visibility(entry: AACConfigEntry, domain_id: str, request: dict[str, Any], approved: list[str]) -> None:
    """An approved visibility request: tick those integrations' entities for the domain (and read, if asked)."""
    if dom := entry.runtime_data.domain(domain_id):
        hass = dom.chats.hass
        new = entities_of(hass, approved)
        await dom.settings.async_set_visible([*visible_ids(hass, dom.settings), *new])
        if request["payload"].get("with_read"):
            await async_set_access(hass, entry.runtime_data.store, {e: {"read": True} for e in new})


async def apply_entities(entry: AACConfigEntry, domain_id: str, request: dict[str, Any], approved: list[str]) -> None:
    """An approved entity request: raise those entities to the requested level."""
    if dom := entry.runtime_data.domain(domain_id):
        write = request["payload"]["level"] == "control"
        await async_set_access(dom.chats.hass, entry.runtime_data.store, {e: {"read": True, **({"write": True} if write else {})} for e in approved})


@dataclass(kw_only=True)
class AACHomeAPI(llm.API):
    """The home tools of one domain."""

    entry: AACConfigEntry
    domain: Domain

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        tools = [OverviewTool, ListEntitiesTool, GetStateTool, ControlTool, RequestAccessTool, RequestVisibilityTool]
        return llm.APIInstance(api=self, api_prompt=API_PROMPT, llm_context=llm_context,
                               tools=[t(self.entry, self.domain) for t in tools])
