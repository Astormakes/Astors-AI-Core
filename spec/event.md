# Event — partial

Everything that happens is an event on one bus. Modules and agents talk only
through events (and tools). Continuous audio/video are not events; only what
their pipelines detect becomes one.

```json
{
  "id": "e_7c1d0a",
  "type": "ha.state_changed",
  "time": "2026-10-19T07:12:03Z",
  "source": "ha",
  "data": {"entity_id": "binary_sensor.front_door", "old": "off", "new": "on"},
  "privacy": "local-only"
}
```

| Type | data | Status |
|---|---|---|
| `ha.state_changed` | entity_id, old, new | built (HA's own event, used by state triggers) |
| `aac.run` | the [run](run.md) (started / finished) | built (pushed to the panel) |
| `aac.chat` | chat_id (a message was added) | built (pushed to the panel) |
| `aac.chats` | — (list changed) | built |
| `voice.heard` | text, speaker, room, confidence | planned (audio pipeline) |
| `vision.seen` | what, who, source, confidence | planned |
| `presence.changed` | person, room | planned |
| `node.free` / `node.busy` | node id | planned (pool) |

Rules:
- `type` is `<area>.<what>`; modules use their module id as area (`shopping.list_changed`).
- Events are facts about the past; they are never commands. Asking for something is a tool call.
- Raw audio/video never travel as events (volatile by design).

Today the panel receives `aac.*` events through the WebSocket subscription
`aac/subscribe` as `{"what": "run" | "chat" | "chats", …}`.

Code: `custom_components/aac/runs.py` (`async_notify`), `websocket.py` (`aac/subscribe`).
