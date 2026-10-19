# Calendar capability — built (v1)

One calendar over everything, without the core owning calendar data (owner design 2026-10-19).
The core knows the **shape** of an entry and keeps a **registry of providers**; each entry stays
in the module it comes from. Asking collects from all providers at that moment, so nothing is
copied and removing a module removes its entries. Repetition is the provider's job: it delivers
the occurrences of the requested period.

## Entry

Only `name` is required; everything else is optional and open.

```json
{"name": "Dentist", "start": "2026-10-23T09:00:00+02:00", "end": "2026-10-23T10:00:00+02:00",
 "all_day": false, "source": "lists", "ref": {"module": "lists", "list": "…", "item": "…"},
 "links": [{"kind": "memory", "id": "…"}, {"kind": "url", "url": "…"}],
 "data": {"location": "…", "anything": "…"}, "tags": ["appointment"], "done": false}
```

## Provider

`CalendarProvider(id, title, query(domain_id, start, end) → [entries], create(domain_id, entry) → entry | None)`,
registered by a module with `ctx.add_calendar(...)`. A provider that fails is skipped; the others still answer.
Where new entries go is the owner's choice (calendar module setting); without it the first writer is used.

| Provider | Entries |
|---|---|
| `core` | AAC's own plans and the owner's time triggers (repeating ones once per occurrence) |
| `lists` (module) | list entries with `fields.when` (date or date+time, optional `fields.end`); takes new entries into a list "Calendar" |
| `calendar` (module) | Home Assistant's calendars (`calendar.*`: Google, CalDAV, Local Calendar, …) through HA's `get_events` / `create_event` actions |

## Who uses it

- Agents: planning tools `calendar(from, to)` and `add_to_calendar(name, start?, end?, all_day?, notes?)`.
- Automatic runs: the next 24 h appear under "open loops".
- Panel: the calendar module's agenda page.

Code: `custom_components/aac/capabilities.py`, `modules/lists`, `modules/calendar`.
