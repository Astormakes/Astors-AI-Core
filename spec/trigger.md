# Trigger — partial

What wakes an agent without the owner asking. Built: HA state change (or one attribute), HA event, time.
Stored in the format below since v0.6.0. Since 0.45 the triggers are a module (`modules/triggers`): a node
on the [stream bus](streams.md) that writes `broker.request`; the heartbeat is a module of its own (0.44).

## General format (contract)

```json
{
  "id": "t_3f2a91c0",
  "name": "Front door",
  "enabled": true,
  "when": {"type": "state", "entity_id": "binary_sensor.front_door", "to": "on"},
  "do": {"template": "event-reaction", "instruction": "If nobody is home, tell me."},
  "cooldown": 300,
  "quiet_hours": null,
  "created_by": "owner",
  "proposed": false
}
```

- `created_by`: `owner` or `agent` (AAC planned it itself). Agents can only change or
  delete their own triggers, never the owner's.
- `proposed`: an agent's plan waiting for the owner's approval (setting `own_plans` = `ask`); disabled until approved.

| `when.type` | Fields | Status |
|---|---|---|
| `heartbeat` | — | moved: since 0.44 the heartbeat is a module (`modules/heartbeat`, a node on the [stream bus](streams.md)) |
| `state` | `entity_id`, optional `to` (fires only when it becomes this state) | built |
| `time` | `at` ("07:30", local), optional `weekdays` (0 = Monday … 6 = Sunday; empty = every day) or `date` ("2026-10-24": once, removed after firing) | built |
| `event` | `event_type` (an HA event type), optional `event_data` (field → value) | built (0.24) |

`do` says what happens:
- `template`: which [agent template](agent-template.md) runs (built: the heartbeat
  and event reactions are fixed templates in code, `heartbeat` / `event-reaction`)
- `instruction`: the owner's words for this trigger, given to the agent
- later: `"rule": {...}` for instant reactions that need no model

Common rules:
- `cooldown` (seconds): after firing, the same trigger stays quiet that long.
- `quiet_hours` (`{"start": "22:00", "end": "08:00"}`, local time, may wrap midnight):
  automatic runs are skipped inside. Built for every trigger (and the heartbeat module).
- A trigger never fires while a run of the same trigger waits or runs (`coalesce`).
- A fired trigger is a `broker.request`: time triggers are `high`, time-critical (`critical`: without a model
  before `due_minutes`, default 30, the instruction reaches the owner as a plain notification), event triggers
  `normal` and expire after 15 min. Both wait across a restart (`keep`); `do` may override `priority`,
  `critical`, `due_minutes`, `tier`.
- Only `state` changes count, not attribute-only updates.
- Whatever the agent decides to say goes to the "From AAC" chat (a heartbeat finding opens a chat of its own,
  `hb_<utc time>`, "Heartbeat · …"; see `broker.request` → `chat: "own"` in [streams.md](streams.md)). Silence is
  normal (the run list shows it).
- `time` triggers usually *are* the message (a reminder): the agent is told to write
  it, and may stay silent only for "check whether …" instructions. `state` triggers
  get the plain "say nothing unless it matters" rule. (Tested: with the plain rule the
  8B answered "Nothing." to a reminder.)

## Agents planning for themselves (built)

The master has `schedule` / `watch` / `list_schedules` / `cancel_schedule` (the triggers module's tools,
`aac-triggers__…`). The module setting `own_plans` decides (up to 0.44: `autonomy.schedule`, moved on load): `off` (tools not offered) · `ask` (proposal, the
owner approves in the panel) · `allowed` (active at once). Every plan or cancellation is
posted to "From AAC". At most 20 own plans. Its plans are listed in the open loops of every automatic run
(`capabilities.add_open_loops`) and show in the calendar (provider `triggers`; AAC may move or cancel its own
plans there, never the owner's). Day names and groups are accepted
(`["weekdays"]`, `["mon","fri"]`, `"Mo-Fr"`), because small models get numbers wrong.

## What is stored

Triggers: the module's storage per domain, `.storage/aac.mod.triggers.triggers[.<domain>]` →
`{"triggers": [...]}`, exactly the format above. Up to 0.44 they were in `aac.config` → `domains[].triggers`;
the module moves them on its first start (then removes them from the config). Flat state triggers from
0.4/0.5 (`entity_id`, `to`, `instruction`, …) are converted then. A new domain starts without triggers.

Heartbeat: module settings (`aac.config` → `module_settings.<domain>.heartbeat`: `interval`, `quiet_start`,
`quiet_end`, `tier`, `tools`, `instructions` ("" = built-in)); on/off = the module's switch per domain. 0.43's
`domains[].heartbeat` is moved there on load.

Code: `modules/triggers/` (`__init__.py`: storage, listening, node, calendar, WebSocket `aac/triggers/info`,
`aac/trigger/save|delete|approve`; `plans.py`: AAC's planning tools; `page.js`), `broker.py` (shared: context,
queue, automatic run), `modules/heartbeat/`. The calendar tools (`calendar`, `add_to_calendar`,
`change_calendar_entry`, `remove_calendar_entry`) moved to the calendar module in 0.45.

## More kinds of "when" (v0.24.0)

| when | Fires |
|---|---|
| `{"type": "state", "entity_id", "attribute"?, "to"?}` | the state changes, or with `attribute` that attribute (e.g. `game` on a Steam sensor); `to` = only to that value |
| `{"type": "event", "event_type", "event_data"?}` | an HA event of that type (e.g. `zha_event`, `mobile_app_notification_action`, `tag_scanned`), filtered by data values |

AAC can set these up itself with the planning tool `watch` (same `own_plans` rules as `schedule`; only entities it
may read; noisy events like `state_changed` are refused; at least 60 s between reactions).

