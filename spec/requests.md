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

## Entity access (the first kind)

AAC keeps its own list per domain (`entities` in the domain settings): `read` (sees the state) or `control`
(may also switch it); missing = hidden (AAC only knows the entity exists). HA's "expose to Assist" setting stays
HA's; until the owner chooses, AAC follows it, and the first change makes AAC's own list. Home tools (LLM API
`aac_home`): `list_entities`, `get_state`, `control` (locks, alarms, covers, valves, sirens only with the owner in
the chat), `request_access`. The home snapshot for automatic runs uses the same list. Note: if the master also
has HA's own Assist tools, those follow HA's exposure, not this list (the Entities page warns and offers to switch
them off).
