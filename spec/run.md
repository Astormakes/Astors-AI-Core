# Run — built

One piece of agent work: a chat answer, a heartbeat, a reaction to an event.
Every run is recorded, so the owner can always see what was done, why, and
with what information (the audit log).

```json
{
  "id": "49252d4aca0d",
  "kind": "auto",
  "trigger": "heartbeat",
  "title": "Heartbeat",
  "status": "silent",
  "started": "2026-10-18T21:36:51.204Z",
  "finished": "2026-10-18T21:37:02.880Z",
  "input": "Check-in: anything I should know right now?",
  "context": "Heartbeat: a scheduled check-in …\nCurrent state of the home …",
  "result": "NOTHING",
  "chat_id": null,
  "allow_tools": false,
  "steps": [
    {"type": "thinking", "text": "The battery sensor reads 12 %, that may be worth a look."},
    {"type": "say", "text": "Let me look at the battery."},
    {"type": "tool_call", "tool": "assist__homeassistant__GetLiveContext", "args": {"area": "Battery"}},
    {"type": "tool_result", "tool": "assist__homeassistant__GetLiveContext", "error": false, "data": "{…}"}
  ]
}
```

What the model got in a run's last call (system prompt, messages, tools offered, token estimate) is kept
apart for the newest 40 runs (`aac.requests`, system prompts stored once by hash); `request: true` on the run
says it is there; `aac/run/request {run_id}` returns it.

Step types: `say` (what the model wrote for the reader before calling tools; shown in the answer between
the steps), `thinking` (its hidden reasoning, folded), `tool_call`, `tool_result`, `note` (AAC's own remark:
context full, model switched, decision). Runs before v0.38 have `text` for both say and note.

| Field | Meaning |
|---|---|
| `kind` | `chat` (the owner asked) or `auto` (AAC started it: trigger, heartbeat) |
| `trigger` | what started it: `chat`, `heartbeat`, `event: <trigger id>`, later `template: <id>` |
| `status` | `running` → `done` (answered / spoke up) · `silent` (decided not to speak) · `failed` |
| `input` | the message the agent got |
| `context` | everything added on top of its instructions (the [context recipe](context-recipe.md) output, offered memories) |
| `result` | the final answer, also kept when silent (so it can be checked) |
| `chat_id` | where the result was posted, if anywhere (`aac` = the "From AAC" chat, `hb_…` = a heartbeat finding's own chat) |
| `allow_tools` | `false` = the agent could only look at its context, not act |
| `steps` | in order: `text` (the model's words next to tool calls), `tool_call`, `tool_result` (`data` truncated to 500 chars) |

Rules:
- A run that was still `running` when HA restarted is loaded as `failed` ("interrupted by a restart").
- The last 100 runs are kept (`.storage/aac.runs`).
- Future: `parent` (the run that spawned this one, for sub-agents), `budget` (tokens/time used), `model` (which model answered).

Code: `custom_components/aac/runs.py`, `broker.py`.
