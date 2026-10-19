# Calendar capability — built (v1)

One calendar over everything, without the core owning calendar data (owner design 2026-10-19).
The core knows the **shape** of an entry and keeps a **registry of providers**; each entry stays
in the module it comes from. Asking collects from all providers at that moment, so nothing is
copied and removing a module removes its entries. Repetition is the provider's job: it delivers
the occurrences of the requested period.

## Entry

Only `name` is required; everything else is optional and open.

```json
{"name": "Dentist", "uid": "lists:8f3a/2c1d", "start": "2026-10-23T09:00:00+02:00", "end": "2026-10-23T10:00:00+02:00",
 "all_day": false, "source": "lists", "recurrence_id": null,
 "links": [{"kind": "memory", "id": "…"}, {"kind": "url", "url": "…"}],
 "data": {"location": "…", "anything": "…"}, "tags": ["appointment"], "done": false}
```

**uid** (like HA / iCalendar): `"<source>:<key>"`, the key is the provider's own (lists: `<list>/<entry>`,
core: `<trigger>`, calendar: `<entity_id>/<event uid>`). A single occurrence of a repeating entry also has a
`recurrence_id` (its date). Changing and removing take only the uid (+ recurrence_id).

## Provider

`CalendarProvider(id, title, query, create?, update?, delete?)`, registered by a module with `ctx.add_calendar(...)`:

| Call | Meaning |
|---|---|
| `query(domain_id, start, end)` | entries of the period (each with a `key`) |
| `create(domain_id, entry)` | store a new entry (optional) |
| `update(domain_id, key, changes, recurrence_id)` | change name/start/end/all_day/notes in the entry's home (optional) |
| `delete(domain_id, key, recurrence_id)` | take it off the calendar in its home (optional) |

The interface offers the ways; whether a module supports them is up to the module (owner, 2026-10-19). Asking a
provider for something it does not offer gives a clear "cannot" instead of a guess. A provider that fails is
skipped; the others still answer. Where new entries go is the owner's choice (calendar module setting).
Every entry has exactly one home, so there is no syncing and no conflicting copies.

| Provider | Entries |
|---|---|
| `core` | AAC's own plans and the owner's time triggers (repeating ones once per occurrence); AAC may move/cancel its own plans, never the owner's triggers |
| `lists` (module) | list entries with `fields.when` (date or date+time, optional `fields.end`); takes new entries into a list "Calendar"; removing = the entry loses its date (except in "Calendar") |
| `calendar` (module) | Home Assistant's calendars (`calendar.*`: Google, CalDAV, Local Calendar, …) read and written through the calendar entities (HA's get_events action leaves out uids); create/change/remove where the calendar allows it. AAC's own calendar entities are skipped (they would ask AAC about itself in a loop) |

## AAC as a calendar in Home Assistant

One calendar entity per AAC domain (`calendar.aac_calendar`, `calendar.aac_<domain>_calendar`) with AAC's own
entries (not those of HA calendars, which HA shows already). HA's calendar page and the HA app can read, create
(→ the list "Calendar"), change and delete (→ the entry's home, by uid).

## Who uses it

- Agents: planning tools `calendar(from, to)`, `add_to_calendar(name, start?, end?, all_day?, notes?)`,
  `change_calendar_entry(uid, recurrence_id?, …)`, `remove_calendar_entry(uid, recurrence_id?)` (owner in the chat only).
- Automatic runs: the next 24 h appear under "open loops".
- Panel: the calendar module's agenda page.

Code: `custom_components/aac/capabilities.py`, `modules/lists`, `modules/calendar`.
