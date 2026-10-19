# Context recipe — partial

What an agent gets to see before it starts. The model does not need a huge
context window if each call gets exactly the right excerpt (framework, section 2).
Each [trigger](trigger.md) or [template](agent-template.md) names a recipe.

```json
{
  "id": "heartbeat",
  "parts": [
    {"type": "intro", "text": "Heartbeat: a scheduled check-in, nobody asked you anything."},
    {"type": "time"},
    {"type": "home_snapshot", "max_entities": 80},
    {"type": "instructions"}
  ],
  "memories": {"limit": 3, "min_score": 0.5, "placement": "user"}
}
```

## Parts

| `type` | Produces | Status |
|---|---|---|
| `intro` / `text` | fixed text | built (in code) |
| `time` | local weekday, date, time | built |
| `home_snapshot` | one line per entity exposed to assistants: `area \| name \| state`; binary sensors translated by device class (`smoke` on → `DETECTED`, `door` on → `OPEN`, `moisture` on → `WET`, …) | built |
| `instructions` | the owner's instructions for this trigger/template | built |
| `memories` | best matches from [memory](memory.md) for the input | built (for every master call) |
| `trigger_event` | what changed (entity, old → new state) | built (as the run input) |
| `calendar` | today's appointments | planned |
| `transcript` | last N minutes of the transcript buffer, per room | planned |
| `open_loops` | due goals, reminders, open questions | planned |

## Placement (important for speed)

- Stable text goes into the **system prompt**: model servers cache the prompt prefix,
  so an unchanged system prompt costs almost nothing on the next call.
- Things that change every call (offered memories) go into the **user message**
  (`"placement": "user"`), so they don't break the cache. On slow GPUs a cache miss
  costs tens of seconds.

## Lessons that are part of the contract (tested)

- Put the snapshot **before** the rules, state the "say nothing" rule **once**.
  Repeating it made two different models swallow real alarms.
- Say what states mean. `on` for a smoke detector was ignored; `DETECTED` was not.
- Give small models the information instead of tools to find it: they tend to
  call a tool for every room even when the answer is already in front of them.

Code: `custom_components/aac/context.py`, `triggers.py`, `conversation.py`.
