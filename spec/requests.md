# Requests — built (v1)

Things AAC may only do with the owner's OK. AAC files a request with a reason; it waits **outside the chat**
(panel: WORKERS → Requests with a badge, plus an HA notification) until the owner allows all, some or none
of its items. Only then is it applied, by the handler of its kind, and AAC hears the outcome in "From AAC".

```json
{"id": "19d983d6f5", "domain": "assistant", "kind": "entities", "title": "Let AAC see 1 entity",
 "reason": "To check the current state of charge of the home battery.",
 "items": [{"id": "sensor.battery_soc", "label": "Battery Soc", "detail": "Battery · sensor.battery_soc"}],
 "payload": {"level": "read"}, "status": "pending", "approved": [], "created": "…", "decided": null}
```

`status`: pending · approved · partly · rejected. Kinds are registered with `requests.register_kind(kind, apply)`
(apply(entry, domain_id, request, approved_item_ids)); modules can add their own.

| Kind | Filed by | Applied as |
|---|---|---|
| `entities` | home tool `request_access(entity_ids, level read/control, reason)` | the entities' access level in that domain |

WebSocket: `aac/requests`, `aac/request/decide` (`approve`: true, false or item ids).

## Entity access (the first kind), two layers (v0.22.0)

1. **What a domain can see at all** (`visible` in the domain settings; domain menu → "What … can see", a tick
   list with tick all / per room / single, saved with Save). Anything else does not exist for that domain, its
   agent, its automatic runs and its modules. New domains see nothing.
2. **Read and write per entity**, for every domain and module (`entity_access` in the config; SETTINGS → Entities,
   lit Read / Write toggles, per-room bulk). Write needs read.

Per domain the effective level is where both meet: invisible · hidden (seen, no read) · read · control. HA's own
"expose to Assist" setting stays HA's; until the owner chooses, AAC starts from it (migration and defaults read
HA's exposure settings, not states, which are incomplete during startup). Home tools (LLM API `aac_home`):
`list_entities` (only visible ones), `get_state`, `control` (locks, alarms, covers, valves, sirens only with the
owner in the chat), `request_access` (only for visible entities; approval sets read/write). The home snapshot for
automatic runs uses the same levels. If the master also has HA's own Assist tools, those follow HA's exposure,
not these lists (the Entities page warns and offers to switch them off).
