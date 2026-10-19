# Hearing (contract 1)

What AAC's microphones hear is a shared interface, like the calendar: the core only hears,
modules decide what to do with it. Owner decision 2026-10-19.

## Core
- Sources (satellites, the HA app, later the browser), RAM ring buffers, voice activity, speech to text
  (the voice pool), the transcript (RAM, last lines), speaking (`async_say`, at the owner's volume) and
  answering through the master (`async_answer`).
- Every new transcript line is handed to each **hearing module that is on in the voice domain**.

## A heard line
```json
{"time": "2026-10-19T18:06:01+00:00", "source": "ha_app", "name": "Home Assistant", "text": "…",
 "language": "en", "confidence": null, "engine": "m_…", "addressed": false}
```
Only `time`, `source` and `text` are sure. Listeners may mark a line: `addressed: true` (it was meant for
AAC), `gate: "…"` / `note: "…"` (shown in the transcript). Planned: optional annotations from stage
modules (speaker, loudness, mood, media vs. live, room/position) — all optional, like calendar fields.

## A hearing module
```python
ctx.add_hearing(Hearing(id="wakeword", title="Wake word", heard=heard))

async def heard(source, line, earlier):  # earlier: the source's previous lines, oldest first
    ...
```
Helpers on `ctx.audio`: `mentions(text)` (one of AAC's names), `async_attention(text, earlier_texts)`
(quick "is someone talking TO AAC?" on the fast tier), `async_answer(source, text, before)`,
`async_say(source, text)`, `changed()` (the panel shows a marked line).

A manifest may say `"enabled_by_default": false`: the owner switches it on per domain.

## Built-in modules
- **Wake word** (`wakeword`, off by default): a name or a follow-up → attention check → the master
  answers by voice, with the lines of the last `look_back` seconds. Settings: follow_up, attention, look_back.
- **Companion** (planned): follows the room and speaks up on its own (decision tiers, interjection gate).
