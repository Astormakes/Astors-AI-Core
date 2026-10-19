# Trigger — partial

What wakes an agent without the owner asking. Built: heartbeat, HA state
change, time. Stored in the format below since v0.6.0.

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
| `time` | `at` ("07:30", local), optional `weekdays` (0 = Monday … 6 = Sunday; empty = every day) | built |
| `event` | `event_type` (an [Event](event.md) type), optional `match` (field → value) | planned |

`do` says what happens:
- `template`: which [agent template](agent-template.md) runs (built: the heartbeat
  and event reactions are fixed templates in code, `heartbeat` / `event-reaction`)
- `instruction`: the owner's words for this trigger, given to the agent
- later: `"rule": {...}` for instant reactions that need no model

Common rules:
- `cooldown` (seconds): after firing, the same trigger stays quiet that long.
- `quiet_hours` (`{"start": "22:00", "end": "08:00"}`, local time, may wrap midnight):
  automatic runs are skipped inside. Built for the heartbeat and every trigger.
- A trigger never fires while a run with the same trigger is still running.
- Only `state` changes count, not attribute-only updates.
- Whatever the agent decides to say goes to the "From AAC" chat; silence is normal.
- `time` triggers usually *are* the message (a reminder): the agent is told to write
  it, and may stay silent only for "check whether …" instructions. `state` triggers
  get the plain "say nothing unless it matters" rule. (Tested: with the plain rule the
  8B answered "Nothing." to a reminder.)

## What is stored

Triggers (`aac.config` → `triggers`): exactly the format above. Flat state triggers
from 0.4/0.5 (`entity_id`, `to`, `instruction`, …) are converted on load.

Heartbeat (`aac.config` → `heartbeat`), still separate:
`{"enabled", "interval", "quiet_start", "quiet_end", "instructions" ("" = built-in default), "tools"}`

Code: `custom_components/aac/triggers.py`, `store.py`.
