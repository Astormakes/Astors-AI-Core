# Trigger — partial

What wakes an agent without the owner asking. Two kinds are built (heartbeat,
HA state change); the general format below is what they become.

## General format (contract)

```json
{
  "id": "t_3f2a91c0",
  "name": "Front door",
  "enabled": true,
  "when": {"type": "state", "entity_id": "binary_sensor.front_door", "to": "on"},
  "do": {"template": "event-reaction", "instruction": "If nobody is home, tell me."},
  "cooldown": 300,
  "quiet_hours": null
}
```

| `when.type` | Fields | Status |
|---|---|---|
| `heartbeat` | `interval` (minutes) | built (one global heartbeat) |
| `state` | `entity_id`, optional `to` (fires only when it becomes this state) | built |
| `time` | `at` ("07:30"), optional `weekdays` | planned |
| `event` | `event_type` (an [Event](event.md) type), optional `match` (field → value) | planned |

`do` says what happens:
- `template`: which [agent template](agent-template.md) runs (built: the heartbeat
  and event reactions are fixed templates in code, `heartbeat` / `event-reaction`)
- `instruction`: the owner's words for this trigger, given to the agent
- later: `"rule": {...}` for instant reactions that need no model

Common rules:
- `cooldown` (seconds): after firing, the same trigger stays quiet that long.
- `quiet_hours` (`{"start": "22:00", "end": "08:00"}`, local time, may wrap midnight):
  automatic runs are skipped inside. Built for the heartbeat.
- A trigger never fires while a run with the same trigger is still running.
- Only `state` changes count, not attribute-only updates.
- Whatever the agent decides to say goes to the "From AAC" chat; silence is normal.

## What is stored today

Heartbeat (`aac.config` → `heartbeat`):
`{"enabled", "interval", "quiet_start", "quiet_end", "instructions" ("" = built-in default), "tools"}`

Event triggers (`aac.config` → `triggers`):
`{"id", "name", "entity_id", "to", "instruction", "cooldown", "enabled"}`

Mapping to the contract: `entity_id`/`to` → `when`, `instruction` → `do.instruction`,
template is implicitly `event-reaction`. A migration to the general format comes
with modules.

Code: `custom_components/aac/triggers.py`.
